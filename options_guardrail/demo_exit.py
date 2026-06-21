"""
Offline demo of the exit monitor over a simulated price/P&L path.

Opens two approved positions, then walks the market through several ticks so you
can watch the monitor close them on invalidation, stop, and take-profit — and
see realized P&L flow into equity and arm the kill-switch.

    python3 demo_exit.py
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from schema import TradePlan
from guardrail import Guardrail
from risk_policy import ACTIVE_POLICY
from state import AccountState
from positions import Position, PositionStore
from market_data import MockMarketData
from exit_monitor import ExitMonitor, ExitConfig, ExitAction

HERE = Path(__file__).parent
POS_PATH = HERE / "demo_positions.json"
STATE_PATH = HERE / "demo_exit_state.json"


def fresh_state(equity=100_000.0) -> AccountState:
    return AccountState(
        equity=equity, day_anchor_equity=equity, week_anchor_equity=equity,
        day_key=date.today().isoformat(), week_key="2026-W22",
    )


PLANS = [
    {  # will be INVALIDATED when SPY breaks 531
        "plan_id": "SPY-call", "symbol": "SPY", "structure": "debit_call_spread",
        "legs": [
            {"symbol": "SPY", "expiry": "2026-06-19", "strike": 535, "right": "C", "side": "BUY"},
            {"symbol": "SPY", "expiry": "2026-06-19", "strike": 540, "right": "C", "side": "SELL"},
        ],
        "thesis": "continuation", "net_price": 2.10,
        "max_loss_usd": 1800.0, "requested_qty": 9, "target_profit_usd": 2700.0,
        "invalidation": {"kind": "underlying_below", "value": 531.0},
    },
    {  # will hit TAKE-PROFIT
        "plan_id": "QQQ-put", "symbol": "QQQ", "structure": "credit_put_spread",
        "legs": [
            {"symbol": "QQQ", "expiry": "2026-06-19", "strike": 440, "right": "P", "side": "SELL"},
            {"symbol": "QQQ", "expiry": "2026-06-19", "strike": 435, "right": "P", "side": "BUY"},
        ],
        "thesis": "sell premium", "net_price": -1.40,
        "max_loss_usd": 1800.0, "requested_qty": 5, "target_profit_usd": 700.0,
        "invalidation": {"kind": "underlying_below", "value": 438.0},
    },
]


def main() -> None:
    for p in (POS_PATH, STATE_PATH):
        p.unlink(missing_ok=True)

    state = fresh_state()
    store = PositionStore(POS_PATH)
    guard = Guardrail(ACTIVE_POLICY)

    # open the approved positions
    for raw in PLANS:
        plan = TradePlan.from_dict(raw)
        res = guard.evaluate(plan, state)
        if res.tradeable:
            store.add(Position.from_execution(plan, res, entry_net_price=plan.net_price))
            state.open_positions += 1
            state.deployed_usd += res.per_unit_max_loss * res.approved_qty
            print(f"OPEN  {plan.plan_id}: {res.approved_qty} units, "
                  f"max loss ${res.per_unit_max_loss * res.approved_qty:,.0f}")
    print(f"Start equity ${state.equity:,.0f}\n" + "=" * 60)

    market = MockMarketData(
        prices={"SPY": 536.0, "QQQ": 445.0},
        pnls={"SPY-call": 300.0, "QQQ-put": 100.0},
    )
    mon = ExitMonitor(store, market, state, STATE_PATH,
                      config=ExitConfig(poll_seconds=0),
                      closer=lambda pos, pnl: None)  # paper: no real broker here

    # a scripted "market path": (label, price/pnl updates)
    ticks = [
        ("t1 drift",      {"SPY": 535.0}, {"SPY-call": 150.0, "QQQ-put": 250.0}),
        ("t2 QQQ wins",   {"QQQ": 447.0}, {"QQQ-put": 720.0}),   # -> take profit
        ("t3 SPY breaks", {"SPY": 530.5}, {"SPY-call": -600.0}), # -> invalidation
    ]

    for label, px, pnl in ticks:
        for s, v in px.items():
            market.set_price(s, v)
        for pid, v in pnl.items():
            market.set_pnl(pid, v)
        decisions = mon.run_once()
        closes = [d for d in decisions if d.action == ExitAction.CLOSE]
        print(f"[{label}] open={len(store.open_positions())} "
              f"equity=${state.equity:,.0f} day={state.day_drawdown_pct:+.2%}")
        for d in closes:
            print(f"    CLOSE {d.plan_id}: {d.code.value} — {d.reason} (P&L ${d.pnl_usd:,.0f})")

    print("=" * 60)
    print(f"Final equity ${state.equity:,.0f}  |  open positions: {len(store.open_positions())}")
    for p in store.all():
        print(f"  {p.plan_id}: {p.status} "
              f"{'(' + p.close_reason + ', $' + format(p.realized_pnl_usd, ',.0f') + ')' if not p.is_open else ''}")


if __name__ == "__main__":
    main()
