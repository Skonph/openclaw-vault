"""
Daily trading-log report -> Telegram.

Runs at 08:30 ICT (01:30 UTC), after the US close, and summarizes the session that
just ended: trades opened/closed, realized P&L, win rate, equity, week drawdown,
open positions carried, and kill-switch status. Read-only — it never trades.

Testable: build_daily_report() is pure (store + state in, text out).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from positions import Position, PositionStore
from state import AccountState
from risk_policy import ACTIVE_POLICY, RiskPolicy


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def build_daily_report(store: PositionStore, state: AccountState,
                       now: Optional[datetime] = None, window_hours: int = 24,
                       policy: RiskPolicy = ACTIVE_POLICY) -> str:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)

    closed = [p for p in store.all()
              if not p.is_open and (_parse(p.closed_at) or now) >= cutoff]
    opened = [p for p in store.all()
              if (_parse(p.opened_at) or now) >= cutoff]
    open_now = store.open_positions()

    pnls = [p.realized_pnl_usd or 0.0 for p in closed]
    total = sum(pnls)
    wins = [x for x in pnls if x > 0]
    win_rate = (len(wins) / len(closed)) if closed else 0.0

    date_label = now.strftime("%Y-%m-%d")
    lines: List[str] = [
        f"📊 *Daily Paper Report* — {date_label} (08:30 ICT)",
        f"_Policy {policy.name} • last {window_hours}h_",
        "",
        f"*Closed:* {len(closed)}  |  *Opened:* {len(opened)}  |  "
        f"*Still open:* {len(open_now)}",
    ]
    if closed:
        lines.append(f"*Realized P&L:* ${total:,.0f}  |  *Win rate:* {win_rate:.0%}")
    lines += [
        f"*Equity:* ${state.equity:,.0f}",
        f"*Day P&L:* {state.day_drawdown_pct:+.2%}  |  "
        f"*Week P&L:* {state.week_drawdown_pct:+.2%}",
    ]

    # kill-switch status
    if state.week_drawdown_pct <= -policy.weekly_halt_pct:
        lines.append("🛑 *WEEKLY kill-switch ARMED* — no new entries this week.")
    elif state.day_drawdown_pct <= -policy.daily_halt_pct:
        lines.append("🛑 *DAILY kill-switch ARMED* — no new entries today.")
    else:
        lines.append("✅ Kill-switch clear.")

    if closed:
        lines += ["", "*Closed trades:*"]
        for p in sorted(closed, key=lambda x: x.closed_at or ""):
            tag = "✅" if (p.realized_pnl_usd or 0) >= 0 else "🔻"
            lines.append(f"{tag} `{p.plan_id}` {p.structure} x{p.qty} — "
                         f"{p.close_reason} (${p.realized_pnl_usd or 0:,.0f})")

    if open_now:
        lines += ["", "*Carried open:*"]
        for p in open_now:
            lines.append(f"• `{p.plan_id}` {p.structure} x{p.qty} "
                         f"(max loss ${p.max_loss_usd:,.0f})")

    if not closed and not opened and not open_now:
        lines += ["", "_No activity in the window (no-edge / halted day)._"]

    return "\n".join(lines)


def main() -> None:
    from config import Config
    from telegram_notify import from_config as telegram_from_config

    cfg = Config.load()
    state = AccountState.load(cfg.state_path, default_equity=cfg.equity)
    store = PositionStore(cfg.positions_path)
    report = build_daily_report(store, state)

    tg = telegram_from_config(cfg)
    tg.notify(report)
    print(report)


if __name__ == "__main__":
    main()
