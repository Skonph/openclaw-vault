"""
Live-paper session entrypoint (the thing systemd starts at the open).

Wires config -> Telegram -> IBKR paper executor + market data -> orchestrator.
Reads the strategist envelope that strategist_run.py wrote in the evening, opens
approved plans (with Telegram approval in 'semi' mode), then manages exits until
flat or the session-manager service is stopped.

    GUARDRAIL_MODE=semi python3 run_ops_session.py        # Telegram-gated entries
    GUARDRAIL_MODE=auto python3 run_ops_session.py        # fully autonomous

A file lock prevents two managers running against the same account.
"""

from __future__ import annotations

import sys
from pathlib import Path

from config import Config
from state import AccountState
from positions import PositionStore
from guardrail import Guardrail
from risk_policy import ACTIVE_POLICY
from exit_monitor import ExitConfig
from pipeline import SessionOrchestrator
from telegram_notify import from_config as telegram_from_config


def _acquire_lock(path: Path):
    """Simple advisory lock so cron overlaps can't double-run."""
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another session manager holds the lock; exiting.")
        sys.exit(0)
    return fh


def main() -> None:
    cfg = Config.load()
    tg = telegram_from_config(cfg)
    lock = _acquire_lock(cfg.data_dir / "session.lock")

    if not cfg.strategist_output_path.exists():
        tg.notify("⚠️ No strategist output found; nothing to trade this session.")
        return
    raw = cfg.strategist_output_path.read_text()

    # connect IBKR paper (hard-gated to DU/DF accounts inside the executor)
    from ibkr_paper_executor import IBKRPaperExecutor
    from market_data import IBKRMarketData, TradierMarketData, YahooMarketData, FallbackMarketData
    from tradier_feed import TradierClient
    executor = IBKRPaperExecutor(host=cfg.ibkr_host, port=cfg.ibkr_port,
                                 client_id=cfg.ibkr_client_id,
                                 paper_only=cfg.ibkr_paper_only).connect()

    ibkr_market = IBKRMarketData(executor._ib)
    if cfg.market_data_provider == "tradier":
        if not cfg.tradier_token:
            raise RuntimeError("TRADIER_TOKEN is required when using the tradier market data provider")
        tradier_client = TradierClient(cfg.tradier_token, cfg.tradier_base_url)
        primary = TradierMarketData(tradier_client)
        market = FallbackMarketData([primary, ibkr_market, YahooMarketData()])
    else:
        market = FallbackMarketData([ibkr_market, YahooMarketData()])

    state = AccountState.load(cfg.state_path, default_equity=cfg.equity)
    store = PositionStore(cfg.positions_path)
    guard = Guardrail(ACTIVE_POLICY)

    # semi mode: each entry must be approved via Telegram; auto mode: no gate
    approver = None
    if cfg.mode == "semi":
        def approver(plan, res):  # noqa: E306
            text = (f"*Approve trade?*\n`{plan.plan_id}`\n{plan.structure} "
                    f"x{res.approved_qty}\nthesis: {plan.thesis}\n"
                    f"max loss ${res.per_unit_max_loss * res.approved_qty:,.0f} | "
                    f"invalidation: {plan.invalidation.kind.value} "
                    f"{plan.invalidation.value}")
            return tg.request_approval(text, plan.plan_id,
                                       timeout_sec=cfg.approval_timeout_sec)

    orch = SessionOrchestrator(market, state, store, guard=guard, executor=executor,
                               exit_config=ExitConfig(), state_path=cfg.state_path,
                               notifier=tg, approver=approver)

    tg.notify(f"▶️ Session start ({cfg.mode}) | equity ${state.equity:,.0f}")
    try:
        env, rep = orch.open_from_strategist(raw)
        tg.notify(f"Opened {len(rep.opened)}, skipped {len(rep.skipped)}. "
                  f"Managing exits…")
        orch.monitor.run_forever()
        tg.notify(f"⏹️ Session flat. {orch.status()}")
    except Exception as e:
        tg.notify(f"❗ Session error: {e}")
        raise
    finally:
        executor.disconnect()
        lock.close()


if __name__ == "__main__":
    main()
