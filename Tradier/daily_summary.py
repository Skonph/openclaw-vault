#!/usr/bin/env python3
"""
Tradier Daily Summary — Sent to Telegram at 8:00 AM ICT
─────────────────────────────────────────────────────────
Cron (Bangkok server, ICT = UTC+7):
  0 8 * * 2-6 /home/ubuntu/trading-bot/venv/bin/python3 /home/ubuntu/trading-bot/daily_summary.py >> /home/ubuntu/trading-bot/logs/summary.log 2>&1

Days 2-6 (Tue–Sat) covers reports for Mon–Fri US sessions.
8:00 AM ICT = 01:00 UTC = 9:00 PM EDT (previous night) — ~5 hrs after US close.

Reports:
  - Today's auto-executed trades (from trade_log.jsonl)
  - Open positions + unrealized P&L (from Tradier sandbox API)
  - Account balance vs. $2K POC benchmark
  - Next session readiness check
"""

import os
import json
import requests
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─── CONFIG ──────────────────────────────────────────────────────────────────
SCRIPT_DIR      = Path(__file__).parent
SANDBOX_URL     = "https://sandbox.tradier.com/v1"
PROD_URL        = "https://api.tradier.com/v1"
SANDBOX_TOKEN   = os.getenv("TRADIER_SANDBOX_TOKEN", "")
PROD_TOKEN      = os.getenv("TRADIER_PROD_TOKEN", "")
ACCOUNT_ID      = os.getenv("TRADIER_SANDBOX_ACCOUNT", "")
BOT_TOKEN       = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID         = os.getenv("TELEGRAM_CHAT_ID", "")
STARTING_CAPITAL = float(os.getenv("STARTING_CAPITAL", "2000"))
TRADE_LOG       = SCRIPT_DIR / "trade_log.jsonl"

SANDBOX_HEADERS = {"Authorization": f"Bearer {SANDBOX_TOKEN}", "Accept": "application/json"}
PROD_HEADERS    = {"Authorization": f"Bearer {PROD_TOKEN}",    "Accept": "application/json"}

# ─── DATA FETCHERS ────────────────────────────────────────────────────────────

def get_account_balance():
    try:
        r   = requests.get(f"{SANDBOX_URL}/accounts/{ACCOUNT_ID}/balances",
                           headers=SANDBOX_HEADERS, timeout=15)
        bal = r.json().get("balances", {})
        cash    = bal.get("total_cash",  0) or bal.get("cash",   0) or 0
        pnl_day = bal.get("close_pl",   0) or 0
        total_pl= bal.get("total_pl",   0) or 0
        return float(cash), float(pnl_day), float(total_pl)
    except Exception as e:
        print(f"[balance error] {e}")
        return 0.0, 0.0, 0.0

def get_open_positions():
    try:
        r       = requests.get(f"{SANDBOX_URL}/accounts/{ACCOUNT_ID}/positions",
                               headers=SANDBOX_HEADERS, timeout=15)
        pos_raw = r.json().get("positions", None)
        if not pos_raw or pos_raw == "null" or isinstance(pos_raw, str):
            return []
        pos = pos_raw.get("position", [])
        return [pos] if isinstance(pos, dict) else (pos or [])
    except Exception as e:
        print(f"[positions error] {e}")
        return []

def get_today_records():
    """Read today's entries AND exits from trade_log.jsonl, separated."""
    today   = date.today().isoformat()
    entries, exits = [], []
    if TRADE_LOG.exists():
        with open(TRADE_LOG) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    t = json.loads(line)
                    if t.get("date") == today:
                        if t.get("type") == "exit":
                            exits.append(t)
                        elif t.get("type") == "scan":
                            pass  # no-trade heartbeat — not an entry or exit
                        else:
                            entries.append(t)
                except Exception:
                    pass
    return entries, exits

def get_performance_stats():
    """Compute win rate, P&L stats and streaks from all exit records."""
    if not TRADE_LOG.exists():
        return None

    exits = []
    total_entries = 0
    with open(TRADE_LOG) as f:
        for line in f:
            try:
                t = json.loads(line.strip())
                if t.get("type") == "exit" and t.get("success"):
                    exits.append(t)
                elif t.get("type") not in ("exit", "scan") and t.get("success"):
                    total_entries += 1
            except Exception:
                pass

    if not exits:
        return {"total_entries": total_entries, "total_exits": 0}

    pnls   = [e.get("realized_pnl", 0) for e in exits]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    reason_counts = {}
    for e in exits:
        r = e.get("exit_reason", "unknown")
        reason_counts[r] = reason_counts.get(r, 0) + 1

    return {
        "total_entries":  total_entries,
        "total_exits":    len(exits),
        "wins":           len(wins),
        "losses":         len(losses),
        "win_rate":       round(len(wins) / len(exits) * 100, 1),
        "total_pnl":      round(sum(pnls), 2),
        "avg_pnl":        round(sum(pnls) / len(pnls), 2),
        "best_trade":     round(max(pnls), 2),
        "worst_trade":    round(min(pnls), 2),
        "profit_targets": reason_counts.get("profit_target", 0),
        "stop_losses":    reason_counts.get("stop_loss", 0),
        "time_stops":     reason_counts.get("time_stop", 0),
    }

# ─── TELEGRAM SENDER ──────────────────────────────────────────────────────────

def send_telegram(msg: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram not configured — printing summary only")
        print(msg)
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=15,
        )
        if r.status_code == 200:
            print(f"[summary] Telegram sent OK")
        else:
            print(f"[summary] Telegram HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"[summary] Telegram error: {e}")

# ─── SUMMARY BUILDER ──────────────────────────────────────────────────────────

def build_summary() -> str:
    now_ict  = datetime.now(timezone(timedelta(hours=7)))
    us_date  = (datetime.now(timezone(timedelta(hours=-4))) - timedelta(hours=4))

    cash, pnl_day, total_pl = get_account_balance()
    positions    = get_open_positions()
    today_entries, today_exits = get_today_records()
    perf = get_performance_stats()

    poc_pnl_pct = (total_pl / STARTING_CAPITAL * 100) if STARTING_CAPITAL else 0

    lines = []
    lines.append(f"📊 *Daily Summary — {now_ict.strftime('%a %b %d, %Y')}*")
    lines.append(f"_{us_date.strftime('%A')} US session · reported at {now_ict.strftime('%I:%M %p ICT')}_")
    lines.append("")

    # ── Account ──────────────────────────────────────────────────────────────
    lines.append("💰 *Account*")
    lines.append(f"Virtual cash:    ${cash:>10,.2f}")
    lines.append(f"Today's P&L:     ${pnl_day:>+10.2f}")
    lines.append(f"Total P&L:       ${total_pl:>+10.2f}  ({poc_pnl_pct:+.2f}% vs ${STARTING_CAPITAL:,.0f} benchmark)")

    # ── Performance stats ────────────────────────────────────────────────────
    lines.append("")
    lines.append("📈 *Strategy Performance*")
    if perf and perf.get("total_exits", 0) > 0:
        lines.append(f"Trades entered:  {perf['total_entries']}")
        lines.append(f"Trades closed:   {perf['total_exits']}")
        lines.append(f"Win rate:        *{perf['win_rate']}%*  ({perf['wins']}W / {perf['losses']}L)")
        lines.append(f"Avg P&L/trade:   ${perf['avg_pnl']:+.2f}")
        lines.append(f"Best trade:      ${perf['best_trade']:+.2f}")
        lines.append(f"Worst trade:     ${perf['worst_trade']:+.2f}")
        lines.append(f"Exit breakdown:  ✅{perf['profit_targets']} targets  🛑{perf['stop_losses']} stops  ⏰{perf['time_stops']} time")
    else:
        total_e = perf['total_entries'] if perf else 0
        lines.append(f"Trades entered: {total_e}  |  No closed trades yet — building history")
    lines.append("")

    # ── Today's entries ──────────────────────────────────────────────────────
    if today_entries:
        lines.append(f"📋 *Trades Entered Today ({len(today_entries)})*")
        for t in today_entries:
            icon  = "✅" if t.get("success") else "❌"
            strat = t.get("strategy", "?")
            ts    = t.get("executed_at", "")[:16].replace("T", " ")
            lines.append(f"{icon} *{strat}* — {t.get('symbol','?')}  `{ts}`")
            if strat == "Iron Condor":
                lines.append(f"   Profit zone: ${t.get('profit_zone_low','?')} – ${t.get('profit_zone_high','?')}  exp {t.get('expiration','?')}")
            else:
                lines.append(f"   ${t.get('short_strike','?')} / ${t.get('long_strike','?')}  exp {t.get('expiration','?')}")
            lines.append(f"   Credit: ${t.get('net_credit','?')}  |  Max loss: ${t.get('max_loss','?')}")
            lines.append(f"   Order: `{t.get('order_id','?')}` ({t.get('order_status','?')})")
            if t.get("error"):
                lines.append(f"   ⚠️ `{t['error'][:80]}`")
    else:
        lines.append("📋 *Trades Entered:* None today")
    lines.append("")

    # ── Today's exits ────────────────────────────────────────────────────────
    if today_exits:
        realized = sum(e.get("realized_pnl", 0) for e in today_exits)
        lines.append(f"🏁 *Trades Closed Today ({len(today_exits)})  |  Realized P&L: ${realized:+.2f}*")
        reason_icons = {"profit_target": "✅", "stop_loss": "🛑", "time_stop": "⏰"}
        reason_labels = {"profit_target": "Profit Target", "stop_loss": "Stop Loss", "time_stop": "Time Stop"}
        for e in today_exits:
            reason = e.get("exit_reason", "?")
            icon   = reason_icons.get(reason, "📋")
            label  = reason_labels.get(reason, reason)
            pnl    = e.get("realized_pnl", 0)
            lines.append(f"{icon} *{label}* — {e.get('strategy','?')} {e.get('symbol','?')}")
            lines.append(f"   Entry ${e.get('entry_credit','?')} → Close ${e.get('close_debit','?')}  |  *P&L ${pnl:+.2f}*")
    else:
        lines.append("🏁 *Trades Closed:* None today")
    lines.append("")

    # ── Open positions ────────────────────────────────────────────────────────
    if positions:
        lines.append(f"📂 *Open Positions ({len(positions)})*")
        for p in positions:
            qty  = p.get("quantity", 0)
            cost = p.get("cost_basis", 0)
            sym  = p.get("symbol", "?")
            lines.append(f"  {sym}  qty: {qty}  cost: ${cost:.2f}")
    else:
        lines.append("📂 *Open Positions:* None (flat)")
    lines.append("")

    # ── Next session ──────────────────────────────────────────────────────────
    lines.append("⏰ *Next scan:* 10:15 AM ET  (21:15 ICT, Tue–Thu)")
    lines.append("_Use /positions or /account for live data anytime_")

    return "\n".join(lines)

# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Generating daily summary...")
    summary = build_summary()
    print(summary)
    send_telegram(summary)

if __name__ == "__main__":
    main()
