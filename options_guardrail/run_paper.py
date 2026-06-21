"""
End-to-end demo: mock strategist output -> guardrail -> (paper) executor.

By default this runs DRY (no IBKR connection) so you can see the guardrail
decisions on a batch of plans. Pass --live-paper to actually connect to IB
Gateway/TWS running in PAPER mode.

    python3 run_paper.py              # dry run, prints decisions
    python3 run_paper.py --live-paper # submit approved plans to IBKR paper
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from schema import TradePlan
from state import AccountState
from guardrail import Guardrail
from risk_policy import ACTIVE_POLICY

STATE_PATH = Path(__file__).parent / "account_state.json"


# A few sample plans an Opus strategist might emit overnight. The last two are
# deliberately bad to show the guardrail rejecting them.
SAMPLE_PLANS = [
    {
        "plan_id": "2026-06-01-SPY-1",
        "symbol": "SPY",
        "structure": "debit_call_spread",
        "regime": "trend",
        "legs": [
            {"symbol": "SPY", "expiry": "2026-06-19", "strike": 535, "right": "C", "side": "BUY"},
            {"symbol": "SPY", "expiry": "2026-06-19", "strike": 540, "right": "C", "side": "SELL"},
        ],
        "thesis": "Overnight ES held VWAP; econ calendar light. Continuation to 540.",
        "net_price": 2.10,
        "max_loss_usd": 4200.0,     # 20 spreads * $210 debit
        "target_profit_usd": 5800.0,
        "requested_qty": 20,
        "invalidation": {"kind": "underlying_below", "value": 531.0},
    },
    {
        "plan_id": "2026-06-01-QQQ-1",
        "symbol": "QQQ",
        "structure": "credit_put_spread",
        "regime": "low_iv_grind",
        "legs": [
            {"symbol": "QQQ", "expiry": "2026-06-19", "strike": 440, "right": "P", "side": "SELL"},
            {"symbol": "QQQ", "expiry": "2026-06-19", "strike": 435, "right": "P", "side": "BUY"},
        ],
        "thesis": "IV rank low, support at 442. Sell premium below.",
        "net_price": -1.40,
        "max_loss_usd": 3600.0,     # 10 spreads * (5.00-1.40)*100
        "target_profit_usd": 1400.0,
        "requested_qty": 10,
        "invalidation": {"kind": "underlying_below", "value": 438.0},
    },
    {
        # BAD: undefined risk -> must be rejected
        "plan_id": "2026-06-01-TSLA-naked",
        "symbol": "TSLA",
        "structure": "naked_put",
        "legs": [
            {"symbol": "TSLA", "expiry": "2026-06-19", "strike": 300, "right": "P", "side": "SELL"},
        ],
        "thesis": "Wheel it.",
        "max_loss_usd": 5000.0,
        "requested_qty": 1,
        "invalidation": {"kind": "underlying_below", "value": 290.0},
    },
    {
        # BAD: no invalidation -> must be rejected
        "plan_id": "2026-06-01-IWM-noinval",
        "symbol": "IWM",
        "structure": "debit_put_spread",
        "legs": [
            {"symbol": "IWM", "expiry": "2026-06-19", "strike": 200, "right": "P", "side": "BUY"},
            {"symbol": "IWM", "expiry": "2026-06-19", "strike": 195, "right": "P", "side": "SELL"},
        ],
        "thesis": "Feels toppy.",
        "max_loss_usd": 1500.0,
        "requested_qty": 5,
        "invalidation": None,
    },
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live-paper", action="store_true",
                    help="connect to IB Gateway/TWS paper and submit approved plans")
    ap.add_argument("--equity", type=float, default=100_000.0)
    args = ap.parse_args()

    state = AccountState.load(STATE_PATH, default_equity=args.equity)
    state.equity = args.equity  # demo: pin to requested equity
    state.day_anchor_equity = state.day_anchor_equity or args.equity
    guard = Guardrail(ACTIVE_POLICY)

    print(f"Policy: {ACTIVE_POLICY.name}  |  equity ${state.equity:,.0f}  |  "
          f"day P&L {state.day_drawdown_pct:+.2%}  week P&L {state.week_drawdown_pct:+.2%}")
    print(f"Per-trade cap: ${state.equity * ACTIVE_POLICY.max_loss_per_trade_pct:,.0f} "
          f"({ACTIVE_POLICY.max_loss_per_trade_pct:.0%})   "
          f"daily halt -{ACTIVE_POLICY.daily_halt_pct:.0%}  "
          f"weekly halt -{ACTIVE_POLICY.weekly_halt_pct:.0%}")
    print("=" * 72)

    executor = None
    if args.live_paper:
        from ibkr_paper_executor import executor_from_env
        executor = executor_from_env().connect()
        print("Connected to IBKR paper.\n")

    approved = []
    for raw in SAMPLE_PLANS:
        try:
            plan = TradePlan.from_dict(raw)
        except Exception as e:
            print(f"[SCHEMA-REJECT] {raw.get('plan_id')}: {e}")
            continue

        res = guard.evaluate(plan, state)
        tag = res.decision.value
        print(f"[{tag:16}] {plan.plan_id:26} {plan.structure}")
        for r in res.reasons:
            print(f"                   - {r}")

        if res.tradeable:
            approved.append((plan, res))
            # reflect deployment in local state so portfolio caps accumulate
            state.open_positions += 1
            state.deployed_usd += res.per_unit_max_loss * res.approved_qty

    print("=" * 72)
    print(f"{len(approved)} of {len(SAMPLE_PLANS)} plans approved for paper execution.")

    if executor is not None:
        for plan, res in approved:
            rep = executor.execute(plan, res)
            print(f"  submit {plan.plan_id}: {rep.status} ({rep.detail})")
        executor.disconnect()

    state.save(STATE_PATH)


if __name__ == "__main__":
    main()
