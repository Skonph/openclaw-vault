"""
Context builder — assembles the strategist's input bundle (context.json).

The strategist is only as good as what it's fed. This gathers the session inputs
into one JSON file that strategist_run.py reads. Each data source is a pluggable
provider: wire your real feeds in, and anything you leave unset is clearly marked
"not configured" so the strategist lowers conviction instead of hallucinating.

Providers are plain callables returning a string or JSON-able object:
    account_provider()          -> dict   (equity, open positions, day/week P&L)
    overnight_flow_provider()   -> str/dict
    iv_provider()               -> str/dict
    calendar_provider()         -> str/dict   (today's ET econ events)

Run in the evening BEFORE strategist_run.py. Account snapshot is pulled from the
local state/positions by default (no broker needed); override to read IBKR live.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from state import AccountState
from positions import PositionStore

Provider = Callable[[], Any]
NOT_CONFIGURED = "not configured — strategist should lower conviction / skip vol-dependent ideas"


def account_from_local(state: AccountState, store: PositionStore) -> Dict[str, Any]:
    return {
        "equity": round(state.equity, 2),
        "day_pnl_pct": round(state.day_drawdown_pct, 4),
        "week_pnl_pct": round(state.week_drawdown_pct, 4),
        "open_positions": [
            {"plan_id": p.plan_id, "symbol": p.symbol, "structure": p.structure,
             "qty": p.qty, "max_loss_usd": p.max_loss_usd}
            for p in store.open_positions()
        ],
    }


def build_context(
    session_date: str,
    watchlist: List[str],
    account: Dict[str, Any],
    overnight_flow_provider: Optional[Provider] = None,
    iv_provider: Optional[Provider] = None,
    calendar_provider: Optional[Provider] = None,
    notes: str = "",
) -> Dict[str, Any]:
    def _safe(provider: Optional[Provider]) -> Any:
        if provider is None:
            return NOT_CONFIGURED
        try:
            return provider()
        except Exception as e:
            return f"provider error: {e} — treat as {NOT_CONFIGURED}"

    return {
        "session_date": session_date,
        "generated_at_iso": datetime.now(timezone.utc).isoformat(),
        "watchlist": watchlist,
        "account": account,
        "overnight_flow": _safe(overnight_flow_provider),
        "iv": _safe(iv_provider),
        "economic_calendar": _safe(calendar_provider),
        "notes": notes,
    }


def _next_session_date(now: Optional[datetime] = None) -> str:
    # Next US weekday (UTC date is close enough for a date label; refine if needed).
    now = now or datetime.now(timezone.utc)
    return now.date().isoformat()


def main() -> None:
    import argparse
    from config import Config

    ap = argparse.ArgumentParser()
    ap.add_argument("--watchlist", default="SPY,QQQ,IWM",
                    help="comma-separated symbols the strategist may consider")
    ap.add_argument("--notes", default="")
    ap.add_argument("--out", help="output path (default: <data>/context.json)")
    args = ap.parse_args()

    cfg = Config.load()
    state = AccountState.load(cfg.state_path, default_equity=cfg.equity)
    store = PositionStore(cfg.positions_path)
    watchlist = [s.strip().upper() for s in args.watchlist.split(",") if s.strip()]

    session_date = _next_session_date()

    # Tradier supplies real quotes + ATM IV (and a market clock we no longer use
    # for the calendar). If no Tradier token, these stay None -> strategist on priors.
    from tradier_feed import from_config as tradier_from_config
    providers = tradier_from_config(cfg, watchlist)
    flow_p = providers["flow"] if providers else None
    iv_p = providers["iv"] if providers else None

    # Real economic calendar: Finnhub -> FRED fallback (event risk for the session).
    from econ_calendar import from_config as econ_from_config
    cal_p = econ_from_config(cfg, session_date)

    notes = args.notes
    if cal_p is None:
        notes = (notes + " | NOTE: no econ-calendar key set; event risk unknown.").strip(" |")

    # ─── SHARED MACRO SIGNAL CROSS-CHECK ───
    try:
        from read_macro_signal import load_macro_signal
        shared_sig = load_macro_signal()
        if shared_sig:
            shared_vix = shared_sig.get("vix")
            shared_spy = shared_sig.get("spy_change_pct")
            print(f"  📡 Shared market_context read (VIX: {shared_vix}, SPY: {shared_spy}%)")
            notes = (notes + f" | Shared market_context VIX: {shared_vix}, SPY: {shared_spy}%").strip(" |")
    except Exception as e:
        print(f"[WARN] Failed to read shared macro context: {e}")

    ctx = build_context(
        session_date=session_date,
        watchlist=watchlist,
        account=account_from_local(state, store),
        overnight_flow_provider=flow_p,
        iv_provider=iv_p,
        calendar_provider=cal_p,
        notes=notes,
    )

    out = Path(args.out) if args.out else (cfg.data_dir / "context.json")
    out.write_text(json.dumps(ctx, indent=2))
    configured = [k for k in ("overnight_flow", "iv", "economic_calendar")
                  if ctx[k] != NOT_CONFIGURED and "provider error" not in str(ctx[k])]
    print(f"Wrote {out} | watchlist={watchlist} | "
          f"feeds configured: {configured or 'none (priors only)'}")


if __name__ == "__main__":
    main()
