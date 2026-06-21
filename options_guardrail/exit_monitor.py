"""
Exit monitor — the unattended loop that closes open positions.

Entry-time guardrails stop bad trades from opening. This stops good trades from
turning into disasters: it watches every open position and closes it the moment
the thesis is invalidated, the profit target is hit, or the stop is breached.
Realized P&L is fed back into AccountState so the day/week kill-switch reacts to
actual paper losses in real time.

Each tick, per open position, FIRST match wins:
    1. INVALIDATION  — underlying through level / IV through level / time stop
    2. STOP          — unrealized loss >= stop_fraction * defined max loss
    3. TAKE_PROFIT   — unrealized gain >= target_profit_usd
    else HOLD.

The loop is broker-agnostic: pass any MarketDataProvider and an optional
`closer` callable. With MockMarketData + no closer it's fully testable offline.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional

from schema import InvalidationKind
from positions import Position, PositionStore
from market_data import MarketDataProvider
from state import AccountState


class ExitAction(str, Enum):
    HOLD = "HOLD"
    CLOSE = "CLOSE"


class ExitCode(str, Enum):
    INVALIDATION = "INVALIDATION"
    STOP = "STOP"
    TAKE_PROFIT = "TAKE_PROFIT"
    NONE = "NONE"


@dataclass
class ExitConfig:
    # Close when unrealized loss reaches this fraction of the defined max loss.
    # 1.0 = let it ride to full defined loss; 0.6 = cut at 60% of max loss.
    stop_loss_fraction: float = 0.85
    # Honor profit targets if the plan supplied one.
    use_profit_target: bool = True
    poll_seconds: float = 30.0
    use_marked_drawdown: bool = True
    # Safety stop: a session can never hang past the trading day. ~6.5h covers a
    # 21:15 open → ~03:00 close (ICT) with buffer. Backstop to the normal exit
    # (book goes flat) in case an out-of-band close is somehow missed.
    max_runtime_sec: float = 23400.0


@dataclass
class ExitDecision:
    plan_id: str
    action: ExitAction
    code: ExitCode
    reason: str
    pnl_usd: float = 0.0


def _parse_iso(value) -> datetime:
    dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class ExitMonitor:
    def __init__(
        self,
        store: PositionStore,
        market: MarketDataProvider,
        state: AccountState,
        state_path: str | Path | None = None,
        config: ExitConfig = ExitConfig(),
        closer: Optional[Callable[[Position, float], None]] = None,
    ):
        """
        closer(position, pnl) actually flattens the position at the broker.
        If None, the monitor only books the close in state/store (dry mode).
        """
        self.store = store
        self.market = market
        self.state = state
        self.state_path = Path(state_path) if state_path is not None else None
        self.config = config
        self.closer = closer
        # plan_ids whose realized P&L is already reflected in self.state.equity.
        # Seed with positions already CLOSED at construction (their P&L is baked
        # into the loaded equity) so we never double-book historical closes.
        self._booked_closed: set[str] = {
            p.plan_id for p in store.all() if not p.is_open
        }

    # ---------- pure decision ----------
    def evaluate(self, pos: Position, now: Optional[datetime] = None) -> ExitDecision:
        now = now or datetime.now(timezone.utc)
        pnl = self.market.position_pnl(pos)

        # 1. INVALIDATION
        inv = pos.invalidation
        if inv is not None:
            kind = InvalidationKind(inv["kind"])
            val = inv["value"]
            if kind == InvalidationKind.UNDERLYING_BELOW:
                px = self.market.underlying_price(pos.symbol)
                if px <= float(val):
                    return ExitDecision(pos.plan_id, ExitAction.CLOSE,
                                        ExitCode.INVALIDATION,
                                        f"{pos.symbol} {px:.2f} <= invalidation {float(val):.2f}",
                                        pnl)
            elif kind == InvalidationKind.UNDERLYING_ABOVE:
                px = self.market.underlying_price(pos.symbol)
                if px >= float(val):
                    return ExitDecision(pos.plan_id, ExitAction.CLOSE,
                                        ExitCode.INVALIDATION,
                                        f"{pos.symbol} {px:.2f} >= invalidation {float(val):.2f}",
                                        pnl)
            elif kind == InvalidationKind.IV_ABOVE:
                iv = self.market.implied_vol(pos.symbol)
                if iv is not None and iv >= float(val):
                    return ExitDecision(pos.plan_id, ExitAction.CLOSE,
                                        ExitCode.INVALIDATION,
                                        f"{pos.symbol} IV {iv:.3f} >= {float(val):.3f}", pnl)
            elif kind == InvalidationKind.IV_BELOW:
                iv = self.market.implied_vol(pos.symbol)
                if iv is not None and iv <= float(val):
                    return ExitDecision(pos.plan_id, ExitAction.CLOSE,
                                        ExitCode.INVALIDATION,
                                        f"{pos.symbol} IV {iv:.3f} <= {float(val):.3f}", pnl)
            elif kind == InvalidationKind.TIME_STOP:
                if now >= _parse_iso(val):
                    return ExitDecision(pos.plan_id, ExitAction.CLOSE,
                                        ExitCode.INVALIDATION,
                                        f"time stop {val} reached", pnl)

        # 2. STOP (defined-risk early cut)
        if pos.max_loss_usd > 0:
            stop_at = -self.config.stop_loss_fraction * pos.max_loss_usd
            if pnl <= stop_at:
                return ExitDecision(pos.plan_id, ExitAction.CLOSE, ExitCode.STOP,
                                    f"uPnL ${pnl:,.0f} <= stop ${stop_at:,.0f} "
                                    f"({self.config.stop_loss_fraction:.0%} of max loss)",
                                    pnl)

        # 3. TAKE PROFIT
        if (self.config.use_profit_target and pos.target_profit_usd is not None
                and pnl >= pos.target_profit_usd):
            return ExitDecision(pos.plan_id, ExitAction.CLOSE, ExitCode.TAKE_PROFIT,
                                f"uPnL ${pnl:,.0f} >= target ${pos.target_profit_usd:,.0f}",
                                pnl)

        return ExitDecision(pos.plan_id, ExitAction.HOLD, ExitCode.NONE, "hold", pnl)

    # ---------- one pass over all open positions ----------
    def run_once(self, now: Optional[datetime] = None) -> List[ExitDecision]:
        now = now or datetime.now(timezone.utc)
        open_pos = self.store.open_positions()

        # Update state's unrealized_pnl
        total_unrealized = sum(self.market.position_pnl(pos) for pos in open_pos)
        self.state.unrealized_pnl = total_unrealized

        from risk_policy import ACTIVE_POLICY
        p = ACTIVE_POLICY

        day_dd = self.state.get_day_drawdown_pct(self.config.use_marked_drawdown)
        week_dd = self.state.get_week_drawdown_pct(self.config.use_marked_drawdown)

        kill_switch_breached = False
        kill_reason = ""

        if self.config.use_marked_drawdown:
            if day_dd <= -p.daily_halt_pct:
                kill_switch_breached = True
                kill_reason = f"MARKED DAILY KILL-SWITCH BREACHED: P&L {day_dd:+.2%} <= -{p.daily_halt_pct:.0%}"
            elif week_dd <= -p.weekly_halt_pct:
                kill_switch_breached = True
                kill_reason = f"MARKED WEEKLY KILL-SWITCH BREACHED: P&L {week_dd:+.2%} <= -{p.weekly_halt_pct:.0%}"

        decisions: List[ExitDecision] = []
        if kill_switch_breached:
            for pos in open_pos:
                pnl = self.market.position_pnl(pos)
                d = ExitDecision(pos.plan_id, ExitAction.CLOSE, ExitCode.STOP, kill_reason, pnl)
                decisions.append(d)
                self._close(pos, d)
        else:
            for pos in open_pos:
                d = self.evaluate(pos, now=now)
                decisions.append(d)
                if d.action == ExitAction.CLOSE:
                    self._close(pos, d)

        # Recalculate unrealized P&L after potential closes
        self.state.unrealized_pnl = sum(self.market.position_pnl(pos) for pos in self.store.open_positions())
        self.state.roll_periods(now)  # uses backtest clock when supplied
        self.state.save(self.state_path)
        return decisions

    def _close(self, pos: Position, decision: ExitDecision) -> None:
        # broker close (paper) if a closer was supplied
        if self.closer is not None:
            self.closer(pos, decision.pnl_usd)
        # book it
        self.store.mark_closed(pos.plan_id, decision.pnl_usd, decision.code.value)
        # feed realized P&L into equity so the kill-switch sees it
        self.state.equity += decision.pnl_usd
        self.state.open_positions = max(0, self.state.open_positions - 1)
        self.state.deployed_usd = max(0.0, self.state.deployed_usd - pos.max_loss_usd)
        self._booked_closed.add(pos.plan_id)  # accounted for; don't re-book on reconcile

    def reconcile_from_store(self) -> None:
        """Make AccountState a faithful projection of the (possibly reloaded)
        position store. Books the realized P&L of any position closed OUT OF BAND
        (e.g. by flatten_all.py in a separate process) into equity exactly once,
        then recomputes open count + deployed capital from what's actually open.

        Because both the session and flatten compute equity as base + Σ(realized
        in the store), they converge to the same value regardless of write order —
        eliminating the cross-process drift that left equity/open_positions stale."""
        for pos in self.store.all():
            if not pos.is_open and pos.plan_id not in self._booked_closed:
                self.state.equity += (pos.realized_pnl_usd or 0.0)
                self._booked_closed.add(pos.plan_id)
        open_now = self.store.open_positions()
        self.state.open_positions = len(open_now)
        self.state.deployed_usd = sum(p.max_loss_usd for p in open_now)

    # ---------- the unattended loop ----------
    def run_forever(self, max_iterations: Optional[int] = None) -> None:  # pragma: no cover
        i = 0
        start = time.monotonic()
        while True:
            # Re-read the book from disk first so out-of-band closes (e.g.
            # flatten_all.py running as a separate process) are observed, then
            # reconcile equity + counters so state never drifts.
            self.store.reload()
            self.reconcile_from_store()

            decisions = self.run_once()
            closed = [d for d in decisions if d.action == ExitAction.CLOSE]
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"[{ts}] checked {len(decisions)} open, closed {len(closed)} | "
                  f"equity ${self.state.equity:,.0f} "
                  f"day {self.state.day_drawdown_pct:+.2%}")
            for d in closed:
                print(f"    CLOSE {d.plan_id}: {d.code.value} — {d.reason} "
                      f"(P&L ${d.pnl_usd:,.0f})")
            i += 1
            if max_iterations is not None and i >= max_iterations:
                return
            if not self.store.open_positions():
                print("No open positions left. Exiting loop.")
                return
            if (time.monotonic() - start) >= self.config.max_runtime_sec:
                print(f"Max runtime {self.config.max_runtime_sec:.0f}s reached; "
                      f"exiting loop (safety stop).")
                return
            time.sleep(self.config.poll_seconds)
