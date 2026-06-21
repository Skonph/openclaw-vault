#!/usr/bin/env python3
"""
Tradier Position Monitor — Auto-Exit Manager
─────────────────────────────────────────────
Checks open spreads 3× daily and closes them when exit rules trigger.

Exit rules (Phase 1 & 2):
  ✅ 50% profit  → BTC at limit   (profit target — take the win)
  🛑 2× loss     → BTC at market  (hard stop — cap the damage)
  ⏰ 2 DTE       → BTC at market  (time stop  — avoid expiration gamma)
  🍁 Partial Exit→ BTC single wing (Iron Condors: close threatened wing, let unthreatened run)
  🔒 Profit lock → BTC at limit   (≥25% of credit captured AND DTE ≤ 21 —
                                    bank partial wins before gamma risk ramps
                                    up near expiry, even if short of the 50%
                                    target. Standard 2-leg spreads and
                                    Iron Condors with both wings still open.)

Cron (CRON_TZ=America/New_York):
  30 10,13,15 * * 1-5  /home/ubuntu/trading-bot/venv/bin/python3 /home/ubuntu/trading-bot/position_monitor.py >> /home/ubuntu/trading-bot/logs/monitor.log 2>&1

Active trades are stored in active_trades.json (written by daily_scan.py).
Exits are appended to trade_log.jsonl and sent to Telegram.
"""

import os
import sys
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
TEST_MODE       = "--test" in sys.argv
SCRIPT_DIR      = Path(__file__).parent
PROD_URL        = "https://api.tradier.com/v1"
SANDBOX_URL     = "https://sandbox.tradier.com/v1"
PROD_TOKEN      = os.getenv("TRADIER_PROD_TOKEN",    "")
SANDBOX_TOKEN   = os.getenv("TRADIER_SANDBOX_TOKEN", "")
ACCOUNT_ID      = os.getenv("TRADIER_SANDBOX_ACCOUNT","")
BOT_TOKEN       = os.getenv("TELEGRAM_BOT_TOKEN",    "")
CHAT_ID         = os.getenv("TELEGRAM_CHAT_ID",      "")

ALPACA_KEY     = os.getenv("ALPACA_API_KEY",        "")
ALPACA_SECRET  = os.getenv("ALPACA_SECRET_KEY",     "")
ALPACA_BASE    = os.getenv("ALPACA_BASE_URL",       "https://paper-api.alpaca.markets/v2")

PROD_HEADERS    = {"Authorization": f"Bearer {PROD_TOKEN}",    "Accept": "application/json"}
SANDBOX_HEADERS = {"Authorization": f"Bearer {SANDBOX_TOKEN}", "Accept": "application/json"}
ALPACA_HEADERS = {
    "APCA-API-KEY-ID":     ALPACA_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET,
    "Content-Type":        "application/json",
}

ACTIVE_TRADES   = SCRIPT_DIR / "active_trades.json"
TRADE_LOG       = SCRIPT_DIR / "trade_log.jsonl"
HEARTBEAT_FILE  = SCRIPT_DIR / "last_heartbeat.json"

# Profit-lock (Phase 2): once DTE drops to this level, bank decent partial
# profits instead of holding for the full 50% target — gamma risk ramps up
# fast in the final weeks, and capital is better redeployed into a fresh
# higher-DTE setup.
PROFIT_LOCK_DTE         = 21
PROFIT_LOCK_MIN_CAPTURE = 0.25  # require >=25% of entry credit captured first

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def is_market_open() -> bool:
    """Check Tradier market clock — only submit orders during regular hours."""
    if TEST_MODE:
        return True
    try:
        r = requests.get(f"{PROD_URL}/markets/clock",
                         headers=PROD_HEADERS, timeout=10)
        state = r.json().get("clock", {}).get("state", "closed")
        return state == "open"
    except Exception as e:
        print(f"  [market clock error] {e} — assuming open")
        return True   # fail-safe: don't block if API unreachable

def send_heartbeat():
    """Send a daily 'systems ready' message on the first monitor run each day."""
    today = date.today().isoformat()
    if HEARTBEAT_FILE.exists():
        try:
            with open(HEARTBEAT_FILE) as f:
                last = json.load(f).get("date", "")
            if last == today:
                return   # already sent today
        except Exception:
            pass

    active = load_active()
    now_et = datetime.now(timezone(timedelta(hours=-4)))
    msg = (
        f"🤖 *Tradier Bot — Daily Check-in*\n"
        f"{now_et.strftime('%a %b %d, %I:%M %p ET')}\n\n"
        f"✅ Position monitor running\n"
        f"📂 Active trades: *{len(active)}*\n"
        f"⏰ Exit checks: 10:30 AM / 1:00 PM / 3:30 PM ET\n"
        f"📊 Scan: 10:15 AM ET  _(Tue–Thu only)_"
    )
    send_telegram(msg)

    with open(HEARTBEAT_FILE, "w") as f:
        json.dump({"date": today, "sent_at": datetime.now().isoformat()}, f)

def load_active():
    if not ACTIVE_TRADES.exists():
        return []
    try:
        with open(ACTIVE_TRADES) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        # FAIL LOUD — never silently report "no active trades" when the file is
        # unreadable, because real open positions would then ride UNMANAGED
        # (no stop, no profit target). Alert and abort so the operator notices.
        msg = (f"🚨 position_monitor: active_trades.json is unreadable "
               f"({type(e).__name__}: {e}). Open positions are NOT being "
               f"monitored — fix the file immediately.")
        print(f"  {msg}")
        try:
            send_telegram(msg)
        except Exception:
            pass
        raise SystemExit(1)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    return data

def save_active(trades):
    if TEST_MODE:
        print("  🧪 [TEST_MODE] Suppressing active_trades.json save.")
        return
    with open(ACTIVE_TRADES, "w") as f:
        json.dump(trades, f, indent=2)

def _check_alpaca_paper_account():
    """Safety guard: enforce that we are trading on a paper account only."""
    if TEST_MODE:
        return
    # 1. Base URL check
    if "paper-api" not in ALPACA_BASE.lower():
        if os.getenv("LIVE_TRADING", "").lower() != "true":
            print(f"\n  🚫 LIVE TRADING BLOCKED — ALPACA_BASE_URL points to live API: {ALPACA_BASE}")
            print("  To override this for real money trading, set LIVE_TRADING=true in .env\n")
            sys.exit(1)
        else:
            print("\n  ⚠️ WARNING: LIVE TRADING IS ENABLED! Real money is at risk!\n")
            return

    # 2. Query /v2/account to confirm
    try:
        url = f"{ALPACA_BASE}/account"
        r = requests.get(url, headers=ALPACA_HEADERS, timeout=10)
        r.raise_for_status()
        acc_data = r.json()
        acc_num = acc_data.get("account_number", "")
        is_paper_acc = acc_num.startswith("PA") or acc_num.startswith("DU") or acc_num.startswith("DF")
        if not is_paper_acc:
            if os.getenv("LIVE_TRADING", "").lower() != "true":
                print(f"\n  🚫 LIVE TRADING BLOCKED — Account number {acc_num} does not appear to be a paper account.")
                print("  To override this, set LIVE_TRADING=true in .env\n")
                sys.exit(1)
            else:
                print(f"\n  ⚠️ WARNING: LIVE TRADING ACTIVE on account {acc_num}!\n")
    except Exception as e:
        print(f"\n  ⚠️ WARNING: Could not verify account paper status via API: {e}")
        if "paper-api" not in ALPACA_BASE.lower() and os.getenv("LIVE_TRADING", "").lower() != "true":
            print("  🚫 Base URL is not paper and API verification failed. Live trading blocked.")
            sys.exit(1)

def reconcile(trades):
    """
    Cross-check local active trades against actual open positions on Alpaca.
    If a trade in active_trades.json is no longer open on Alpaca, remove it.
    Also removes expired trades (DTE < 0).
    """
    if TEST_MODE:
        return trades

    # Ensure paper check
    _check_alpaca_paper_account()

    try:
        r = requests.get(f"{ALPACA_BASE}/positions", headers=ALPACA_HEADERS, timeout=10)
        r.raise_for_status()
        positions = r.json()
    except Exception as e:
        print(f"  ⚠️  [reconcile] Failed to fetch Alpaca positions: {e}")
        return trades

    # Extract all open option symbols from Alpaca positions
    open_symbols = set()
    for p in positions:
        if p.get("asset_class") == "us_option":
            open_symbols.add(p.get("symbol"))

    reconciled = []
    removed_count = 0
    today = date.today()

    for t in trades:
        # Check if expired
        try:
            exp_date = datetime.strptime(t["expiration"], "%Y-%m-%d").date()
            if exp_date < today:
                print(f"  🗑  [reconcile] Removing expired trade: {t['symbol']} (exp {t['expiration']})")
                removed_count += 1
                continue
        except Exception:
            pass

        # Check if the option legs of the trade are still open on Alpaca
        if t["strategy"] == "Iron Condor":
            legs = [t.get("put_short_symbol"), t.get("put_long_symbol"),
                    t.get("call_short_symbol"), t.get("call_long_symbol")]
        else:
            legs = [t.get("short_symbol"), t.get("long_symbol")]

        # Filter out empty legs
        legs = [l for l in legs if l]

        any_leg_open = any(l in open_symbols for l in legs)

        if any_leg_open:
            reconciled.append(t)
        else:
            print(f"  🗑  [reconcile] Removing closed trade from active_trades.json: {t['symbol']} (no legs found in Alpaca positions)")
            removed_count += 1

    if removed_count > 0:
        save_active(reconciled)

    return reconciled

def days_to_expiry(exp_str):
    return (datetime.strptime(exp_str, "%Y-%m-%d").date() - date.today()).days

def get_quotes(symbols: list) -> dict:
    """Fetch live bid/ask from Production API for a list of option symbols."""
    if not symbols:
        return {}
    if TEST_MODE:
        return {}
    try:
        r = requests.get(
            f"{PROD_URL}/markets/quotes",
            headers=PROD_HEADERS,
            params={"symbols": ",".join(symbols), "greeks": "false"},
            timeout=15,
        )
        try:
            raw = r.json().get("quotes", {}).get("quote", [])
        except ValueError as je:
            print(f"  [quotes error] Response not JSON. HTTP status: {r.status_code}")
            print(f"  Response content: {r.text[:300]}")
            return {}
        if isinstance(raw, dict):
            raw = [raw]
        return {q["symbol"]: q for q in raw}
    except Exception as e:
        print(f"  [quotes error] {e}")
        return {}

def spread_cost_to_close(quotes, short_sym, long_sym):
    """
    Cost to BTC a 2-leg spread:
      BUY back short leg at ask  (costs money)
      SELL long leg at bid       (receives money)
    Net debit = short_ask − long_bid.
    Lower = more profit already captured.
    """
    short_ask = (quotes.get(short_sym) or {}).get("ask", 0) or 0
    long_bid  = (quotes.get(long_sym)  or {}).get("bid", 0) or 0
    return round(max(0, short_ask - long_bid), 2)

def send_telegram(msg: str):
    if TEST_MODE:
        print(f"  📱 [Telegram Alert] {msg.replace('*', '')}")
        return
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        print(f"  [telegram] {e}")

def log_exit(trade, reason, close_debit, pnl, order_result):
    entry = {
        "type":         "exit",
        "date":         date.today().isoformat(),
        "exited_at":    datetime.now().isoformat(),
        "trade_id":     trade.get("trade_id", ""),
        "strategy":     trade["strategy"],
        "symbol":       trade["symbol"],
        "expiration":   trade["expiration"],
        "exit_reason":  reason,
        "entry_credit": trade["entry_credit"],
        "close_debit":  close_debit,
        "realized_pnl": round(pnl, 2),
        "order_id":     order_result.get("order_id", "?"),
        "success":      order_result.get("success", False),
    }
    if order_result.get("error"):
        entry["error"] = order_result["error"]
    
    if TEST_MODE:
        print(f"  📝 [TEST_MODE] Logging exit: {entry}")
        return

    with open(TRADE_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")

# ─── ORDER SUBMISSION ─────────────────────────────────────────────────────────

def submit_btc(trade, close_type="limit", debit=0.0, wings_to_close=None) -> dict:
    """
    Submit Buy-to-Close (BTC) order to Alpaca — reverses open legs of the original entry.
    close_type: "limit" for profit target, "market" for stop/time exits.
    """
    strategy = trade["strategy"]
    symbol   = trade["symbol"]
    qty      = str(trade.get("quantity", 1))

    if TEST_MODE:
        print(f"  🧪 [TEST_MODE] Mock submitting BTC {close_type} order (qty: {qty}) for {symbol}")
        return {"success": True, "order_id": "TEST-BTC-123", "status": "filled"}

    _check_alpaca_paper_account()

    legs_payload = []
    if strategy == "Iron Condor":
        if wings_to_close is None:
            # Close whatever wings are still open
            if not trade.get("put_wing_closed", False):
                legs_payload.append({"symbol": trade["put_short_symbol"], "ratio_qty": 1, "side": "buy",  "position_effect": "close"})
                legs_payload.append({"symbol": trade["put_long_symbol"],  "ratio_qty": 1, "side": "sell", "position_effect": "close"})
            if not trade.get("call_wing_closed", False):
                legs_payload.append({"symbol": trade["call_short_symbol"], "ratio_qty": 1, "side": "buy",  "position_effect": "close"})
                legs_payload.append({"symbol": trade["call_long_symbol"],  "ratio_qty": 1, "side": "sell", "position_effect": "close"})
        else:
            if "put" in wings_to_close:
                legs_payload.append({"symbol": trade["put_short_symbol"], "ratio_qty": 1, "side": "buy",  "position_effect": "close"})
                legs_payload.append({"symbol": trade["put_long_symbol"],  "ratio_qty": 1, "side": "sell", "position_effect": "close"})
            if "call" in wings_to_close:
                legs_payload.append({"symbol": trade["call_short_symbol"], "ratio_qty": 1, "side": "buy",  "position_effect": "close"})
                legs_payload.append({"symbol": trade["call_long_symbol"],  "ratio_qty": 1, "side": "sell", "position_effect": "close"})

        if not legs_payload:
            return {"success": False, "error": "No wings to close"}
    else:
        legs_payload = [
            {"symbol": trade["short_symbol"], "ratio_qty": 1, "side": "buy",  "position_effect": "close"},
            {"symbol": trade["long_symbol"],  "ratio_qty": 1, "side": "sell", "position_effect": "close"}
        ]

    payload = {
        "order_class":   "mleg",
        "type":          close_type,
        "time_in_force": "day",
        "qty":           qty,
        "legs":          legs_payload,
    }

    if close_type == "limit":
        payload["limit_price"] = f"{debit:.2f}"

    url = f"{ALPACA_BASE}/orders"
    try:
        r = requests.post(url, headers=ALPACA_HEADERS, json=payload, timeout=15)
        try:
            resp = r.json()
        except ValueError as je:
            print(f"  [btc error] Response not JSON. HTTP status: {r.status_code}")
            print(f"  Response content: {r.text[:300]}")
            return {"success": False, "error": f"HTTP {r.status_code}: non-JSON response ({r.text[:100]})"}
    except Exception as e:
        print(f"  [btc error] {e}")
        return {"success": False, "error": str(e)}

    order_id = resp.get("id", "unknown")
    status   = resp.get("status", "unknown")

    if r.status_code in (200, 201) and status in ("accepted", "pending_new", "accepted_for_bidding", "partially_filled", "filled", "new"):
        print(f"  ✅ BTC order submitted to Alpaca: {order_id} ({status})")
        return {"success": True, "order_id": str(order_id), "status": status}
    else:
        print(f"  ❌ BTC order rejected by Alpaca (HTTP {r.status_code}): {resp}")
        return {"success": False, "error": str(resp)[:200]}

# ─── EXIT LOGIC ───────────────────────────────────────────────────────────────

def evaluate_trade(trade, quotes) -> bool:
    """
    Check one active trade against all exit rules.
    Returns True if closed (remove from active list), False if still holding.
    """
    strategy     = trade["strategy"]
    dte          = days_to_expiry(trade["expiration"])
    entry_credit = float(trade["entry_credit"])
    profit_tgt   = float(trade["profit_target_debit"])   # 50% of credit
    stop_loss    = float(trade["stop_loss_debit"])        # 2× credit
    qty          = int(trade.get("quantity", 1))

    # Check if any wing is already closed
    put_closed = trade.get("put_wing_closed", False)
    call_closed = trade.get("call_wing_closed", False)

    # Current cost to close
    if strategy == "Iron Condor":
        put_cost = 0.0
        call_cost = 0.0
        if not put_closed:
            put_cost  = spread_cost_to_close(quotes, trade["put_short_symbol"],  trade["put_long_symbol"])
        if not call_closed:
            call_cost = spread_cost_to_close(quotes, trade["call_short_symbol"], trade["call_long_symbol"])
        current   = round(put_cost + call_cost, 2)
    else:
        current = spread_cost_to_close(quotes, trade["short_symbol"], trade["long_symbol"])

    pnl     = round((entry_credit - current) * 100 * qty, 2)
    pnl_pct = round((entry_credit - current) / entry_credit * 100, 1) if entry_credit else 0

    print(f"\n  {strategy} — {trade['symbol']}  exp {trade['expiration']}  ({dte} DTE)  [Qty: {qty}]")
    if strategy == "Iron Condor":
        print(f"  Wings: Put Wing {'CLOSED' if put_closed else f'Cost: ${put_cost:.2f}'} | Call Wing {'CLOSED' if call_closed else f'Cost: ${call_cost:.2f}'}")
    print(f"  Entry ${entry_credit:.2f}  →  Current ${current:.2f}  |  P&L ${pnl:+.2f} ({pnl_pct:+.1f}%)")
    print(f"  Targets:  profit ≤${profit_tgt:.2f}  |  stop ≥${stop_loss:.2f}")

    # ── Evaluate rules (priority: time > stop > profit > profit-lock) ────
    exit_reason  = None
    close_type   = "limit"
    close_debit  = current
    wings_to_close = None

    entered_at_str = trade.get("entered_at")
    elapsed_days = 0
    if entered_at_str:
        try:
            dt_entered = datetime.fromisoformat(entered_at_str)
            if dt_entered.tzinfo is not None:
                dt_entered = dt_entered.replace(tzinfo=None)
            elapsed_days = (datetime.now() - dt_entered).days
        except Exception as e:
            print(f"  ⚠️  [date parse error] {e}")

    # Print age info
    print(f"  Age: {elapsed_days} days (Entered: {entered_at_str})")

    if dte <= 2:
        exit_reason = "time_stop"
        close_type  = "market"
        print(f"  ⏰ TIME STOP — {dte} DTE ≤ 2, closing remaining wings at market")
    elif elapsed_days >= 14:
        exit_reason = "recycle_gate"
        close_type  = "market"
        print(f"  ♻️ RECYCLE GATE — Position open for {elapsed_days} days >= 14 days, force closing at market")
    elif strategy == "Iron Condor":
        put_credit = float(trade.get("put_credit", entry_credit / 2.0))
        call_credit = float(trade.get("call_credit", entry_credit / 2.0))
        
        # Check partial stops first if both are open
        if not put_closed and not call_closed:
            if put_cost >= 2.0 * put_credit:
                exit_reason = "partial_stop_loss_put"
                close_type = "market"
                wings_to_close = ["put"]
                close_debit = put_cost
                print(f"  🛑 PARTIAL STOP LOSS (PUT WING) — current put cost ${put_cost:.2f} >= stop ${2.0*put_credit:.2f}")
            elif call_cost >= 2.0 * call_credit:
                exit_reason = "partial_stop_loss_call"
                close_type = "market"
                wings_to_close = ["call"]
                close_debit = call_cost
                print(f"  🛑 PARTIAL STOP LOSS (CALL WING) — current call cost ${call_cost:.2f} >= stop ${2.0*call_credit:.2f}")
            elif current <= profit_tgt:
                exit_reason = "profit_target"
                close_debit = profit_tgt
                print(f"  ✅ PROFIT TARGET (COMBINED) — current ${current:.2f} <= target ${profit_tgt:.2f}")
            elif dte <= PROFIT_LOCK_DTE and current <= entry_credit * (1 - PROFIT_LOCK_MIN_CAPTURE):
                exit_reason = "profit_lock_dte"
                close_debit = current
                lock_thresh = entry_credit * (1 - PROFIT_LOCK_MIN_CAPTURE)
                print(f"  🔒 PROFIT LOCK (COMBINED, {dte} DTE ≤ {PROFIT_LOCK_DTE}) — current ${current:.2f} <= ${lock_thresh:.2f} ({PROFIT_LOCK_MIN_CAPTURE:.0%}+ of credit captured)")
        elif not put_closed:  # Only Put wing is open
            if put_cost >= 2.0 * put_credit:
                exit_reason = "stop_loss"
                close_type = "market"
                close_debit = put_cost
                print(f"  🛑 STOP LOSS (PUT WING) — current put cost ${put_cost:.2f} >= stop ${2.0*put_credit:.2f}")
            elif put_cost <= 0.50 * put_credit:
                exit_reason = "profit_target"
                close_debit = round(0.50 * put_credit, 2)
                print(f"  ✅ PROFIT TARGET (PUT WING) — current put cost ${put_cost:.2f} <= target ${close_debit:.2f}")
        elif not call_closed:  # Only Call wing is open
            if call_cost >= 2.0 * call_credit:
                exit_reason = "stop_loss"
                close_type = "market"
                close_debit = call_cost
                print(f"  🛑 STOP LOSS (CALL WING) — current call cost ${call_cost:.2f} >= stop ${2.0*call_credit:.2f}")
            elif call_cost <= 0.50 * call_credit:
                exit_reason = "profit_target"
                close_debit = round(0.50 * call_credit, 2)
                print(f"  ✅ PROFIT TARGET (CALL WING) — current call cost ${call_cost:.2f} <= target ${close_debit:.2f}")
    else:
        # Standard 2-leg spread
        if current >= stop_loss:
            exit_reason = "stop_loss"
            close_type  = "market"
            print(f"  🛑 STOP LOSS — current ${current:.2f} >= stop ${stop_loss:.2f}")
        elif current <= profit_tgt:
            exit_reason = "profit_target"
            close_debit = profit_tgt
            print(f"  ✅ PROFIT TARGET — current ${current:.2f} <= target ${profit_tgt:.2f}")
        elif dte <= PROFIT_LOCK_DTE and current <= entry_credit * (1 - PROFIT_LOCK_MIN_CAPTURE):
            exit_reason = "profit_lock_dte"
            close_debit = current
            lock_thresh = entry_credit * (1 - PROFIT_LOCK_MIN_CAPTURE)
            print(f"  🔒 PROFIT LOCK — {dte} DTE ≤ {PROFIT_LOCK_DTE} and current ${current:.2f} <= ${lock_thresh:.2f} ({PROFIT_LOCK_MIN_CAPTURE:.0%}+ of credit captured)")

    if not exit_reason:
        print(f"  → Holding. No exit rule triggered.")
        return False

    # Submit BTC order
    result = submit_btc(trade, close_type, close_debit, wings_to_close)
    if not result["success"]:
        msg = (
            f"❌ *Exit Failed — {strategy}* — {trade['symbol']}\n"
            f"Reason: {exit_reason}  |  Error: `{result.get('error','?')[:100]}`\n"
            f"_Manual intervention required_"
        )
        send_telegram(msg)
        return False

    # Calculate actual realized P&L based on contract quantity
    is_partial = False
    if exit_reason == "partial_stop_loss_put":
        trade["put_wing_closed"] = True
        is_partial = True
        put_credit = float(trade.get("put_credit", entry_credit / 2.0))
        actual_pnl = round((put_credit - close_debit) * 100 * qty, 2)
    elif exit_reason == "partial_stop_loss_call":
        trade["call_wing_closed"] = True
        is_partial = True
        call_credit = float(trade.get("call_credit", entry_credit / 2.0))
        actual_pnl = round((call_credit - close_debit) * 100 * qty, 2)
    else:
        actual_pnl = round((entry_credit - close_debit) * 100 * qty, 2)

    log_exit(trade, exit_reason, close_debit, actual_pnl, result)

    icons   = {"profit_target": "✅", "stop_loss": "🛑", "time_stop": "⏰",
               "partial_stop_loss_put": "🛑", "partial_stop_loss_call": "🛑",
               "profit_lock_dte": "🔒", "recycle_gate": "♻️"}
    labels  = {"profit_target": "Profit Target Hit 🎯",
               "stop_loss": "Stop Loss Hit",
               "time_stop": "Time Stop (2 DTE)",
               "partial_stop_loss_put": "Partial Stop Loss (Put Wing Closed)",
               "partial_stop_loss_call": "Partial Stop Loss (Call Wing Closed)",
               "profit_lock_dte": f"Profit Lock ({PROFIT_LOCK_DTE} DTE)",
               "recycle_gate": "Recycle Gate (14-day hold limit)"}
    
    icon    = icons.get(exit_reason, "📋")
    label   = labels.get(exit_reason, exit_reason)

    pnl_label = "Wing P&L" if is_partial else "Realized P&L"
    msg = (
        f"{icon} *{label}*\n\n"
        f"*{strategy}* — {trade['symbol']}\n"
        f"Exp: {trade['expiration']}  ({dte} DTE at exit)\n"
        f"Entry Credit: ${entry_credit:.2f}  →  Closed: ${close_debit:.2f}\n"
        f"*{pnl_label}: ${actual_pnl:+.2f}* (Qty: {qty})\n"
        f"Order: `{result.get('order_id','?')}`"
    )
    send_telegram(msg)

    if is_partial:
        # If both wings are now closed, the trade is fully closed
        if trade.get("put_wing_closed", False) and trade.get("call_wing_closed", False):
            return True
        return False

    return True

# ─── MOCK DIAGNOSTICS SUITE ──────────────────────────────────────────────────

def run_test_suite():
    """Run simulated exits to verify exit monitoring and order routing behavior."""
    print("🧪 Running Position Monitor Exit Diagnostics Test Suite...\n")

    test_trades = [
        {
            "trade_id": "TEST_001",
            "strategy": "Bull Put Spread",
            "symbol": "SPY",
            "expiration": (date.today() + timedelta(days=10)).isoformat(),
            "entry_credit": 0.50,
            "profit_target_debit": 0.25,
            "stop_loss_debit": 1.00,
            "short_symbol": "SPY_SHORT_PUT",
            "long_symbol": "SPY_LONG_PUT",
            "quantity": 1
        },
        {
            "trade_id": "TEST_002",
            "strategy": "Bull Put Spread",
            "symbol": "SPY",
            "expiration": (date.today() + timedelta(days=10)).isoformat(),
            "entry_credit": 0.50,
            "profit_target_debit": 0.25,
            "stop_loss_debit": 1.00,
            "short_symbol": "SPY_SHORT_PUT",
            "long_symbol": "SPY_LONG_PUT",
            "quantity": 2
        },
        {
            "trade_id": "TEST_003",
            "strategy": "Bull Put Spread",
            "symbol": "SPY",
            "expiration": (date.today() + timedelta(days=1)).isoformat(), # DTE = 1
            "entry_credit": 0.50,
            "profit_target_debit": 0.25,
            "stop_loss_debit": 1.00,
            "short_symbol": "SPY_SHORT_PUT",
            "long_symbol": "SPY_LONG_PUT",
            "quantity": 1
        },
        {
            "trade_id": "TEST_004",
            "strategy": "Iron Condor",
            "symbol": "SPY",
            "expiration": (date.today() + timedelta(days=10)).isoformat(),
            "entry_credit": 1.00,
            "put_credit": 0.50,
            "call_credit": 0.50,
            "profit_target_debit": 0.50,
            "stop_loss_debit": 2.00,
            "put_short_symbol": "SPY_PUT_SHORT",
            "put_long_symbol": "SPY_PUT_LONG",
            "call_short_symbol": "SPY_CALL_SHORT",
            "call_long_symbol": "SPY_CALL_LONG",
            "quantity": 1
        },
        {
            "trade_id": "TEST_005",
            "strategy": "Bull Put Spread",
            "symbol": "SPY",
            "expiration": (date.today() + timedelta(days=15)).isoformat(),  # DTE = 15 (<= 21)
            "entry_credit": 0.50,
            "profit_target_debit": 0.25,
            "stop_loss_debit": 1.00,
            "short_symbol": "SPY_SHORT_PUT",
            "long_symbol": "SPY_LONG_PUT",
            "quantity": 1
        },
        {
            "trade_id": "TEST_006",
            "strategy": "Bull Put Spread",
            "symbol": "SPY",
            "expiration": (date.today() + timedelta(days=15)).isoformat(),  # DTE = 15 (<= 21)
            "entry_credit": 0.50,
            "profit_target_debit": 0.25,
            "stop_loss_debit": 1.00,
            "short_symbol": "SPY_SHORT_PUT",
            "long_symbol": "SPY_LONG_PUT",
            "quantity": 1
        },
        {
            "trade_id": "TEST_007",
            "strategy": "Bull Put Spread",
            "symbol": "SPY",
            "expiration": (date.today() + timedelta(days=25)).isoformat(),  # DTE = 25 (> 21)
            "entry_credit": 0.50,
            "profit_target_debit": 0.25,
            "stop_loss_debit": 1.00,
            "short_symbol": "SPY_SHORT_PUT",
            "long_symbol": "SPY_LONG_PUT",
            "quantity": 1
        },
        {
            "trade_id": "TEST_008",
            "strategy": "Iron Condor",
            "symbol": "SPY",
            "expiration": (date.today() + timedelta(days=18)).isoformat(),  # DTE = 18 (<= 21)
            "entry_credit": 1.00,
            "put_credit": 0.50,
            "call_credit": 0.50,
            "profit_target_debit": 0.50,
            "stop_loss_debit": 2.00,
            "put_short_symbol": "SPY_PUT_SHORT",
            "put_long_symbol": "SPY_PUT_LONG",
            "call_short_symbol": "SPY_CALL_SHORT",
            "call_long_symbol": "SPY_CALL_LONG",
            "quantity": 1
        },
        {
            "trade_id": "TEST_009",
            "strategy": "Bull Put Spread",
            "symbol": "SPY",
            "expiration": (date.today() + timedelta(days=20)).isoformat(),
            "entry_credit": 0.50,
            "profit_target_debit": 0.25,
            "stop_loss_debit": 1.00,
            "short_symbol": "SPY_SHORT_PUT",
            "long_symbol": "SPY_LONG_PUT",
            "quantity": 1,
            "entered_at": (datetime.now() - timedelta(days=15)).isoformat()
        }
    ]

    # Quotes Scenario 1: Profit Target Hit
    # cost to close = short_ask - long_bid = 0.30 - 0.10 = 0.20 (<= profit target 0.25)
    quotes_profit = {
        "SPY_SHORT_PUT": {"ask": 0.30},
        "SPY_LONG_PUT": {"bid": 0.10}
    }
    print("─" * 50)
    print("▶️ TEST 1: Bull Put Spread (1 contract) — Profit Target Hit")
    closed = evaluate_trade(test_trades[0], quotes_profit)
    print(f"Result: Closed = {closed} (Expected: True)")

    # Quotes Scenario 2: Stop Loss Hit (2 contracts)
    # cost to close = 1.20 - 0.10 = 1.10 (>= stop loss 1.00)
    quotes_loss = {
        "SPY_SHORT_PUT": {"ask": 1.20},
        "SPY_LONG_PUT": {"bid": 0.10}
    }
    print("─" * 50)
    print("▶️ TEST 2: Bull Put Spread (2 contracts) — Stop Loss Hit")
    closed = evaluate_trade(test_trades[1], quotes_loss)
    print(f"Result: Closed = {closed} (Expected: True)")

    # Quotes Scenario 3: Time Stop Hit (DTE = 1)
    quotes_normal = {
        "SPY_SHORT_PUT": {"ask": 0.45},
        "SPY_LONG_PUT": {"bid": 0.10}
    }
    print("─" * 50)
    print("▶️ TEST 3: Bull Put Spread — Time Stop Hit (DTE <= 2)")
    closed = evaluate_trade(test_trades[2], quotes_normal)
    print(f"Result: Closed = {closed} (Expected: True)")

    # Quotes Scenario 4: Iron Condor — Normal Hold
    quotes_ic_normal = {
        "SPY_PUT_SHORT": {"ask": 0.40},
        "SPY_PUT_LONG": {"bid": 0.10},
        "SPY_CALL_SHORT": {"ask": 0.40},
        "SPY_CALL_LONG": {"bid": 0.10}
    }
    print("─" * 50)
    print("▶️ TEST 4: Iron Condor — Holding")
    closed = evaluate_trade(test_trades[3], quotes_ic_normal)
    print(f"Result: Closed = {closed} (Expected: False)")

    # Quotes Scenario 5: Iron Condor — Partial Put Stop Loss Hit
    # put_cost = 1.10 - 0.05 = 1.05 (>= 2x put_credit 0.50)
    # call_cost = 0.20 - 0.10 = 0.10
    quotes_ic_partial_stop = {
        "SPY_PUT_SHORT": {"ask": 1.10},
        "SPY_PUT_LONG": {"bid": 0.05},
        "SPY_CALL_SHORT": {"ask": 0.20},
        "SPY_CALL_LONG": {"bid": 0.10}
    }
    print("─" * 50)
    print("▶️ TEST 5: Iron Condor — Partial Put Stop Loss Hit")
    ic_trade = test_trades[3].copy() # Reset condor trade
    closed = evaluate_trade(ic_trade, quotes_ic_partial_stop)
    print(f"Result: Closed = {closed} (Expected: False)")
    print(f"Wing Status after partial close: Put closed = {ic_trade.get('put_wing_closed')}, Call closed = {ic_trade.get('call_wing_closed')}")

    # Quotes Scenario 6: Remaining Wing Profit Target Hit
    # put is closed. call_cost = 0.15 - 0.10 = 0.05 (<= 0.5x call_credit 0.50)
    quotes_ic_remaining_profit = {
        "SPY_CALL_SHORT": {"ask": 0.15},
        "SPY_CALL_LONG": {"bid": 0.10}
    }
    print("─" * 50)
    print("▶️ TEST 6: Iron Condor — Remaining Call Wing Profit Target Hit")
    closed = evaluate_trade(ic_trade, quotes_ic_remaining_profit)
    print(f"Result: Closed = {closed} (Expected: True)")

    # Quotes Scenario 7: Profit Lock — 25%+ captured, DTE <= 21, below 50% target
    # cost to close = 0.40 - 0.05 = 0.35 (entry 0.50 * 0.75 = 0.375 -> 0.35 <= 0.375, > profit_tgt 0.25)
    quotes_profit_lock = {
        "SPY_SHORT_PUT": {"ask": 0.40},
        "SPY_LONG_PUT": {"bid": 0.05}
    }
    print("─" * 50)
    print("▶️ TEST 7: Bull Put Spread (15 DTE) — Profit Lock Triggered (25%+ captured)")
    closed = evaluate_trade(test_trades[4], quotes_profit_lock)
    print(f"Result: Closed = {closed} (Expected: True)")

    # Quotes Scenario 8: DTE <= 21 but only ~10% captured -> below lock threshold, holds
    # cost to close = 0.50 - 0.05 = 0.45 (entry 0.50 * 0.75 = 0.375 -> 0.45 > 0.375)
    quotes_profit_lock_below_threshold = {
        "SPY_SHORT_PUT": {"ask": 0.50},
        "SPY_LONG_PUT": {"bid": 0.05}
    }
    print("─" * 50)
    print("▶️ TEST 8: Bull Put Spread (15 DTE) — Below Profit Lock Threshold, Holding")
    closed = evaluate_trade(test_trades[5], quotes_profit_lock_below_threshold)
    print(f"Result: Closed = {closed} (Expected: False)")

    # Quotes Scenario 9: 25%+ captured but DTE > 21 -> profit lock does NOT fire, holds
    # cost to close = 0.35 - 0.05 = 0.30 (entry 0.50 * 0.75 = 0.375 -> 0.30 <= 0.375, but DTE 25 > 21)
    quotes_profit_lock_dte_too_high = {
        "SPY_SHORT_PUT": {"ask": 0.35},
        "SPY_LONG_PUT": {"bid": 0.05}
    }
    print("─" * 50)
    print("▶️ TEST 9: Bull Put Spread (25 DTE) — Captured 25%+ but DTE > 21, Holding")
    closed = evaluate_trade(test_trades[6], quotes_profit_lock_dte_too_high)
    print(f"Result: Closed = {closed} (Expected: False)")

    # Quotes Scenario 10: Iron Condor — Combined Profit Lock at 18 DTE
    # put_cost = 0.40 - 0.05 = 0.35, call_cost = 0.40 - 0.05 = 0.35, combined = 0.70
    # entry 1.00 * 0.75 = 0.75 -> 0.70 <= 0.75, > profit_tgt 0.50
    quotes_ic_profit_lock = {
        "SPY_PUT_SHORT": {"ask": 0.40},
        "SPY_PUT_LONG": {"bid": 0.05},
        "SPY_CALL_SHORT": {"ask": 0.40},
        "SPY_CALL_LONG": {"bid": 0.05}
    }
    print("─" * 50)
    print("▶️ TEST 10: Iron Condor (18 DTE) — Combined Profit Lock Triggered")
    closed = evaluate_trade(test_trades[7], quotes_ic_profit_lock)
    print(f"Result: Closed = {closed} (Expected: True)")
    print("─" * 50)
    print("▶️ TEST 11: Bull Put Spread (15 days old) — Recycle Gate Triggered")
    closed = evaluate_trade(test_trades[8], quotes_normal)
    print(f"Result: Closed = {closed} (Expected: True)")
    print("─" * 50)
    print("\n✅ Position monitor test diagnostics complete.")

# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    now_et = datetime.now(timezone(timedelta(hours=-4)))
    print(f"\n{'═'*55}")
    print(f"  POSITION MONITOR — {now_et.strftime('%Y-%m-%d %H:%M ET')}")
    print(f"{'═'*55}")

    # Heartbeat on first run of the day
    if not TEST_MODE:
        send_heartbeat()

    # Only submit exit orders during regular market hours
    if not is_market_open():
        print("  Market is closed — skipping exit checks (no orders submitted).")
        return

    if TEST_MODE:
        run_test_suite()
        return

    trades = load_active()
    if trades:
        trades = reconcile(trades)

    if not trades:
        print("  No active trades to monitor.")
        return

    print(f"  {len(trades)} active trade(s) to check (reconciled)")

    # Collect all option symbols for one batch quote call
    symbols = []
    for t in trades:
        if t["strategy"] == "Iron Condor":
            symbols += [t.get("put_short_symbol",""), t.get("put_long_symbol",""),
                        t.get("call_short_symbol",""), t.get("call_long_symbol","")]
        else:
            symbols += [t.get("short_symbol",""), t.get("long_symbol","")]
    symbols = [s for s in symbols if s]

    quotes = get_quotes(symbols)

    remaining = []
    for trade in trades:
        closed = evaluate_trade(trade, quotes)
        if not closed:
            remaining.append(trade)

    save_active(remaining)
    n_closed = len(trades) - len(remaining)
    print(f"\n  Done.  Closed: {n_closed}  Still open: {len(remaining)}")
    print(f"{'═'*55}\n")

if __name__ == "__main__":
    main()
