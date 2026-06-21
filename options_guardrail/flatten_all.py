"""
Flatten-all safety net.

Force-closes every open position near the US close so nothing carries overnight
unmanaged (e.g. if the session manager died, or a position never hit its exit).
Books realized P&L into state exactly like a normal exit, updates the store, and
reports to Telegram.

Idempotent: if the book is already flat it does nothing. Read of marks + close go
through the same MarketDataProvider / executor the live system uses, so the core
`flatten()` is testable offline with mocks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from state import AccountState
from positions import Position, PositionStore
from market_data import MarketDataProvider
from schema import TradePlan


@dataclass
class FlattenReport:
    closed: List[str] = field(default_factory=list)
    total_pnl: float = 0.0
    errors: List[str] = field(default_factory=list)


def flatten(
    store: PositionStore,
    state: AccountState,
    market: MarketDataProvider,
    state_path: Optional[Path] = None,
    closer: Optional[Callable[[Position, float], None]] = None,
    reason: str = "EOD_FLATTEN",
) -> FlattenReport:
    """Close all open positions at current marks. closer(pos, pnl) performs the
    broker close (None in dry mode)."""
    rep = FlattenReport()
    for pos in list(store.open_positions()):
        try:
            pnl = market.position_pnl(pos)
            if closer is not None:
                closer(pos, pnl)
            store.mark_closed(pos.plan_id, pnl, reason)
            state.equity += pnl
            state.open_positions = max(0, state.open_positions - 1)
            state.deployed_usd = max(0.0, state.deployed_usd - pos.max_loss_usd)
            rep.closed.append(pos.plan_id)
            rep.total_pnl += pnl
        except Exception as e:  # one bad close shouldn't strand the rest
            rep.errors.append(f"{pos.plan_id}: {e}")
    state.save(state_path)
    return rep


def main() -> None:
    from config import Config
    from telegram_notify import from_config as telegram_from_config

    cfg = Config.load()
    tg = telegram_from_config(cfg)
    state = AccountState.load(cfg.state_path, default_equity=cfg.equity)
    store = PositionStore(cfg.positions_path)

    if not store.open_positions():
        tg.notify("🧹 Flatten: book already flat, nothing to close.")
        return

    # connect IBKR paper and build a closer + market
    from ibkr_paper_executor import IBKRPaperExecutor
    from market_data import IBKRMarketData

    executor = IBKRPaperExecutor(host=cfg.ibkr_host, port=cfg.ibkr_port,
                                 client_id=cfg.ibkr_client_id + 1,  # distinct clientId
                                 paper_only=cfg.ibkr_paper_only).connect()
    market = IBKRMarketData(executor._ib)

    # Recover plan legs from today's strategist output so we can send closing
    # combos even though the position store doesn't carry legs.
    plans: Dict[str, TradePlan] = {}
    if cfg.strategist_output_path.exists():
        from strategist_bridge import parse_strategist_output
        env = parse_strategist_output(cfg.strategist_output_path.read_text())
        plans = {p.plan_id: p for p in env.plans}

    def closer(pos: Position, pnl: float) -> None:
        plan = plans.get(pos.plan_id)
        if plan is not None:
            executor.close_position(plan, pos.qty)
        else:
            # Legs unknown (e.g. carried from a prior session whose output rolled).
            tg.notify(f"⚠️ Flatten: no legs for `{pos.plan_id}` — close it manually "
                      f"in IBKR. Booking locally to keep state consistent.")

    try:
        rep = flatten(store, state, market, state_path=cfg.state_path, closer=closer)
        msg = (f"🧹 *Flatten complete* — closed {len(rep.closed)} "
               f"(P&L ${rep.total_pnl:,.0f}). Equity ${state.equity:,.0f}.")
        if rep.errors:
            msg += "\n⚠️ errors: " + "; ".join(rep.errors)
        tg.notify(msg)
        print(msg)
    finally:
        executor.disconnect()


if __name__ == "__main__":
    main()
