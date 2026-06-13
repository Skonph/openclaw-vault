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

PROD_HEADERS    = {"Authorization": f"Bearer {PROD_TOKEN}",    "Accept": "application/json"}
SANDBOX_HEADERS = {"Authorization": f"Bearer {SANDBOX_TOKEN}", "Accept": "application/json"}

ACTIVE_TRADES   = SCRIPT_DIR / "active_trades.json"
TRADE_LOG       = SCRIPT_DIR / "trade_log.jsonl"
HEARTBEAT_FILE  = SCRIPT_DIR / "last_heartbeat.json"

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
    with open(ACTIVE_TRADES) as f:
        return json.load(f)

def save_active(trades):
    if TEST_MODE:
        print("  🧪 [TEST_MODE] Suppressing active_trades.json save.")
        return
    with open(ACTIVE_TRADES, "w") as f:
        json.dump(trades, f, indent=2)

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
    Submit Buy-to-Close (BTC) order — reverses open legs of the original entry.
    close_type: "limit" for profit target, "market" for stop/time exits.
    """
    strategy = trade["strategy"]
    symbol   = trade["symbol"]
    url      = f"{SANDBOX_URL}/accounts/{ACCOUNT_ID}/orders"
    qty      = str(trade.get("quantity", 1))

    if TEST_MODE:
        print(f"  🧪 [TEST_MODE] Mock submitting BTC {close_type} order (qty: {qty}) for {symbol}")
        return {"success": True, "order_id": "TEST-BTC-123", "status": "filled"}

    if strategy == "Iron Condor":
        legs = []
        if wings_to_close is None:
            # Close whatever wings are still open
            if not trade.get("put_wing_closed", False):
                legs.append((trade["put_short_symbol"], "buy_to_close"))
                legs.append((trade["put_long_symbol"], "sell_to_close"))
            if not trade.get("call_wing_closed", False):
                legs.append((trade["call_short_symbol"], "buy_to_close"))
                legs.append((trade["call_long_symbol"], "sell_to_close"))
        else:
            if "put" in wings_to_close:
                legs.append((trade["put_short_symbol"], "buy_to_close"))
                legs.append((trade["put_long_symbol"], "sell_to_close"))
            if "call" in wings_to_close:
                legs.append((trade["call_short_symbol"], "buy_to_close"))
                legs.append((trade["call_long_symbol"], "sell_to_close"))

        if not legs:
            return {"success": False, "error": "No wings to close"}

        payload = {
            "class": "multileg",
            "symbol": symbol,
            "type": "debit" if close_type == "limit" else close_type,
            "duration": "day",
        }
        for idx, (opt_sym, side) in enumerate(legs):
            payload[f"option_symbol[{idx}]"] = opt_sym
            payload[f"side[{idx}]"] = side
            payload[f"quantity[{idx}]"] = qty
    else:
        payload = {
            "class": "multileg", "symbol": symbol,
            "type": "debit" if close_type == "limit" else close_type, "duration": "day",
            "option_symbol[0]": trade["short_symbol"], "side[0]": "buy_to_close",  "quantity[0]": qty,
            "option_symbol[1]": trade["long_symbol"],  "side[1]": "sell_to_close", "quantity[1]": qty,
        }

    if close_type == "limit":
        payload["price"] = f"{debit:.2f}"

    try:
        r = requests.post(url, headers=SANDBOX_HEADERS, data=payload, timeout=15)
        try:
            resp = r.json()
        except ValueError as je:
            print(f"  [btc error] Response not JSON. HTTP status: {r.status_code}")
            print(f"  Response content: {r.text[:300]}")
            return {"success": False, "error": f"HTTP {r.status_code}: non-JSON response ({r.text[:100]})"}
    except Exception as e:
        print(f"  [btc error] {e}")
        return {"success": False, "error": str(e)}

    order_id = resp.get("order", {}).get("id", "unknown")
    status   = resp.get("order", {}).get("status", "unknown")

    if r.status_code in (200, 201) and status in ("ok", "pending", "open", "filled"):
        print(f"  ✅ BTC order submitted: {order_id} ({status})")
        return {"success": True, "order_id": str(order_id), "status": status}
    else:
        err = resp.get("errors", resp)
        print(f"  ❌ BTC order rejected: {err}")
        return {"success": False, "error": str(err)[:200]}

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

    # ── Evaluate rules (priority: time > stop > profit) ──────────────────
    exit_reason  = None
    close_type   = "limit"
    close_debit  = current
    wings_to_close = None

    if dte <= 2:
        exit_reason = "time_stop"
        close_type  = "market"
        print(f"  ⏰ TIME STOP — {dte} DTE ≤ 2, closing remaining wings at market")
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
               "partial_stop_loss_put": "🛑", "partial_stop_loss_call": "🛑"}
    labels  = {"profit_target": "Profit Target Hit 🎯", 
               "stop_loss": "Stop Loss Hit", 
               "time_stop": "Time Stop (2 DTE)",
               "partial_stop_loss_put": "Partial Stop Loss (Put Wing Closed)",
               "partial_stop_loss_call": "Partial Stop Loss (Call Wing Closed)"}
    
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
    if not trades:
        print("  No active trades to monitor.")
        return

    print(f"  {len(trades)} active trade(s) to check")

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
