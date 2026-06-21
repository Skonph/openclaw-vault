#!/usr/bin/env python3
"""
Tradier Telegram Bot — Skon's $2K Paper Trading POC
────────────────────────────────────────────────────
Runs on Ubuntu (ubuntu@43.156.9.185) as a systemd service.
Long-polls Telegram for commands. Trades execute autonomously — no approval needed.

Commands:
  /scan      — Run morning scan + auto-execute trade
  /positions — Open positions + unrealized P&L
  /account   — Account balance summary
  /log       — Today's trade activity log
  /test      — Scan with mock data (no API calls, no execution)
  /status    — Bot status + last scan time
  /help      — Show this menu

Security: only responds to messages from TELEGRAM_CHAT_ID in .env
"""

import os
import sys
import json
import time
import subprocess
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─── CONFIG ──────────────────────────────────────────────────────────────────
SCRIPT_DIR         = Path(__file__).parent
BOT_TOKEN          = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID            = str(os.getenv("TELEGRAM_CHAT_ID", ""))
SANDBOX_TOKEN      = os.getenv("TRADIER_SANDBOX_TOKEN", "")
SANDBOX_ACCOUNT    = os.getenv("TRADIER_SANDBOX_ACCOUNT", "")
SANDBOX_URL        = "https://sandbox.tradier.com/v1"

PENDING_TRADE_FILE = SCRIPT_DIR / "pending_trade.json"
LAST_SCAN_FILE     = SCRIPT_DIR / "last_scan.json"
VENV_PYTHON        = SCRIPT_DIR / "venv" / "bin" / "python3"
PYTHON             = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
DAILY_SCAN         = str(SCRIPT_DIR / "daily_scan.py")

TELEGRAM_API       = f"https://api.telegram.org/bot{BOT_TOKEN}"
MAX_MSG_LEN        = 4000   # Telegram limit is 4096; leave headroom

# ─── TELEGRAM HELPERS ────────────────────────────────────────────────────────

def send(text: str, parse_mode: str = "Markdown") -> None:
    """Send message to the authorized chat. Splits on Telegram's 4096-char limit."""
    # Split into chunks — never cut inside a code block
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > MAX_MSG_LEN:
            if current:
                chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)

    for chunk in chunks:
        try:
            requests.post(f"{TELEGRAM_API}/sendMessage", json={
                "chat_id":    CHAT_ID,
                "text":       chunk,
                "parse_mode": parse_mode,
            }, timeout=10)
        except Exception as e:
            print(f"[send error] {e}")

def send_plain(text: str) -> None:
    send(text, parse_mode="")

def get_updates(offset: int = 0, timeout: int = 30) -> list:
    """Long-poll Telegram for new messages."""
    try:
        r = requests.get(f"{TELEGRAM_API}/getUpdates", params={
            "offset":          offset,
            "timeout":         timeout,
            "allowed_updates": ["message"],
        }, timeout=timeout + 5)
        return r.json().get("result", [])
    except Exception as e:
        print(f"[getUpdates error] {e}")
        return []

# ─── SCAN RUNNER ─────────────────────────────────────────────────────────────

def run_scan(extra_args: list = None) -> str:
    """Run daily_scan.py, return full stdout+stderr output."""
    cmd = [PYTHON, DAILY_SCAN] + (extra_args or [])
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(SCRIPT_DIR),
        timeout=120,    # 2-min cap — options chains can be slow
    )
    output = result.stdout
    if result.returncode != 0 and result.stderr:
        output += f"\n\n[stderr]\n{result.stderr}"
    return output

def format_for_telegram(raw: str) -> str:
    """Wrap scan output in a code block for monospace display."""
    return f"```\n{raw[:MAX_MSG_LEN]}\n```"

# ─── ORDER EXECUTION ─────────────────────────────────────────────────────────

def execute_pending_trade() -> str:
    """
    Read pending_trade.json and POST the order to Tradier sandbox.
    Returns a result message.
    """
    if not PENDING_TRADE_FILE.exists():
        return "⚠️ No pending trade found. Run /scan first."

    with open(PENDING_TRADE_FILE) as f:
        trade = json.load(f)

    payload   = trade.get("order_payload", {})
    meta      = trade.get("meta", {})
    account   = SANDBOX_ACCOUNT or trade.get("account_id", "")

    if not account or not SANDBOX_TOKEN:
        return "❌ Missing TRADIER_SANDBOX_ACCOUNT or TRADIER_SANDBOX_TOKEN in .env"

    # POST order to sandbox
    url     = f"{SANDBOX_URL}/accounts/{account}/orders"
    headers = {"Authorization": f"Bearer {SANDBOX_TOKEN}", "Accept": "application/json"}
    try:
        r = requests.post(url, data=payload, headers=headers, timeout=15)
        try:
            resp = r.json()
        except ValueError as je:
            return f"❌ Order submission failed: HTTP {r.status_code} — non-JSON response: {r.text[:100]}"
    except Exception as e:
        return f"❌ Order submission failed: {e}"

    # Parse response
    order_id = resp.get("order", {}).get("id", "unknown")
    status   = resp.get("order", {}).get("status", "unknown")

    if r.status_code in (200, 201) and status in ("ok", "pending", "open", "filled"):
        # Archive executed trade
        PENDING_TRADE_FILE.rename(SCRIPT_DIR / f"executed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        strategy = meta.get("strategy", "Bull Put Spread")
        if strategy == "Iron Condor":
            legs = (
                f"PUT:  SELL {meta.get('put_short_symbol','?')} / BUY {meta.get('put_long_symbol','?')}\n"
                f"CALL: SELL {meta.get('call_short_symbol','?')} / BUY {meta.get('call_long_symbol','?')}\n"
                f"Profit zone: ${meta.get('profit_zone_low','?')} – ${meta.get('profit_zone_high','?')}\n"
            )
        else:
            legs = (
                f"SHORT: SELL {meta.get('short_symbol','?')} @ ${meta.get('short_bid','?')}\n"
                f"LONG:  BUY  {meta.get('long_symbol','?')} @ ${meta.get('long_ask','?')}\n"
            )
        return (
            f"✅ *Order submitted!*\n\n"
            f"Order ID: `{order_id}`\n"
            f"Status:   `{status}`\n\n"
            f"*{strategy}*\n"
            f"{legs}"
            f"Credit: ${meta.get('net_credit','?')} | Max loss: ${meta.get('max_loss','?')}\n\n"
            f"Exit rules saved. Monitor with /positions"
        )
    else:
        err = resp.get("errors", resp)
        return f"❌ Order rejected (HTTP {r.status_code}):\n```{json.dumps(err, indent=2)[:500]}```"

# ─── COMMAND HANDLERS ────────────────────────────────────────────────────────

def cmd_scan() -> None:
    send("🔄 Running scan \\+ auto\\-executing trade\\.\\.\\. \\(~30 sec\\)", parse_mode="MarkdownV2")
    output = run_scan(["--no-notify"])   # bot displays output; daily_scan.py auto-executes internally
    send(format_for_telegram(output))
    with open(LAST_SCAN_FILE, "w") as f:
        json.dump({"ts": datetime.now(timezone.utc).isoformat(), "lines": len(output.splitlines())}, f)

def cmd_log() -> None:
    """Show today's trade activity from trade_log.jsonl."""
    log_path = SCRIPT_DIR / "trade_log.jsonl"
    if not log_path.exists():
        send("📋 No trade log yet\\. Trades appear here after the first auto\\-execution\\.", parse_mode="MarkdownV2")
        return

    from datetime import date
    today = date.today().isoformat()
    trades = []
    with open(log_path) as f:
        for line in f:
            try:
                t = json.loads(line.strip())
                if t.get("date") == today:
                    trades.append(t)
            except Exception:
                pass

    entries = [t for t in trades if t.get("type") != "exit"]
    exits   = [t for t in trades if t.get("type") == "exit"]

    if not entries and not exits:
        send(f"📋 No trades logged today \\({today}\\)\\.", parse_mode="MarkdownV2")
        return

    lines = [f"📋 *Trade Log — {today}*\n"]

    if entries:
        lines.append(f"*Entries ({len(entries)})*")
        for t in entries:
            icon  = "✅" if t.get("success") else "❌"
            strat = t.get("strategy", "?")
            lines.append(f"{icon} *{strat}* — {t.get('symbol','?')}")
            if strat == "Iron Condor":
                lines.append(f"Zone: ${t.get('profit_zone_low','?')}–${t.get('profit_zone_high','?')}  exp {t.get('expiration','?')}")
            else:
                lines.append(f"${t.get('short_strike','?')}/${t.get('long_strike','?')}  exp {t.get('expiration','?')}")
            lines.append(f"Credit: ${t.get('net_credit','?')} | Max loss: ${t.get('max_loss','?')}")
            lines.append(f"Order: `{t.get('order_id','?')}` ({t.get('order_status','?')})")
            if t.get("error"):
                lines.append(f"⚠️ `{t['error'][:80]}`")
            lines.append("")

    if exits:
        realized = sum(e.get("realized_pnl", 0) for e in exits)
        lines.append(f"*Exits ({len(exits)})  |  Realized P&L: ${realized:+.2f}*")
        icons  = {"profit_target": "✅", "stop_loss": "🛑", "time_stop": "⏰"}
        labels = {"profit_target": "Profit Target", "stop_loss": "Stop Loss", "time_stop": "Time Stop"}
        for e in exits:
            reason = e.get("exit_reason", "?")
            lines.append(f"{icons.get(reason,'📋')} *{labels.get(reason, reason)}* — {e.get('strategy','?')} {e.get('symbol','?')}")
            lines.append(f"Entry ${e.get('entry_credit','?')} → Close ${e.get('close_debit','?')}  |  *P&L ${e.get('realized_pnl',0):+.2f}*")
            lines.append("")

    send("\n".join(lines))

def cmd_health() -> None:
    """Show system health: bot uptime, last scan, active trades, cron logs."""
    import os
    from datetime import date

    lines = ["🩺 *System Health Check*\n"]

    # Last scan
    if LAST_SCAN_FILE.exists():
        with open(LAST_SCAN_FILE) as f:
            d = json.load(f)
        last_dt = datetime.fromisoformat(d["ts"]).astimezone(timezone(timedelta(hours=-4)))
        lines.append(f"📊 Last scan:   `{last_dt.strftime('%Y-%m-%d %H:%M ET')}`")
    else:
        lines.append("📊 Last scan:   `never`")

    # Active trades
    active_path = SCRIPT_DIR / "active_trades.json"
    if active_path.exists():
        with open(active_path) as f:
            active = json.load(f)
        lines.append(f"📂 Active trades: `{len(active)}/2`")
        for t in active:
            lines.append(f"   • {t['strategy']} {t['symbol']} exp {t['expiration']}")
    else:
        lines.append("📂 Active trades: `0/2`")

    # Heartbeat
    hb_path = SCRIPT_DIR / "last_heartbeat.json"
    if hb_path.exists():
        with open(hb_path) as f:
            hb = json.load(f)
        lines.append(f"🤖 Last heartbeat: `{hb.get('date','?')}`")
    else:
        lines.append("🤖 Heartbeat: `not yet sent`")

    # Log sizes
    log_dir = SCRIPT_DIR / "logs"
    for logname in ["cron.log", "monitor.log", "summary.log"]:
        lp = log_dir / logname
        if lp.exists():
            kb = lp.stat().st_size // 1024
            lines.append(f"📄 {logname}: `{kb} KB`")

    lines.append(f"\n_Server time: {datetime.now(timezone(timedelta(hours=7))).strftime('%Y-%m-%d %H:%M ICT')}_")
    send("\n".join(lines))


def cmd_reconcile() -> None:
    """Cross-check active_trades.json vs actual Tradier positions. Remove expired records."""
    from datetime import date
    active_path = SCRIPT_DIR / "active_trades.json"

    # Load and clean expired records
    active = []
    if active_path.exists():
        with open(active_path) as f:
            active = json.load(f)

    today = date.today()
    before = len(active)
    active = [t for t in active
              if datetime.strptime(t["expiration"], "%Y-%m-%d").date() >= today]
    expired_removed = before - len(active)

    if expired_removed:
        with open(active_path, "w") as f:
            json.dump(active, f, indent=2)

    lines = ["🔄 *Reconciliation*\n"]
    lines.append(f"*Monitoring {len(active)} trade(s):*")
    if active:
        for t in active:
            dte = (datetime.strptime(t["expiration"], "%Y-%m-%d").date() - today).days
            lines.append(
                f"  • {t['strategy']} {t['symbol']}  exp {t['expiration']}  ({dte} DTE)\n"
                f"    Entry: ${t['entry_credit']}  |  Target: ${t['profit_target_debit']}  |  Stop: ${t['stop_loss_debit']}"
            )
    else:
        lines.append("  _None_")

    if expired_removed:
        lines.append(f"\n🗑 Removed {expired_removed} expired record(s) from active\\_trades.json")

    lines.append("\n*Fetching live Tradier positions...*")
    send("\n".join(lines))

    # Show actual Tradier positions for visual comparison
    output = run_scan(["--positions", "--no-notify"])
    send(format_for_telegram(output))


def cmd_positions() -> None:
    send("🔄 Checking positions\\.\\.\\.", parse_mode="MarkdownV2")
    output = run_scan(["--positions"])
    send(format_for_telegram(output))

def cmd_account() -> None:
    send("🔄 Fetching account\\.\\.\\.", parse_mode="MarkdownV2")
    output = run_scan(["--account"])
    send(format_for_telegram(output))

def cmd_test() -> None:
    send("🔄 Running test scan \\(mock data\\)\\.\\.\\.", parse_mode="MarkdownV2")
    output = run_scan(["--test", "--no-notify"])   # suppress Telegram inside daily_scan.py
    send(format_for_telegram(output))

def cmd_status() -> None:
    now_et = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-4)))
    last = "never"
    if LAST_SCAN_FILE.exists():
        with open(LAST_SCAN_FILE) as f:
            d = json.load(f)
        last_dt = datetime.fromisoformat(d["ts"]).astimezone(timezone(timedelta(hours=-4)))
        last = last_dt.strftime("%Y-%m-%d %H:%M ET")

    pending = "yes" if PENDING_TRADE_FILE.exists() else "none"
    send(
        f"🤖 *Tradier Bot Status*\n\n"
        f"Server time: `{now_et.strftime('%Y-%m-%d %H:%M ET')}`\n"
        f"Last scan:   `{last}`\n"
        f"Pending trade: `{pending}`\n"
        f"Python: `{PYTHON}`\n"
        f"Script: `{DAILY_SCAN}`"
    )

def cmd_help() -> None:
    send(
        "📊 *Tradier Bot — Commands*\n\n"
        "/scan — Morning scan \\+ auto\\-execute trade\n"
        "/positions — Open positions \\+ P&L\n"
        "/account — Account balance\n"
        "/log — Today's entries \\+ exits \\+ P&L\n"
        "/reconcile — Sync active trades vs Tradier\n"
        "/health — System health check\n"
        "/test — Scan with mock data \\(no execution\\)\n"
        "/status — Bot status \\+ last scan time\n"
        "/help — This menu\n\n"
        "_Autonomous\\. Max 2 positions\\. Daily summary 8:00 AM ICT\\._",
        parse_mode="MarkdownV2"
    )

# ─── DISPATCH ────────────────────────────────────────────────────────────────

COMMANDS = {
    "/scan":       cmd_scan,
    "/run":        cmd_scan,
    "/positions":  cmd_positions,
    "/pos":        cmd_positions,
    "/account":    cmd_account,
    "/acc":        cmd_account,
    "/log":        cmd_log,
    "/activity":   cmd_log,
    "/reconcile":  cmd_reconcile,
    "/health":     cmd_health,
    "/test":       cmd_test,
    "/status":     cmd_status,
    "/help":       cmd_help,
    "/start":      cmd_help,
}

def handle(text: str) -> None:
    """Dispatch a command string to the right handler."""
    # Strip bot username suffix (e.g. /scan@MyBot → /scan)
    cmd = text.strip().lower().split()[0].split("@")[0]
    handler = COMMANDS.get(cmd)
    if handler:
        handler()
    else:
        send(f"Unknown command: `{cmd}`\nType /help for the menu\\.", parse_mode="MarkdownV2")

# ─── MAIN LOOP ───────────────────────────────────────────────────────────────

def main() -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print("ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env")
        sys.exit(1)

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Tradier bot started — chat {CHAT_ID}")
    send(
        "🤖 *Tradier bot online*\n"
        "Paper trading POC — $2K benchmark\n\n"
        "Type /help to see available commands\\.",
        parse_mode="MarkdownV2"
    )

    offset = 0
    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                msg     = update.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text    = msg.get("text", "")

                # Security: ignore anyone who isn't the authorized user
                if chat_id != CHAT_ID:
                    print(f"[ignored] message from unauthorized chat {chat_id}")
                    continue

                if not text.startswith("/"):
                    continue    # ignore non-command messages

                print(f"[cmd] {text.strip()}")
                try:
                    handle(text)
                except subprocess.TimeoutExpired:
                    send("⏰ Scan timed out after 2 minutes\\. Try again\\.", parse_mode="MarkdownV2")
                except Exception as e:
                    send(f"❌ Error: `{e}`")

        except KeyboardInterrupt:
            print("\nBot stopped.")
            break
        except Exception as e:
            print(f"[main loop error] {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
