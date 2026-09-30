#!/usr/bin/env python3
"""
execute_qqq_iron_condor.py — Production Execution Engine for QQQ Iron Condor (RULE-094)
Target: ALPACA_LIVE (#290523608)
Collateral: $5,000.00 (2 Contracts x $25.00 width)
Wings:
  • Put Wing : QQQ $700P / $675P (-5.8% OTM)
  • Call Wing: QQQ $775C / $800C (+4.3% OTM)
"""

import sys
import os
import json
import time
import datetime
import urllib.request
import ssl
from pathlib import Path

# Add shared directory to path
SHARED_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SHARED_DIR))

from alpaca_broker import AlpacaClient
from intelligent_spread_formatter import parse_option_symbol

def send_telegram_alert(text: str) -> bool:
    group_id = "-1004375899205"
    hermes_token = "8991076258:AAGHyb-jEnp29BQ5O5V0mo4vFOmgXfgnmTg"
    ctx_ssl = ssl._create_unverified_context()
    try:
        url = f"https://api.telegram.org/bot{hermes_token}/sendMessage"
        payload = json.dumps({"chat_id": group_id, "text": text}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, context=ctx_ssl, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return bool(data.get("ok"))
    except Exception as e:
        print(f"  ℹ️ Telegram alert error: {e}")
        return False

def broadcast_to_anna(subject: str, message: str, payload: dict = None):
    try:
        from agent_bridge import AgentBridge
        bridge = AgentBridge("hermes")
        body_text = message
        if payload:
            body_text += "\n\nJSON_METADATA:\n" + json.dumps(payload, indent=2)
        bridge.send("anna", body_text, channel="trade_execution", subject=subject)
        print(f"  ⚡ Mirrored trade event to Anna via AgentBridge!")
    except Exception as ex:
        print(f"  ℹ️ Bridge mirror notice: {ex}")

def execute_qqq_condor():
    print("============================================================")
    print("🚀 EXECUTING QQQ IRON CONDOR LIVE DISPATCH (RULE-094)")
    print("============================================================")
    
    broker = AlpacaClient("alpaca_live")
    
    # 1. Market Open Verification
    clock = broker.get_clock()
    if not clock.get("is_open", False):
        print(f"🛑 Market is CLOSED according to Alpaca Clock. Aborting.")
        return False, None
    print(f"✅ Market is OPEN. Clock: {clock.get('timestamp')}")
    
    # 2. Account & Margin Health Pre-Flight
    acct = broker.get_account()
    cash = float(acct.get("cash", 0.0))
    equity = float(acct.get("portfolio_value", 0.0))
    bp = float(acct.get("buying_power", 0.0))
    print(f"📊 Alpaca Live Balances: Cash=${cash:,.2f} | Equity=${equity:,.2f} | Buying Power=${bp:,.2f}")
    
    if cash < 25000.0:
        print(f"🛑 Insufficient cash balance for live trade: ${cash:,.2f}")
        return False, None
        
    positions = broker.get_positions()
    current_collateral = 0.0
    for p in positions:
        # Sum known locked collateral
        sym = p.get("symbol", "")
        qty = abs(float(p.get("qty", 0)))
        if "AVGO" in sym and float(p.get("qty", 0)) < 0:
            current_collateral += qty * 30.0 * 100.0 # $30w
        elif "NVDA" in sym and float(p.get("qty", 0)) < 0:
            current_collateral += qty * 5.0 * 100.0  # $5w
        elif "TSM" in sym and float(p.get("qty", 0)) < 0:
            current_collateral += qty * 10.0 * 100.0 # $10w
        elif "XLE" in sym and float(p.get("qty", 0)) < 0:
            current_collateral += qty * 2.0 * 100.0  # $2w
            
    print(f"🔒 Current Locked Collateral: ${current_collateral:,.2f} ({current_collateral/cash*100:.1f}% of Cash)")
    from dynamic_regime_manager import evaluate_market_regime
    regime_info = evaluate_market_regime()
    margin_ceiling_pct = regime_info.get("margin_ceiling_pct", 0.70)
    max_deployment = cash * margin_ceiling_pct
    headroom = max_deployment - current_collateral
    print(f"🛡️ {margin_ceiling_pct*100:.0f}% Deployment Ceiling : ${max_deployment:,.2f} (Available Headroom: ${headroom:,.2f})")
    
    contracts = 2
    width = 25.0
    condor_risk = contracts * width * 100.0 # $5,000.00
    
    if condor_risk > headroom:
        print(f"🛑 Defined risk ${condor_risk:,.2f} exceeds headroom ${headroom:,.2f}. Aborting.")
        return False, None
    print(f"✅ Risk check passed: ${condor_risk:,.2f} fits within ${headroom:,.2f} headroom!")
    
    # 3. Live Option Quotes & Striking
    exp_date = "2026-10-16"
    s_put = "QQQ261016P00700000"
    l_put = "QQQ261016P00675000"
    s_call = "QQQ261016C00775000"
    l_call = "QQQ261016C00800000"
    
    symbols = [s_put, l_put, s_call, l_call]
    url_snaps = "https://data.alpaca.markets/v1beta1/options/snapshots?symbols=" + ",".join(symbols)
    req = urllib.request.Request(url_snaps, headers=broker._headers())
    with urllib.request.urlopen(req, context=broker.ssl_ctx) as resp:
        snaps = json.loads(resp.read().decode()).get("snapshots", {})
        
    s_put_q = snaps.get(s_put, {}).get("latestQuote", {})
    l_put_q = snaps.get(l_put, {}).get("latestQuote", {})
    s_call_q = snaps.get(s_call, {}).get("latestQuote", {})
    l_call_q = snaps.get(l_call, {}).get("latestQuote", {})
    
    p_nat = round(s_put_q.get("bp", 0.0) - l_put_q.get("ap", 0.0), 2)
    p_mid = round(((s_put_q.get("bp", 0.0) + s_put_q.get("ap", 0.0))/2) - ((l_put_q.get("bp", 0.0) + l_put_q.get("ap", 0.0))/2), 2)
    
    c_nat = round(s_call_q.get("bp", 0.0) - l_call_q.get("ap", 0.0), 2)
    c_mid = round(((s_call_q.get("bp", 0.0) + s_call_q.get("ap", 0.0))/2) - ((l_call_q.get("bp", 0.0) + l_call_q.get("ap", 0.0))/2), 2)
    
    total_nat = round(p_nat + c_nat, 2)
    total_mid = round(p_mid + c_mid, 2)
    
    print(f"\n📊 Live Option Quotes:")
    print(f"  • Put Wing  ($700P/$675P): Nat=${p_nat:.2f} | Mid=${p_mid:.2f}")
    print(f"  • Call Wing ($775C/$800C): Nat=${c_nat:.2f} | Mid=${c_mid:.2f}")
    print(f"  • TOTAL IC (Width $25w)  : Nat=${total_nat:.2f} | Mid=${total_mid:.2f}")
    
    # Priority Marketable Midpoint Limit (Capture positive slippage without retail spread drag)
    # Target credit is midpoint minus 2 cents to jump priority queue, but never below total_nat
    limit_credit = max(total_nat, round(total_mid - 0.03, 2))
    limit_credit = max(2.25, limit_credit) # MAC floor of 9.0% on $25w
    
    roc = (limit_credit / width) * 100.0
    total_cash_inflow = round(limit_credit * contracts * 100.0, 2)
    
    print(f"\n🎯 Submitting Limit Order:")
    print(f"  • Target Limit Credit : +${limit_credit:.2f} / share")
    print(f"  • Return on Capital   : {roc:.1f}% in 16 DTE (Annualized {roc*(365/16):.0f}%)")
    print(f"  • Total Upfront Cash  : +${total_cash_inflow:,.2f} IN FLOW")
    
    # 4. Construct Atomic 4-Leg Order
    order_payload = {
        "order_class": "mleg",
        "type": "limit",
        "time_in_force": "day",
        "legs": [
            {"symbol": s_put, "ratio_qty": 1, "side": "sell", "position_intent": "sell_to_open"},
            {"symbol": l_put, "ratio_qty": 1, "side": "buy", "position_intent": "buy_to_open"},
            {"symbol": s_call, "ratio_qty": 1, "side": "sell", "position_intent": "sell_to_open"},
            {"symbol": l_call, "ratio_qty": 1, "side": "buy", "position_intent": "buy_to_open"}
        ],
        "qty": str(contracts),
        "limit_price": f"-{limit_credit:.2f}"
    }
    
    print("\n📡 Submitting 4-Leg Atomic Order to Alpaca Live Complex Order Book...")
    try:
        resp = broker._call_api(f"{broker.base_url}/v2/orders", method="POST", data=order_payload, timeout=12)
        order_id = resp.get("id")
        order_status = resp.get("status", "accepted")
        print(f"🎉 ORDER ACCEPTED BY EXCHANGE! Order ID: {order_id} (Status: {order_status})")
    except Exception as ex:
        print(f"🔴 Order Submission Failed: {ex}")
        return False, None
        
    # 5. Monitor Initial Fill Status (10 Seconds)
    print("⏳ Monitoring complex book fill confirmation...")
    is_filled = False
    filled_price = limit_credit
    for _ in range(5):
        time.sleep(2)
        try:
            o_chk = broker.get_order(order_id)
            status_now = o_chk.get("status")
            print(f"   • Order Status Check: {status_now}")
            if status_now == "filled":
                is_filled = True
                # Get filled price if available
                filled_price = float(o_chk.get("filled_avg_price") or limit_credit)
                break
        except Exception:
            pass
            
    now_ict = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT")
    
    # 6. Format Notifications
    if is_filled:
        alert_header = "🚀 AUTONOMOUS QQQ IRON CONDOR FILLED LIVE! 💰"
        exec_desc = f"FILLED ON COMPLEX BOOK @ +${filled_price:.2f} CREDIT"
    else:
        alert_header = "🚀 AUTONOMOUS QQQ IRON CONDOR SUBMITTED (RESTING ON BOOK) ⏳"
        exec_desc = f"RESTING ON COMPLEX BOOK (Limit: +${limit_credit:.2f} Credit)"
        
    alert_text = f"""{alert_header}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 RULE-094 ATOMIC 4-LEG INDEX ENVELOPE
• Asset          : QQQ (2-Contract Iron Condor)
• Target Account : ALPACA_LIVE (290523608)
• Put Wing       : $700.0P / $675.0P (Width: $25.00 | 5.8% OTM)
• Call Wing      : $775.0C / $800.0C (Width: $25.00 | 4.3% OTM)
• Expiration     : {exp_date} (16 DTE Monthly)
• Limit Credit   : +${limit_credit:.2f} / share ({roc:.1f}% ROC)
• Total Cash Flow: +${total_cash_inflow:,.2f} IN FLOW
• Defined Risk   : ${condor_risk:,.2f} (Collateral strictly capped)
• Order Status   : {exec_desc}
• Alpaca Order ID: {order_id}
• FastHarvest TP : +${total_cash_inflow * 0.50:,.2f} Net Profit (DIR-09 50% Rule)
• Time           : {now_ict}

Zero orphaned legs. OCC single-margin collateral enforced! 🛡️🦅"""

    print("\n" + alert_text)
    send_telegram_alert(alert_text)
    broadcast_to_anna(f"IRON_CONDOR_EXECUTED_QQQ", alert_text, {
        "order_id": order_id,
        "symbol": "QQQ",
        "account": "alpaca_live",
        "contracts": contracts,
        "width": width,
        "put_strikes": "700/675",
        "call_strikes": "775/800",
        "credit": limit_credit,
        "defined_risk": condor_risk,
        "status": "filled" if is_filled else "working"
    })
    
    # 7. Update active_trades.json
    try:
        from reconcile_active_trades import reconcile_active_trades
        reconcile_active_trades(quiet=True)
        print("📂 Reconciled active_trades.json with broker ground truth!")
    except Exception as ex_at:
        print(f"ℹ️ Reconcile notice: {ex_at}")
        
    try:
        import transaction_journal_manager
        transaction_journal_manager.update_excel_journal()
        print("📊 Synchronized SkonVault_Live_Transaction_Journal.xlsx!")
    except Exception as ex_j:
        print(f"ℹ️ Journal sync notice: {ex_j}")
        
    return True, order_id

if __name__ == "__main__":
    execute_qqq_condor()
