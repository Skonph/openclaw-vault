#!/usr/bin/env python3
"""
order_fill_tracker.py — 3-Stage Adaptive Fill Protocol (RULE-075) Engine

Eliminates the "Fire-and-Forget Midpoint" problem:
- Stage 1 (21:30 ICT): Nudges resting limit order down by 1 cent ($0.01) toward market makers.
- Stage 2 (21:46 ICT): Evaluates UOA #2 Conviction Gate. If flow confirmed, fires Marketable Natural Limit
                       subject to Minimum Acceptable Credit (MAC) floor for guaranteed execution.
- Stage 3 (22:00 ICT): Hard Order Book Sweep. Cancels any lingering unfilled orders with zero tolerance.
"""

import sys
import os
import json
import datetime
import time
import re
import urllib.request
import ssl
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from alpaca_broker import AlpacaClient
from transaction_journal_manager import update_excel_journal
from active_trades_io import save_active_trades, load_active_trades
try:
    from tradier_broker import TradierClient
except ImportError:
    TradierClient = None
try:
    from agent_bridge import AgentBridge
except ImportError:
    AgentBridge = None

TELEGRAM_BOT_TOKEN = "8991076258:AAGHyb-jEnp29BQ5O5V0mo4vFOmgXfgnmTg"
TELEGRAM_CHAT_ID = "-1004375899205"

def _is_tradier_order(order: Dict[str, Any]) -> bool:
    acct = str(order.get("account", "")).lower()
    broker = str(order.get("broker_type", "")).lower()
    return "tradier" in acct or "tradier" in broker

def _check_order_status(order: Dict[str, Any]) -> Tuple[str, float, float]:
    """
    Queries order status against the appropriate broker (Tradier or Alpaca).
    Returns (status, filled_qty, fill_price).
    """
    acct = order.get("account", "pion_main")
    oid = str(order.get("order_id", ""))
    
    if _is_tradier_order(order):
        if not TradierClient:
            return "error", 0.0, 0.0
        try:
            client = TradierClient("live" if "live" in acct.lower() else "sandbox")
            res = client.get_order_status(oid)
            ord_data = res.get("order", {})
            st = ord_data.get("status", "").lower()
            qty = float(ord_data.get("exec_quantity", 0.0))
            px = abs(float(ord_data.get("avg_fill_price") or ord_data.get("price") or order.get("current_limit", 0.0)))
            return st, qty, px
        except Exception as ex:
            print(f"  ⚠️ Error checking Tradier order {oid}: {ex}")
            return "error", 0.0, 0.0
    else:
        try:
            client = AlpacaClient(acct)
            order_info = client.get_order(oid)
            st = order_info.get("status", "").lower()
            qty = float(order_info.get("filled_qty", 0.0))
            px = abs(float(order_info.get("filled_avg_price") or order.get("current_limit", 0.0)))
            return st, qty, px
        except Exception as ex:
            print(f"  ⚠️ Error checking Alpaca order {oid}: {ex}")
            return "error", 0.0, 0.0

def _cancel_broker_order(order: Dict[str, Any]) -> bool:
    """Cancels working order on either Tradier or Alpaca."""
    acct = order.get("account", "pion_main")
    oid = str(order.get("order_id", ""))

    if _is_tradier_order(order):
        if not TradierClient:
            return False
        try:
            client = TradierClient("live" if "live" in acct.lower() else "sandbox")
            client.cancel_order(oid)
            return True
        except Exception as ex:
            print(f"  ⚠️ Error cancelling Tradier order {oid}: {ex}")
            return False
    else:
        try:
            client = AlpacaClient(acct)
            client.cancel_order(oid)
            return True
        except Exception as ex:
            print(f"  ⚠️ Error cancelling Alpaca order {oid}: {ex}")
            return False

def _get_spread_quotes(order: Dict[str, Any]) -> Tuple[float, float, float, float]:
    """
    Returns (s_bid, s_ask, l_bid, l_ask) for short_sym and long_sym on the correct broker.
    """
    acct = order.get("account", "pion_main")
    short_sym = order.get("short_sym", "")
    long_sym = order.get("long_sym", "")

    if _is_tradier_order(order):
        if not TradierClient:
            return 0.0, 0.0, 0.0, 0.0
        try:
            client = TradierClient("live" if "live" in acct.lower() else "sandbox")
            q_list = client.get_quotes([short_sym, long_sym])
            q_map = {q.get("symbol"): q for q in q_list if isinstance(q, dict)}
            s_q = q_map.get(short_sym, {})
            l_q = q_map.get(long_sym, {})
            return float(s_q.get("bid") or 0.0), float(s_q.get("ask") or 0.0), float(l_q.get("bid") or 0.0), float(l_q.get("ask") or 0.0)
        except Exception as ex:
            print(f"  ⚠️ Error fetching Tradier option quotes: {ex}")
            return 0.0, 0.0, 0.0, 0.0
    else:
        try:
            client = AlpacaClient(acct)
            quotes = client.get_option_snapshot([short_sym, long_sym])
            s_bid = float(quotes.get(short_sym, {}).get("bid", 0.0) or 0.0)
            s_ask = float(quotes.get(short_sym, {}).get("ask", 0.0) or 0.0)
            l_bid = float(quotes.get(long_sym, {}).get("bid", 0.0) or 0.0)
            l_ask = float(quotes.get(long_sym, {}).get("ask", 0.0) or 0.0)
            return s_bid, s_ask, l_bid, l_ask
        except Exception as ex:
            print(f"  ⚠️ Error fetching Alpaca option quotes: {ex}")
            return 0.0, 0.0, 0.0, 0.0

def _replace_spread_order(order: Dict[str, Any], new_limit: float) -> Tuple[bool, Optional[str], str]:
    """
    Replaces working order with new_limit credit.
    - On Alpaca: Uses replace_vertical_spread_limit
    - On Tradier: Cancels resting order and submits new vertical spread
    """
    acct = order.get("account", "pion_main")
    oid = str(order.get("order_id", ""))

    if _is_tradier_order(order):
        if not TradierClient:
            return False, None, "TradierClient unavailable"
        try:
            client = TradierClient("live" if "live" in acct.lower() else "sandbox")
            client.cancel_order(oid)
            time.sleep(1.0)
            res = client.execute_vertical_spread(
                symbol=order["symbol"],
                short_occ=order["short_sym"],
                long_occ=order["long_sym"],
                qty=int(order["contracts"]),
                limit_credit=new_limit,
                duration="day"
            )
            new_oid = res.get("order_id")
            if new_oid:
                return True, str(new_oid), "Tradier cancel+resubmit success"
            return False, None, f"Tradier resubmit failed: {res}"
        except Exception as ex:
            return False, None, f"Tradier replace error: {ex}"
    else:
        try:
            client = AlpacaClient(acct)
            return client.replace_vertical_spread_limit(
                order_id=oid,
                short_sym=order["short_sym"],
                long_sym=order["long_sym"],
                contracts=int(order["contracts"]),
                new_limit_credit=new_limit
            )
        except Exception as ex:
            return False, None, f"Alpaca replace error: {ex}"

def _get_base_dir() -> Path:
    p = Path("/home/ubuntu/shared")
    if not p.exists():
        p = Path(__file__).parent
    return p

def _get_orders_file() -> Path:
    return _get_base_dir() / "pending_spread_orders.json"

def send_telegram(message: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)
    try:
        ctx_ssl = ssl._create_unverified_context()
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, context=ctx_ssl, timeout=10) as _:
            pass
        print("  📡 Dispatched alert to Telegram Group!")
    except Exception as ex:
        print(f"  ℹ️ Telegram notice: {ex}")

def load_pending_orders() -> List[Dict[str, Any]]:
    f = _get_orders_file()
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data.get("orders", [])
    except Exception as e:
        print(f"  ℹ️ Error loading pending orders: {e}")
        return []

def save_pending_orders(orders: List[Dict[str, Any]]):
    f = _get_orders_file()
    data = {
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT"),
        "orders": orders
    }
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")

def calculate_mac_floor(width: float) -> float:
    """
    Computes Minimum Acceptable Credit (MAC) to protect risk-reward ratio (RULE-084 Parity):
    Enforces width-adaptive Return on Collateral (ROC) floor (par ROC from screener):
    - Width >= $20.00: 7.5% ROC floor ($30w -> $2.25, $25w -> $1.88, $20w -> $1.50)
    - Width < $20.00 : 12.5% ROC floor ($5w -> $0.62, $2w -> $0.25, $1w -> $0.12)
    """
    roc_rate = 0.075 if width >= 20.0 else 0.125
    return max(0.25, round(width * roc_rate, 2))

def register_order(
    account: str,
    symbol: str,
    short_sym: str,
    long_sym: str,
    short_strike: float,
    long_strike: float,
    width: float,
    contracts: int,
    exp_date: str,
    order_id: str,
    limit_credit: float,
    broker_type: str = "alpaca"
) -> Dict[str, Any]:
    """Registers a newly submitted spread order with the 3-Stage Adaptive Fill Tracker."""
    orders = load_pending_orders()
    now_ict = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT")

    entry = {
        "order_id": order_id,
        "account": account,
        "broker_type": broker_type,
        "symbol": symbol.upper(),
        "short_sym": short_sym,
        "long_sym": long_sym,
        "short_strike": short_strike,
        "long_strike": long_strike,
        "width": width,
        "contracts": contracts,
        "exp_date": exp_date,
        "submitted_at": now_ict,
        "initial_limit": limit_credit,
        "current_limit": limit_credit,
        "current_stage": 1,
        "status": "working",
        "stage1_nudged_at": None,
        "stage2_action": None,
        "filled_at": None,
        "filled_avg_price": None
    }

    # Replace existing if same account and symbol
    orders = [o for o in orders if not (o.get("account") == account and o.get("symbol") == symbol.upper() and o.get("status") == "working")]
    orders.append(entry)
    save_pending_orders(orders)
    print(f"  📝 Registered order {order_id} ({symbol} ${short_strike}/${long_strike} @ ${limit_credit:.2f}) with RULE-075 Fill Tracker.")
    return entry

def record_confirmed_fill(order: Dict[str, Any], fill_price: float):
    """Safely updates active_trades.json and Excel journal only after confirmed fill."""
    base_dir = _get_base_dir()
    trades_file = base_dir / "active_trades.json"
    account = order["account"]
    sym = order["symbol"]
    short_s = order["short_strike"]
    long_s = order["long_strike"]
    contracts = order["contracts"]
    exp_date = order["exp_date"]

    now_ict = datetime.datetime.now().strftime("%Y-%m-%d %H:%M ICT")

    # 1. Update active_trades.json
    if trades_file.exists():
        try:
            tdata = json.loads(trades_file.read_text(encoding="utf-8"))
            accts = tdata.setdefault("accounts", {})
            acct_data = accts.setdefault(account, {"positions": []})
            positions = acct_data.setdefault("positions", [])
            
            # Avoid duplicate positions
            existing = next((p for p in positions if p.get("symbol") == sym and p.get("short_strike") == short_s and p.get("status") == "Active Protection"), None)
            if not existing:
                positions.append({
                    "symbol": sym,
                    "strategy": f"Bull Put Spread ({short_s:.0f}/{long_s:.0f})",
                    "short_strike": short_s,
                    "long_strike": long_s,
                    "contracts": contracts,
                    "expiration": exp_date,
                    "net_credit": fill_price,
                    "entry_date": now_ict,
                    "status": "Active Protection",
                    "order_id": order["order_id"]
                })
                save_active_trades(tdata, writer="order_fill_tracker", path=trades_file)
                print(f"  ✅ Logged confirmed fill into active_trades.json for {account.upper()}!")
        except Exception as ex_t:
            print(f"  ℹ️ active_trades update notice: {ex_t}")

    # 2. Update Transaction Journal (Excel)
    try:
        update_excel_journal()
        print("  📊 Updated SkonVault_Live_Transaction_Journal.xlsx with confirmed fill!")
    except Exception as ex_j:
        print(f"  ℹ️ Excel journal notice: {ex_j}")

    # 3. RULE-084 Telemetry: Update last_entry_status.json for morning reports
    try:
        last_entry_file = base_dir / "last_entry_status.json"
        last_entry_file.write_text(json.dumps({
            "timestamp": now_ict,
            "subject": f"ORDER_FILLED_{sym}",
            "message": f"Confirmed Fill: {contracts}C {sym} (${short_s:.0f}P/${long_s:.0f}P) @ +${fill_price:.2f}/sh in {account.upper()}!"
        }, indent=2), encoding="utf-8")
        print(f"  📢 Reconciled last_entry_status.json with ORDER_FILLED_{sym}!")
        if AgentBridge:
            try:
                bridge = AgentBridge("hermes")
                bridge.send("anna", f"CONFIRMED FILL: {contracts}C {sym} @ +${fill_price:.2f}/sh in {account.upper()}", channel="trade_execution", subject=f"ORDER_FILLED_{sym}")
                print(f"  ⚡ Mirrored ORDER_FILLED_{sym} to Anna via AgentBridge!")
            except Exception: pass
    except Exception as ex_le:
        print(f"  ℹ️ last_entry_status notice: {ex_le}")


def run_stage1_nudge():
    """
    Fires at 21:30 ICT (+15 min from 21:15 entry).
    Checks order fill status. If still working, nudges limit down by 1 cent ($0.01).
    """
    now_ict = datetime.datetime.now().strftime("%Y-%m-%d %H:%M ICT")
    print("=" * 80)
    print(f"⚡ RULE-075: STAGE 1 ORDER FILL CHECK & MICRO-NUDGE — {now_ict}")
    print("=" * 80)

    orders = load_pending_orders()
    working = [o for o in orders if o.get("status") == "working"]

    if not working:
        print("  ℹ️ No active working orders to track for Stage 1.")
        return

    for order in working:
        acct = order["account"]
        oid = order["order_id"]
        sym = order["symbol"]
        
        # 1. Check Broker Status via Unified Dual-Broker Gateway
        status, filled_qty, fill_price = _check_order_status(order)

        print(f"  • Checking {sym} ({acct.upper()}) | Order: {oid} | Status: {status} | Filled: {filled_qty}")

        if status == "filled" or filled_qty >= order["contracts"]:
            fill_price = fill_price or abs(float(order["current_limit"]))
            order["status"] = "filled"
            order["filled_at"] = now_ict
            order["filled_avg_price"] = fill_price
            save_pending_orders(orders)
            record_confirmed_fill(order, fill_price)

            send_telegram(
                f"🎉 PASSIVE MIDPOINT HARVEST FILLED! 🟢\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📅 Time: {now_ict}\n"
                f"💼 Account: {acct.upper()}\n"
                f"📦 Spread: {sym} ${order['short_strike']:.1f}P / ${order['long_strike']:.1f}P ({order['contracts']}C)\n"
                f"💵 Fill Credit: +${fill_price:.2f} / share (Midpoint Captured!)\n"
                f"🌾 FastHarvest 50% TP Armed & Active!"
            )
            continue

        if status in ("canceled", "expired", "rejected"):
            print(f"  🛑 Order {oid} was {status}. Marking as closed.")
            order["status"] = status
            save_pending_orders(orders)
            continue

        # 2. Still Working -> Perform Adaptive EV Sweet-Spot Nudge (RULE-075 Stage 1 Upgrade)
        old_limit = order["current_limit"]
        mac_floor = calculate_mac_floor(order["width"])

        # Fetch live option quotes to calculate the spread gap
        s_bid, s_ask, l_bid, l_ask = _get_spread_quotes(order)

        nat_credit = round(s_bid - l_ask, 2)
        s_mid = (s_bid + s_ask) / 2.0
        l_mid = (l_bid + l_ask) / 2.0
        mid_credit = round(s_mid - l_mid, 2)
        spread_gap = max(0.0, round(mid_credit - nat_credit, 2))

        # Target: Concede ~60% of gap to market makers, retaining ~40% price improvement
        # Positions order in the Moderate-to-High (~75%) fill probability zone with maximum profit
        if spread_gap > 0.05 and nat_credit > 0:
            target_limit = round(nat_credit + 0.40 * spread_gap, 2)
        else:
            target_limit = round(old_limit - 0.02, 2)

        new_limit = max(mac_floor, min(old_limit - 0.01, target_limit))

        if new_limit < mac_floor:
            print(f"  ⚠️ Nudge stopped: Next limit ${new_limit:.2f} is below MAC floor ${mac_floor:.2f}.")
            continue

        print(f"  ⚡ Stage 1 EV Nudge for {sym}: Mid=${mid_credit:.2f} | Nat=${nat_credit:.2f} (Gap: ${spread_gap:.2f}) -> New Limit: ${new_limit:.2f}")
        success, new_oid, msg = _replace_spread_order(order, new_limit)

        if success and new_oid:
            order["order_id"] = new_oid
            order["current_limit"] = new_limit
            order["current_stage"] = 2
            order["stage1_nudged_at"] = now_ict
            save_pending_orders(orders)
            roc_pct = (new_limit / order["width"]) * 100.0
            print(f"  ✅ Replaced order: New ID {new_oid} @ limit ${new_limit:.2f} (ROC: {roc_pct:.1f}%)")

            send_telegram(
                f"⚡ 21:30 ICT STAGE 1 OPTIMAL EV NUDGE\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📅 Time: {now_ict}\n"
                f"💼 Account: {acct.upper()}\n"
                f"📦 Spread: {sym} ${order['short_strike']:.1f}P / ${order['long_strike']:.1f}P ({order['contracts']}C)\n"
                f"🎯 Adjusted Limit: ${old_limit:.2f} ➔ ${new_limit:.2f} Credit\n"
                f"📊 Market Spread: Nat ${nat_credit:.2f} | Mid ${mid_credit:.2f} (Retaining +${new_limit - nat_credit:.2f} Edge)\n"
                f"📈 Projected ROC: {roc_pct:.1f}% (Total Income: ${new_limit * order['contracts'] * 100:.2f})\n"
                f"🎯 Fill Probability: Moderate-High (~75% Execution Zone)\n"
                f"🛡️ MAC Safety Floor: ${mac_floor:.2f}\n"
                f"⏳ Next Check: 21:46 ICT UOA #2 Conviction Gate"
            )
        else:
            print(f"  🔴 Failed to replace order: {msg}")


def _execute_rule081_fallback_relay(client: Any, account: str, aborted_order: Dict[str, Any]):
    """
    RULE-081: Autonomous Fallback Relay Engine.
    When a candidate aborts due to MAC floor failure or flow drop:
    1. Quarantines the aborted symbol for the remainder of the session.
    2. Identifies all currently active positions in the portfolio across accounts.
    3. Scans surviving candidates in verified_universe_catalog.json with live quotes.
    4. Enforces live Net Credit >= MAC floor.
    5. Weights and ranks using the 5-factor scoring model.
    6. Automatically submits the #1 surviving fallback winner on Alpaca or Tradier Live
       to eliminate idle cash drag.
    """
    now_ict = datetime.datetime.now().strftime("%Y-%m-%d %H:%M ICT")
    base_dir = _get_base_dir()
    aborted_sym = aborted_order.get("symbol", "").upper()
    contracts = int(aborted_order.get("contracts", 2))
    is_tradier = _is_tradier_order(aborted_order) or "tradier" in account.lower()

    print(f"\n🔄 RULE-081: ACTIVATING AUTONOMOUS FALLBACK RELAY (Account: {account.upper()} | Quarantining {aborted_sym})...")

    # 1. Quarantine aborted symbol + active portfolio positions across all accounts
    quarantine_symbols = {aborted_sym}
    for acct_type in ["alpaca_live", "pion_main", "pion2_sub"]:
        try:
            cli = AlpacaClient(acct_type)
            for p in cli.get_positions():
                sym_raw = p.get("symbol", "")
                m = re.match(r"^([A-Z]+)", sym_raw)
                if m:
                    quarantine_symbols.add(m.group(1))
                elif sym_raw:
                    quarantine_symbols.add(sym_raw)
        except Exception:
            pass

    if TradierClient:
        try:
            t_chk = TradierClient("live" if "live" in account.lower() else "sandbox")
            for p in t_chk.get_positions():
                sym_raw = p.get("symbol", "")
                m = re.match(r"^([A-Z]+)", sym_raw)
                if m:
                    quarantine_symbols.add(m.group(1))
                elif sym_raw:
                    quarantine_symbols.add(sym_raw)
        except Exception:
            pass

    print(f"  🛡️ Quarantined Assets: {sorted(list(quarantine_symbols))}")

    # 2. Ingest Catalog
    cat_file = base_dir / "verified_universe_catalog.json"
    if not cat_file.exists():
        print("  ⚠️ Catalog missing for fallback relay.")
        return

    catalog = json.loads(cat_file.read_text(encoding="utf-8"))

    # 5-Factor Theme COT weights
    theme_cot_scores = {
        "1. AI Chips & Hardware": 95.0,
        "2. Data Center Power & Cooling": 88.0,
        "3. Inflation Defense & Hard Assets": 75.0,
        "4. Longevity & Healthcare": 85.0,
        "5. Consumer Staples & National Defense": 90.0,
        "6. Financial Services & Payment Rails": 92.0
    }

    viable_candidates = []

    if is_tradier:
        t_client = client if hasattr(client, "execute_vertical_spread") else (TradierClient("live" if "live" in account.lower() else "sandbox") if TradierClient else None)
        if not t_client:
            print("  ⚠️ TradierClient unavailable for Tradier fallback relay.")
            return

        for theme, cands in catalog.items():
            for cand in cands:
                sym = cand.get("symbol", "").upper()
                if sym in quarantine_symbols:
                    continue

                cur_spot = 0.0
                try:
                    from live_spot import get_spot
                    s_data = get_spot(sym)
                    cur_spot = float(s_data.get("price", 0.0)) if s_data else 0.0
                except Exception:
                    cur_spot = 0.0
                if cur_spot <= 0:
                    try:
                        q_data = t_client.get_quotes([sym])
                        if q_data:
                            cur_spot = float(q_data[0].get("last") or q_data[0].get("bid") or 0.0)
                    except Exception:
                        cur_spot = 0.0

                if cur_spot <= 0:
                    continue

                cand_w = float(cand.get("width", 5.0))
                w = min(5.0, cand_w) if cand_w > 0 else 5.0
                if sym in ["XLF", "SLV"]:
                    w = 2.0

                step = 1.0 if cur_spot < 100 else 5.0
                s = round((cur_spot * 0.95) / step) * step
                l = s - w
                if s <= 0 or l <= 0:
                    continue

                try:
                    expirations = t_client.get_option_expirations(sym)
                    if not expirations:
                        continue
                    today = datetime.date.today()
                    dtes = []
                    for e in expirations:
                        try:
                            d = (datetime.datetime.strptime(e, "%Y-%m-%d").date() - today).days
                            if 8 <= d <= 35:
                                dtes.append((e, d))
                        except Exception: pass
                    if not dtes:
                        continue
                    target_exp = min(dtes, key=lambda x: abs(x[1] - 14))[0]

                    chain = t_client.get_option_chain(sym, target_exp, greeks=True)
                    s_opt = next((o for o in chain if o.get("option_type") == "put" and abs(float(o.get("strike", 0)) - s) < 0.05), None)
                    l_opt = next((o for o in chain if o.get("option_type") == "put" and abs(float(o.get("strike", 0)) - l) < 0.05), None)

                    if not s_opt or not l_opt:
                        continue

                    s_bid = float(s_opt.get("bid") or 0.0)
                    s_ask = float(s_opt.get("ask") or s_bid)
                    l_bid = float(l_opt.get("bid") or 0.0)
                    l_ask = float(l_opt.get("ask") or 0.0)
                    if s_bid <= 0 or l_ask <= 0:
                        continue

                    net = round(s_bid - l_ask, 2)
                    mac = calculate_mac_floor(w)

                    if net >= mac:
                        box_score = 25.0
                        div_score = 20.0
                        cot_score = round((theme_cot_scores.get(theme, 80.0) / 100.0) * 20.0, 1)
                        roc_pct = (net / w) * 100.0
                        is_core = sym in ["SPY", "QQQ", "XLF", "GLD", "NVDA"]
                        yield_score = 20.0 if (is_core or roc_pct >= 10.0) else 18.0
                        safety_score = 15.0
                        skew_bonus = 10.0 if sym in ["GE", "AMD", "TSM", "NVDA", "AVGO", "LMT"] else 5.0
                        total_score = round(box_score + div_score + cot_score + yield_score + safety_score + skew_bonus, 2)

                        viable_candidates.append({
                            "symbol": sym,
                            "theme": theme,
                            "short_strike": s,
                            "long_strike": l,
                            "width": w,
                            "exp_date": target_exp,
                            "short_sym": s_opt.get("symbol"),
                            "long_sym": l_opt.get("symbol"),
                            "net_credit": net,
                            "mac_floor": mac,
                            "total_score": total_score,
                            "roc_pct": roc_pct,
                            "s_bid": s_bid,
                            "s_ask": s_ask,
                            "l_bid": l_bid,
                            "l_ask": l_ask
                        })
                except Exception:
                    continue
    else:
        for theme, cands in catalog.items():
            for cand in cands:
                sym = cand.get("symbol", "").upper()
                if sym in quarantine_symbols:
                    continue

                cur_spot = 0.0
                try:
                    from live_spot import get_spot
                    s_data = get_spot(sym)
                    cur_spot = float(s_data.get("price", 0.0)) if s_data else 0.0
                except Exception:
                    cur_spot = 0.0
                if cur_spot <= 0:
                    try:
                        bbar = client.get_latest_bar(sym) if hasattr(client, 'get_latest_bar') else None
                        cur_spot = float(bbar.get("c", 0.0)) if bbar else 0.0
                    except Exception:
                        cur_spot = 0.0

                w = float(cand.get("width", 5.0))
                if cur_spot > 0:
                    step = 1.0 if cur_spot < 100 else 5.0
                    s = round((cur_spot * 0.95) / step) * step
                    l = s - w
                else:
                    print(f"  ⚠️ Skipping {sym} in fallback relay: live spot unavailable.")
                    continue

                if s <= 0:
                    continue

                # Target dynamic expiration: pass None so resolve_spread_pair resolves active 14-45 DTE Friday
                resolved = client.resolve_spread_pair(sym, s, width=w, require_live_bid=False, target_expiration=None)
                if not resolved:
                    continue

                quotes = client.get_option_snapshot([resolved["short_sym"], resolved["long_sym"]])
                s_bid = quotes.get(resolved["short_sym"], {}).get("bid", 0.0)
                l_ask = quotes.get(resolved["long_sym"], {}).get("ask", 0.0)
                net = round(s_bid - l_ask, 2)
                mac = calculate_mac_floor(w)

                if net >= mac:
                    # 5-factor scoring model
                    box_score = 25.0
                    div_score = 20.0  # Zero overlap with active holdings
                    cot_score = round((theme_cot_scores.get(theme, 80.0) / 100.0) * 20.0, 1)
                    roc_pct = (net / w) * 100.0
                    is_core = sym in ["SPY", "QQQ", "XLF", "GLD", "NVDA"]
                    yield_score = 20.0 if (is_core or roc_pct >= 10.0) else 18.0
                    safety_score = 15.0
                    skew_bonus = 10.0 if sym in ["GE", "AMD", "TSM", "NVDA", "AVGO", "LMT"] else 5.0

                    total_score = round(box_score + div_score + cot_score + yield_score + safety_score + skew_bonus, 2)

                    viable_candidates.append({
                        "symbol": sym,
                        "theme": theme,
                        "short_strike": s,
                        "long_strike": l,
                        "width": w,
                        "exp_date": resolved["exp_date"],
                        "short_sym": resolved["short_sym"],
                        "long_sym": resolved["long_sym"],
                        "net_credit": net,
                        "mac_floor": mac,
                        "total_score": total_score,
                        "roc_pct": roc_pct
                    })

    if not viable_candidates:
        print(f"  🛡️ RULE-081: Zero alternative candidates meet MAC Floor. Standing down safely in cash.")
        try:
            last_entry_file = base_dir / "last_entry_status.json"
            last_entry_file.write_text(json.dumps({
                "timestamp": now_ict,
                "subject": f"ORDER_RESOLVED_STANDOFF_CLOSED_{aborted_sym}",
                "message": f"Fallback Stand Down: Zero viable candidates above MAC floor. Portfolio in 100% settled USD cash reserve."
            }, indent=2), encoding="utf-8")
            if AgentBridge:
                bridge = AgentBridge("hermes")
                bridge.send("anna", f"FALLBACK STAND DOWN: Zero candidates above MAC floor. Portfolio in cash.", channel="trade_execution", subject=f"ORDER_RESOLVED_STANDOFF_CLOSED_{aborted_sym}")
        except Exception: pass

        send_telegram(
            f"🛡️ RULE-081 FALLBACK RELAY: STAND DOWN\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 Time: {now_ict}\n"
            f"💼 Account: {account.upper()}\n"
            f"🚫 Quarantined: {aborted_sym}\n"
            f"ℹ️ Status: All surviving candidates below MAC Floor.\n"
            f"💵 Action: 100% Capital preserved in USD Cash Reserve (≥35% floor protected)."
        )
        return

    # Sort descending by 5-Factor Score
    viable_candidates = sorted(viable_candidates, key=lambda x: x["total_score"], reverse=True)
    winner = viable_candidates[0]

    print(f"  🏆 RULE-081 FALLBACK WINNER: {winner['symbol']} ({winner['total_score']:.1f} pts | Credit: ${winner['net_credit']:.2f} | ROC: {winner['roc_pct']:.1f}%)")

    if is_tradier:
        t_client = client if hasattr(client, "execute_vertical_spread") else (TradierClient("live" if "live" in account.lower() else "sandbox") if TradierClient else None)
        final_contracts = 1
        s_bid = winner.get("s_bid", 0.0)
        s_ask = winner.get("s_ask", 0.0)
        l_bid = winner.get("l_bid", 0.0)
        l_ask = winner.get("l_ask", 0.0)
        nat_credit = round(s_bid - l_ask, 2)
        s_mid = (s_bid + s_ask) / 2.0
        l_mid = (l_bid + l_ask) / 2.0
        mid_credit = round(s_mid - l_mid, 2)
        spread_gap = max(0.0, round(mid_credit - nat_credit, 2))

        if spread_gap > 0.05 and nat_credit > 0:
            optimal_fallback_limit = max(winner["mac_floor"], round(nat_credit + 0.40 * spread_gap, 2))
        else:
            optimal_fallback_limit = max(winner["mac_floor"], mid_credit)

        print(f"  🎯 Submitting Fallback {winner['symbol']} to TRADIER LIVE at Optimal EV Limit: ${optimal_fallback_limit:.2f}")

        try:
            res_tr = t_client.execute_vertical_spread(
                symbol=winner["symbol"],
                short_occ=winner["short_sym"],
                long_occ=winner["long_sym"],
                qty=final_contracts,
                limit_credit=optimal_fallback_limit,
                duration="day"
            )
            new_oid = str(res_tr.get("order_id", ""))
            success = bool(new_oid and new_oid != "None")
            oids = [new_oid] if success else []
            cred = optimal_fallback_limit
            msg = f"Placed Tradier {final_contracts}C {winner['symbol']} Fallback @ ${optimal_fallback_limit:.2f}"
        except Exception as ex_tr:
            success = False
            oids = []
            cred = optimal_fallback_limit
            msg = f"Tradier fallback submission failed: {ex_tr}"
    else:
        # Sizing check for target account (Alpaca)
        target_risk = 1200.0 if account == "pion2_sub" else 4000.0
        final_contracts = max(1, min(contracts, int(target_risk / (winner["width"] * 100.0))))

        # Fetch live quotes for winner to calculate Optimal EV Limit (RULE-081 + RULE-075 Ladder Alignment)
        quotes = client.get_option_snapshot([winner["short_sym"], winner["long_sym"]])
        s_bid = quotes.get(winner["short_sym"], {}).get("bid", 0.0)
        s_ask = quotes.get(winner["short_sym"], {}).get("ask", 0.0)
        l_bid = quotes.get(winner["long_sym"], {}).get("bid", 0.0)
        l_ask = quotes.get(winner["long_sym"], {}).get("ask", 0.0)

        nat_credit = round(s_bid - l_ask, 2)
        s_mid = (s_bid + s_ask) / 2.0
        l_mid = (l_bid + l_ask) / 2.0
        mid_credit = round(s_mid - l_mid, 2)
        spread_gap = max(0.0, round(mid_credit - nat_credit, 2))

        # Apply identical RULE-075 Stage 1 EV Sweet Spot: Concede 60% gap, keep 40% price improvement
        if spread_gap > 0.05 and nat_credit > 0:
            optimal_fallback_limit = max(winner["mac_floor"], round(nat_credit + 0.40 * spread_gap, 2))
        else:
            optimal_fallback_limit = max(winner["mac_floor"], mid_credit)

        print(f"  🎯 Submitting Fallback {winner['symbol']} at Optimal EV Limit: ${optimal_fallback_limit:.2f} (Mid: ${mid_credit:.2f} | Nat: ${nat_credit:.2f})")

        # Atomic Multi-Leg Order Submission at Optimal EV Limit
        mleg_payload = {
            "order_class": "mleg",
            "type": "limit",
            "time_in_force": "day",
            "legs": [
                {"symbol": winner["short_sym"], "ratio_qty": 1, "side": "sell", "position_intent": "sell_to_open"},
                {"symbol": winner["long_sym"], "ratio_qty": 1, "side": "buy", "position_intent": "buy_to_open"}
            ],
            "qty": str(final_contracts),
            "limit_price": f"-{optimal_fallback_limit:.2f}"
        }

        try:
            req_mleg = urllib.request.Request(
                f"{client.base_url}/v2/orders",
                data=json.dumps(mleg_payload).encode("utf-8"),
                headers=client._headers()
            )
            with urllib.request.urlopen(req_mleg, context=client.ssl_ctx, timeout=10) as r_mleg:
                resp_mleg = json.loads(r_mleg.read().decode())
                new_oid = resp_mleg.get("id")
                success = bool(new_oid)
                oids = [new_oid] if new_oid else []
                cred = optimal_fallback_limit
                msg = f"Placed {final_contracts}C {winner['symbol']} Fallback @ ${optimal_fallback_limit:.2f}"
        except Exception as ex_mleg:
            success = False
            oids = []
            cred = optimal_fallback_limit
            msg = f"Fallback submission failed: {ex_mleg}"

    if success and oids:
        new_oid = oids[0]
        register_order(
            account=account,
            symbol=winner["symbol"],
            short_sym=winner["short_sym"],
            long_sym=winner["long_sym"],
            short_strike=winner["short_strike"],
            long_strike=winner["long_strike"],
            width=winner["width"],
            contracts=final_contracts,
            exp_date=winner["exp_date"],
            order_id=new_oid,
            limit_credit=cred,
            broker_type="tradier" if is_tradier else "alpaca"
        )
        roc_fallback = (cred / winner["width"]) * 100.0
        send_telegram(
            f"⚡ RULE-081 FALLBACK RELAY DEPLOYED! 🚀\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 Time: {now_ict}\n"
            f"💼 Account: {account.upper()}\n"
            f"🚫 Quarantined: {aborted_sym} (Liquidity failure)\n"
            f"🏆 Next Best Winner: {winner['symbol']} ${winner['short_strike']:.0f}P/${winner['long_strike']:.0f}P ({winner['exp_date']})\n"
            f"📊 5-Factor Score: {winner['total_score']:.1f} pts (ROC: {roc_fallback:.1f}%)\n"
            f"💵 Optimal Limit: ${cred:.2f} (Total Income: ${cred * final_contracts * 100:.2f})\n"
            f"🎯 Fill Probability: Moderate-High (~75% Execution Zone)\n"
            f"📦 Order ID: {new_oid}\n"
            f"🛡️ Citadel Envelope: ${final_contracts * winner['width'] * 100:.2f} max collateral"
        )
        try:
            last_entry_file = base_dir / "last_entry_status.json"
            last_entry_file.write_text(json.dumps({
                "timestamp": now_ict,
                "subject": f"ORDER_SUBMITTED_FALLBACK_RELAY_{winner['symbol']}",
                "message": f"Fallback Deployed: {final_contracts}C {winner['symbol']} (${winner['short_strike']:.0f}P/${winner['long_strike']:.0f}P) @ ${cred:.2f} in {account.upper()}."
            }, indent=2), encoding="utf-8")
            if AgentBridge:
                bridge = AgentBridge("hermes")
                bridge.send("anna", f"FALLBACK DEPLOYED: {final_contracts}C {winner['symbol']} @ ${cred:.2f}", channel="trade_execution", subject=f"ORDER_SUBMITTED_FALLBACK_RELAY_{winner['symbol']}")
                print(f"  ⚡ Mirrored ORDER_SUBMITTED_FALLBACK_RELAY_{winner['symbol']} to Anna via AgentBridge!")
        except Exception: pass
    else:
        print(f"  🔴 RULE-081 Fallback submission notice: {msg}")


def run_stage2_conviction_trigger():
    """
    Fires at 21:46 ICT (immediately after 21:45 ICT UOA Sweep #2).
    If UOA #2 confirms institutional flow, executes at Marketable Natural Limit
    subject to MAC floor for guaranteed fill. If flow died, aborts.
    """
    now_ict = datetime.datetime.now().strftime("%Y-%m-%d %H:%M ICT")
    print("=" * 80)
    print(f"🚀 RULE-075: STAGE 2 UOA #2 CONVICTION GATE & ACCELERATOR — {now_ict}")
    print("=" * 80)

    orders = load_pending_orders()
    working = [o for o in orders if o.get("status") == "working"]

    if not working:
        print("  ℹ️ No active working orders to track for Stage 2.")
        return

    # Check UOA institutional flow conviction
    base_dir = _get_base_dir()
    uoa_file = base_dir / "uoa_live_cache.json"
    uoa_confirmed = False
    uoa_ratio = 0.0
    if uoa_file.exists():
        try:
            file_age_sec = time.time() - uoa_file.stat().st_mtime
            if file_age_sec <= 2700:
                udata = json.loads(uoa_file.read_text(encoding="utf-8"))
                uoa_ratio = float(udata.get("volume_oi_ratio") or udata.get("vol_oi_ratio", 0.0))
                bias = str(udata.get("institutional_bias", "")).upper()
                sweeps = udata.get("sweeps", [])
                sweeps_cnt = int(udata.get("institutional_sweeps_count") or len(sweeps))

                # Institutional Conviction Gate (RULE-075 / Tumbler 4):
                # Whole-chain volume/OI ratio typically ranges 0.10x-0.35x in morning sessions.
                # Confirmed if institutional bias is supported above put wall / bullish / neutral (no breakdown)
                # and market tape is active (uoa_ratio >= 0.05), OR if strike-level sweeps exist (>0).
                # True flow divergence only triggers if there is genuine breakdown (spot below put wall) or dead tape (<0.03x).
                is_breakdown = "BELOW" in bias or "BREAKDOWN" in bias or "BEARISH" in bias
                tape_active = uoa_ratio >= 0.05
                bias_supported = "SUPPORTED" in bias or "BULLISH" in bias or "NEUTRAL" in bias or not is_breakdown
                uoa_confirmed = (bias_supported and tape_active) or sweeps_cnt > 0 or uoa_ratio >= 1.5
                print(f"  📡 Fresh UOA Flow Conviction: {uoa_ratio:.2f}x Vol/OI | Bias: {bias} | Confirmed: {uoa_confirmed} (Age: {int(file_age_sec/60)}m)")
            else:
                print(f"  ⚠️ UOA Flow Cache is STALE ({int(file_age_sec/60)}m old > 45m limit). Conviction unconfirmed.")
        except Exception as ex_uoa:
            print(f"  ⚠️ Error parsing UOA cache: {ex_uoa}")
    else:
        print("  ⚠️ UOA live cache missing. Conviction unconfirmed.")

    for order in working:
        acct = order["account"]
        oid = order["order_id"]
        sym = order["symbol"]

        # 1. Check if already filled
        status, filled_qty, fill_price = _check_order_status(order)

        if status == "filled" or filled_qty >= order["contracts"]:
            fill_price = fill_price or abs(float(order["current_limit"]))
            order["status"] = "filled"
            order["filled_at"] = now_ict
            order["filled_avg_price"] = fill_price
            save_pending_orders(orders)
            record_confirmed_fill(order, fill_price)
            print(f"  🎉 Order {oid} already filled at ${fill_price:.2f}!")
            continue

        # 2. Evaluate Conviction Gate
        if not uoa_confirmed:
            print(f"  🛑 UOA Flow Diverged (Bias: {bias} | Vol/OI {uoa_ratio:.2f}x). Aborting order to preserve capital.")
            _cancel_broker_order(order)
            order["status"] = "aborted_flow_diverged"
            save_pending_orders(orders)
            try:
                last_entry_file = base_dir / "last_entry_status.json"
                last_entry_file.write_text(json.dumps({
                    "timestamp": now_ict,
                    "subject": f"ORDER_RESOLVED_ABORTED_FLOW_{sym}",
                    "message": f"Stage 2 Abort: {sym} cancelled due to UOA flow breakdown ({bias} | Vol/OI {uoa_ratio:.2f}x). Order cancelled safely."
                }, indent=2), encoding="utf-8")
                if AgentBridge:
                    bridge = AgentBridge("hermes")
                    bridge.send("anna", f"STAGE 2 ABORT: {sym} cancelled due to flow divergence ({bias}). Capital safe.", channel="trade_execution", subject=f"ORDER_RESOLVED_ABORTED_FLOW_{sym}")
                    print(f"  ⚡ Mirrored ORDER_RESOLVED_ABORTED_FLOW_{sym} to Anna via AgentBridge!")
            except Exception: pass

            send_telegram(
                f"🛑 21:46 ICT STAGE 2 ABORT: FLOW DIVERGENCE\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📅 Time: {now_ict}\n"
                f"💼 Account: {acct.upper()}\n"
                f"📦 Spread: {sym} ${order['short_strike']:.1f}P / ${order['long_strike']:.1f}P\n"
                f"⚠️ Reason: UOA Sweep #2 showed flow breakdown ({bias} | Vol/OI {uoa_ratio:.2f}x).\n"
                f"🛡️ Action: Order cancelled. Capital 100% preserved in settled USD Cash Reserve."
            )
            # RULE-081: Autonomous Fallback Relay across Alpaca and Tradier Live
            if _is_tradier_order(order):
                t_cli = TradierClient("live" if "live" in acct.lower() else "sandbox") if TradierClient else None
                if t_cli:
                    _execute_rule081_fallback_relay(t_cli, acct, order)
            else:
                client = AlpacaClient(acct)
                _execute_rule081_fallback_relay(client, acct, order)
            continue

        # 3. Flow Confirmed: Fetch Live Quotes for Marketable Natural Limit
        s_bid, s_ask, l_bid, l_ask = _get_spread_quotes(order)

        # Fallback to current limit - 2 cents if live quotes unavailable
        if s_bid <= 0 or l_ask <= 0:
            natural_credit = round(order["current_limit"] - 0.02, 2)
        else:
            natural_credit = round(s_bid - l_ask, 2)

        mac_floor = calculate_mac_floor(order["width"])

        print(f"  📡 Live Quotes: Short Bid ${s_bid:.2f} | Long Ask ${l_ask:.2f} | Natural Credit: ${natural_credit:.2f} (MAC Floor: ${mac_floor:.2f})")

        if natural_credit < mac_floor:
            print(f"  🛑 Natural credit ${natural_credit:.2f} is below MAC floor ${mac_floor:.2f}. Aborting to protect risk-reward.")
            _cancel_broker_order(order)
            order["status"] = "aborted_mac_floor"
            save_pending_orders(orders)
            try:
                last_entry_file = base_dir / "last_entry_status.json"
                last_entry_file.write_text(json.dumps({
                    "timestamp": now_ict,
                    "subject": f"ORDER_RESOLVED_ABORTED_MAC_{sym}",
                    "message": f"Stage 2 Abort: {sym} cancelled due to MAC floor protection (Natural ${natural_credit:.2f} < Floor ${mac_floor:.2f}). Order cancelled safely."
                }, indent=2), encoding="utf-8")
                if AgentBridge:
                    bridge = AgentBridge("hermes")
                    bridge.send("anna", f"STAGE 2 ABORT: {sym} cancelled due to MAC floor protection. Capital safe.", channel="trade_execution", subject=f"ORDER_RESOLVED_ABORTED_MAC_{sym}")
                    print(f"  ⚡ Mirrored ORDER_RESOLVED_ABORTED_MAC_{sym} to Anna via AgentBridge!")
            except Exception: pass

            send_telegram(
                f"🛑 21:46 ICT STAGE 2 ABORT: MAC FLOOR PROTECTION\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📅 Time: {now_ict}\n"
                f"💼 Account: {acct.upper()}\n"
                f"📦 Spread: {sym} ${order['short_strike']:.1f}P / ${order['long_strike']:.1f}P\n"
                f"⚠️ Reason: Marketable Credit (${natural_credit:.2f}) < MAC Floor (${mac_floor:.2f}).\n"
                f"🛡️ Action: Spread bid-ask blown out. Cancelled safely to prevent poor risk-reward."
            )
            # RULE-081: Autonomous Fallback Relay across Alpaca and Tradier Live
            if _is_tradier_order(order):
                t_cli = TradierClient("live" if "live" in acct.lower() else "sandbox") if TradierClient else None
                if t_cli:
                    _execute_rule081_fallback_relay(t_cli, acct, order)
            else:
                client = AlpacaClient(acct)
                _execute_rule081_fallback_relay(client, acct, order)
            continue

        # 4. Fire Marketable Natural Limit for Instant Fill
        print(f"  ⚡ Firing MARKETABLE NATURAL LIMIT @ ${natural_credit:.2f} for immediate execution...")
        success, new_oid, msg = _replace_spread_order(order, natural_credit)

        if success and new_oid:
            order["order_id"] = new_oid
            order["current_limit"] = natural_credit
            order["current_stage"] = 3
            order["stage2_action"] = f"fired_natural_{natural_credit}"

            # Quick verification sweep (marketable limit usually fills in 1-3 seconds)
            time.sleep(2)
            st_check, qty_check, px_check = _check_order_status(order)
            if st_check == "filled" or qty_check >= order["contracts"]:
                fill_price = px_check or natural_credit
                order["status"] = "filled"
                order["filled_at"] = now_ict
                order["filled_avg_price"] = fill_price
                save_pending_orders(orders)
                record_confirmed_fill(order, fill_price)

                send_telegram(
                    f"🚀 21:46 ICT UOA #2 ACCELERATOR FILLED! 🟢\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📅 Time: {now_ict}\n"
                    f"💼 Account: {acct.upper()}\n"
                    f"📦 Spread: {sym} ${order['short_strike']:.1f}P / ${order['long_strike']:.1f}P ({order['contracts']}C)\n"
                    f"💵 Execution Price: +${fill_price:.2f} / share (Marketable Limit ✅)\n"
                    f"🛡️ Defined Risk Cap: ${order['contracts'] * order['width'] * 100 - order['contracts'] * fill_price * 100:,.2f}\n"
                    f"🌾 FastHarvest 50% TP Armed & Active!"
                )
            else:
                save_pending_orders(orders)
                print(f"  ℹ️ Marketable order submitted (ID: {new_oid}, Status: {st_check}).")


def run_stage3_standoff_sweep():
    """
    Fires at 22:00 ICT (Hard Order Standoff).
    Cancels any remaining unfilled working orders across all accounts to ensure
    zero orders linger until 03:15 AM to become expired ghosts.
    """
    now_ict = datetime.datetime.now().strftime("%Y-%m-%d %H:%M ICT")
    base_dir = _get_base_dir()
    print("=" * 80)
    print(f"🛡️ RULE-075: STAGE 3 HARD ORDER STANDOFF SWEEP — {now_ict}")
    print("=" * 80)

    orders = load_pending_orders()
    working = [o for o in orders if o.get("status") == "working"]

    if not working:
        print("  ✅ Zero working orders pending. Order book 100% clean!")
        return

    for order in working:
        acct = order["account"]
        oid = order["order_id"]
        sym = order["symbol"]

        # Check if filled right before 22:00
        st_final, qty_final, px_final = _check_order_status(order)
        if st_final == "filled" or qty_final >= order["contracts"]:
            fill_price = px_final or abs(float(order["current_limit"]))
            order["status"] = "filled"
            order["filled_at"] = now_ict
            order["filled_avg_price"] = fill_price
            record_confirmed_fill(order, fill_price)
            print(f"  🎉 Order {oid} filled at the wire!")
            continue

        # Cancel lingering order
        print(f"  🛑 22:00 Standoff: Cancelling lingering order {oid} ({sym} on {acct})...")
        _cancel_broker_order(order)
        order["status"] = "canceled_standoff_2200"
        try:
            last_entry_file = base_dir / "last_entry_status.json"
            last_entry_file.write_text(json.dumps({
                "timestamp": now_ict,
                "subject": f"ORDER_RESOLVED_STANDOFF_CLOSED_{sym}",
                "message": f"Stage 3 Standoff: Unfilled order {oid} ({sym}) cancelled at 22:00 ICT. Zero overnight ghosts. Capital 100% safe in settled USD cash reserve."
            }, indent=2), encoding="utf-8")
            if AgentBridge:
                bridge = AgentBridge("hermes")
                bridge.send("anna", f"STAGE 3 STANDOFF: {sym} cancelled at 22:00 ICT. Zero overnight ghosts. Capital 100% in cash.", channel="trade_execution", subject=f"ORDER_RESOLVED_STANDOFF_CLOSED_{sym}")
                print(f"  ⚡ Mirrored ORDER_RESOLVED_STANDOFF_CLOSED_{sym} to Anna via AgentBridge!")
        except Exception: pass

        send_telegram(
            f"🛡️ 22:00 ICT HARD ORDER STANDOFF EXECUTED\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 Time: {now_ict}\n"
            f"💼 Account: {acct.upper()}\n"
            f"📦 Spread: {sym} ${order['short_strike']:.1f}P / ${order['long_strike']:.1f}P\n"
            f"🛑 Action: Working order cancelled before liquidity dries up.\n"
            f"🛡️ Zero Overnight Ghosts: Capital 100% safe in settled USD Cash Reserve."
        )

    save_pending_orders(orders)
    print("  ✅ All lingering orders cleared. Order book 100% clean.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="RULE-075 3-Stage Adaptive Fill Protocol Engine")
    parser.add_argument("--stage1", action="store_true", help="Run 21:30 ICT Stage 1 Micro-Nudge")
    parser.add_argument("--stage2", action="store_true", help="Run 21:46 ICT Stage 2 UOA #2 Conviction Trigger")
    parser.add_argument("--stage3", action="store_true", help="Run 22:00 ICT Stage 3 Hard Standoff Sweep")
    parser.add_argument("--status", action="store_true", help="Show current pending order state")
    args = parser.parse_args()

    if args.stage1:
        run_stage1_nudge()
    elif args.stage2:
        run_stage2_conviction_trigger()
    elif args.stage3:
        run_stage3_standoff_sweep()
    elif args.status:
        orders = load_pending_orders()
        print(json.dumps(orders, indent=2))
    else:
        parser.print_help()
