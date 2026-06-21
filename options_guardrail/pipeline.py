"""
Session orchestrator — the whole pipeline in one place.

    Opus strategist JSON
        -> bridge (tolerant parse + guardrail)
        -> open approved plans (paper executor, or dry)
        -> exit monitor loop through the session
        -> persist state + positions

Two modes:
    DRY  (default): no IBKR. You supply a MarketDataProvider (e.g. MockMarketData)
                    so you can rehearse a full session offline.
    LIVE-PAPER:     connects IBKRPaperExecutor + IBKRMarketData (paper-gated).

The orchestrator never widens risk — every entry goes through the guardrail, and
every open position is managed by the exit monitor with the same rules used in the
backtest.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from state import AccountState
from positions import Position, PositionStore
from guardrail import Guardrail
from risk_policy import ACTIVE_POLICY
from exit_monitor import ExitMonitor, ExitConfig, ExitAction
from market_data import MarketDataProvider
from schema import TradePlan
from strategist_bridge import (
    parse_strategist_output, evaluate_envelope, summarize, StrategistEnvelope,
)

HERE = Path(__file__).parent
STATE_PATH = HERE / "session_state.json"
POS_PATH = HERE / "session_positions.json"


@dataclass
class OpenReport:
    opened: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)


class SessionOrchestrator:
    def __init__(
        self,
        market: MarketDataProvider,
        state: AccountState,
        store: PositionStore,
        guard: Optional[Guardrail] = None,
        executor=None,                      # IBKRPaperExecutor or None (dry)
        exit_config: ExitConfig = ExitConfig(),
        state_path: Optional[Path] = STATE_PATH,
        notifier=None,                      # obj with .notify(text); e.g. TelegramClient
        approver: Optional[Callable[[TradePlan, "object"], bool]] = None,
    ):
        self.market = market
        self.state = state
        self.store = store
        self.guard = guard or Guardrail(ACTIVE_POLICY)
        self.executor = executor
        self.notifier = notifier
        self.approver = approver
        self.plan_registry: Dict[str, TradePlan] = {
            p.plan_id: None for p in store.open_positions()  # filled as we open
        }
        # exit monitor closes via executor in live mode, otherwise just books it
        closer = None
        if executor is not None:
            def closer(pos, pnl):  # noqa: E306
                self.executor.close_position(pos, pos.qty)
        self.monitor = ExitMonitor(store, market, state, state_path,
                                   config=exit_config, closer=closer)

    # ---------- phase 1: ingest strategist + open ----------
    def open_from_strategist(self, raw_text: str) -> tuple[StrategistEnvelope, OpenReport]:
        env = parse_strategist_output(raw_text)
        decisions = evaluate_envelope(env, self.state, self.guard)
        rep = OpenReport()
        print(summarize(env, decisions))

        for d in decisions:
            if not d.result.tradeable:
                rep.skipped.append(d.plan.plan_id)
                continue
            if self.store.get(d.plan.plan_id) is not None:
                rep.skipped.append(d.plan.plan_id)  # already open
                continue

            # optional human/Telegram gate (semi-auto mode)
            if self.approver is not None and not self.approver(d.plan, d.result):
                rep.skipped.append(d.plan.plan_id)
                self._notify(f"⏭️ Skipped {d.plan.plan_id} (not approved).")
                continue

            # submit (live) or just record (dry)
            if self.executor is not None:
                exec_rep = self.executor.execute(d.plan, d.result)
                if not exec_rep.submitted:
                    rep.skipped.append(d.plan.plan_id)
                    continue

            pos = Position.from_execution(d.plan, d.result,
                                          entry_net_price=d.plan.net_price)
            self.store.add(pos)
            self.plan_registry[d.plan.plan_id] = d.plan
            self.state.open_positions += 1
            self.state.deployed_usd += d.result.per_unit_max_loss * d.result.approved_qty
            rep.opened.append(d.plan.plan_id)
            self._notify(
                f"🟢 OPEN {d.plan.plan_id} {d.plan.structure} x{d.result.approved_qty} "
                f"(max loss ${d.result.per_unit_max_loss * d.result.approved_qty:,.0f})")

        self.state.save(self.monitor.state_path)
        return env, rep

    def _notify(self, text: str) -> None:
        if self.notifier is not None:
            try:
                self.notifier.notify(text)
            except Exception as e:
                print(f"[notify failed] {e}")

    # ---------- phase 2: manage open positions ----------
    def run_session(self, max_ticks: Optional[int] = None) -> None:
        """Run the exit-monitor loop. In live mode use run_forever; here we expose
        a bounded loop so it's testable and so a dry rehearsal terminates."""
        ticks = 0
        while self.store.open_positions():
            halted_before = self.state.day_drawdown_pct
            decisions = self.monitor.run_once()
            for d in decisions:
                if d.action == ExitAction.CLOSE:
                    emoji = "✅" if d.pnl_usd >= 0 else "🔻"
                    msg = (f"{emoji} CLOSE {d.plan_id}: {d.code.value} — {d.reason} "
                           f"(P&L ${d.pnl_usd:,.0f})")
                    print(f"  {msg}")
                    self._notify(msg)
            # alert once if a kill-switch just armed
            if (self.state.day_drawdown_pct <= -self.guard.policy.daily_halt_pct
                    and halted_before > -self.guard.policy.daily_halt_pct):
                self._notify(f"🛑 DAILY KILL-SWITCH armed at "
                             f"{self.state.day_drawdown_pct:+.2%}. No new entries today.")
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                break

    def status(self) -> str:
        return (f"equity ${self.state.equity:,.0f} | "
                f"open {len(self.store.open_positions())} | "
                f"day {self.state.day_drawdown_pct:+.2%} "
                f"week {self.state.week_drawdown_pct:+.2%}")


# ----------------------------- CLI (dry rehearsal) -----------------------------
def _dry_main(args) -> None:
    """Offline rehearsal: read strategist JSON, open, then mark positions to a
    user-supplied closing P&L map to show exits firing."""
    from market_data import MockMarketData

    raw = Path(args.file).read_text()
    state = AccountState.load(STATE_PATH, default_equity=args.equity)
    state.equity = args.equity
    store = PositionStore(POS_PATH)

    market = MockMarketData()
    orch = SessionOrchestrator(market, state, store, executor=None)

    print("=" * 72)
    env, rep = orch.open_from_strategist(raw)
    print("=" * 72)
    print(f"Opened {len(rep.opened)}: {rep.opened}")
    print(orch.status())

    # To demonstrate exits in a dry run, force every open position to its
    # invalidation by reading underlying prices the user can tweak in the JSON.
    # Here we simply mark them flat and stop (no live data offline).
    print("\n(no live market data in dry mode — connect IBKR for real management)")
    state.save(STATE_PATH)


def main() -> None:
    ap = argparse.ArgumentParser(description="Run a trading session pipeline.")
    ap.add_argument("file", help="path to strategist JSON output")
    ap.add_argument("--equity", type=float, default=100_000.0)
    ap.add_argument("--live-paper", action="store_true",
                    help="connect IBKR paper + manage positions live")
    args = ap.parse_args()

    if not args.live_paper:
        _dry_main(args)
        return

    # ---- live paper ----
    from ibkr_paper_executor import executor_from_env
    from market_data import IBKRMarketData

    raw = Path(args.file).read_text()
    state = AccountState.load(STATE_PATH, default_equity=args.equity)
    store = PositionStore(POS_PATH)

    executor = executor_from_env().connect()
    market = IBKRMarketData(executor._ib)  # share the connection
    orch = SessionOrchestrator(market, state, store, executor=executor)

    env, rep = orch.open_from_strategist(raw)
    print(f"Opened {len(rep.opened)}: {rep.opened}\n{orch.status()}")
    try:
        orch.monitor.run_forever()
    finally:
        executor.disconnect()


if __name__ == "__main__":
    main()
