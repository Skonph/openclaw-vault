#!/usr/bin/env python3
"""
execute_daily_market_open.py — Universal Autonomous 20:30 ICT Market Open Order Dispatcher & Harvester

Executes 100% autonomously every weekday at 20:30 ICT via Linux Cron:
1. Detects weekday target setup:
   - Mon: LMT (Defense Moat)
   - Tue: AVGO -> JPM (Financial Rails) Waterfall
   - Wed: Real Live FastHarvest on SPY & LMT
   - Thu: XLU (Utility Anchor, $55P/$50P)
   - Fri: Hedge Expiry & Weekly Graduation Audit
2. Uses Dynamic Contract Resolver & Universe Waterfall (RULE-048 & RULE-049)
3. Direct broker execution + AgentBridge synchronization!
"""

import os
import sys
import json
import datetime
import urllib.request
import ssl
from pathlib import Path

# Add shared directory to Python path
sys.path.insert(0, str(Path(__file__).parent))
from alpaca_broker import AlpacaClient, AlpacaAuthError, AlpacaOrderError
from auto_harvest_positions import run_all_harvests
from agent_bridge import AgentBridge

def _send_telegram_alert(text: str):
    group_id = "-1004375899205"
    hermes_token = "8991076258:AAGHyb-jEnp29BQ5O5V0mo4vFOmgXfgnmTg"
    ctx_ssl = ssl._create_unverified_context()
    try:
        url = f"https://api.telegram.org/bot{hermes_token}/sendMessage"
        payload = json.dumps({"chat_id": group_id, "text": text}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, context=ctx_ssl, timeout=10)
    except Exception:
        pass

def run_autonomous_market_open():
    print("============================================================")
    print("🤖 RUNNING FULLY AUTONOMOUS MARKET OPEN DISPATCH (20:30 ICT)")
    print("============================================================")

    now = datetime.datetime.now()
    now_ict = now.strftime("%Y-%m-%d %H:%M ICT")
    weekday_idx = now.weekday() # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri

    # Target Setup Catalog by Weekday (Fallback baseline)
    setups = {
        0: {"candidates": [{"symbol": "LMT", "short_strike": 545.0, "width": 5.0}], "account": "pion_main", "contracts": 2, "name": "Monday LMT Entry"},
        1: {"candidates": [{"symbol": "AVGO", "short_strike": 165.0, "width": 5.0}, {"symbol": "JPM", "short_strike": 210.0, "width": 5.0}], "account": "pion_main", "contracts": 2, "name": "Tuesday AVGO/JPM Entry"},
        2: {"action": "HARVEST", "name": "Wednesday Fast Harvest"},
        3: {"candidates": [{"symbol": "XLU", "short_strike": 55.0, "width": 5.0}, {"symbol": "GLD", "short_strike": 230.0, "width": 5.0}], "account": "pion_main", "contracts": 3, "name": "Thursday XLU Entry (3C Recovery Sizing)"},
        4: {"candidates": [{"symbol": "GLD", "short_strike": 230.0, "width": 5.0}, {"symbol": "SPY", "short_strike": 735.0, "width": 5.0}], "account": "pion2_sub", "contracts": 1, "name": "Friday Weekend Theta Decay Entry"}
    }

    target = setups.get(weekday_idx, None)

    # Dynamic Screener Ingestion: Override with Live Quant Screener if available
    base_dir = Path("/home/ubuntu/shared")
    if not base_dir.exists(): base_dir = Path("/Users/SkonP/AI_Prompt/Obsidient/SkonVault/shared")
    dyn_file = base_dir / "tonight_selected_target.json"
    
    if dyn_file.exists() and weekday_idx != 2: # Keep Wednesday purely for harvesting
        try:
            dyndata = json.loads(dyn_file.read_text(encoding="utf-8"))
            prim = dyndata.get("primary") or dyndata.get("primary_selection")
            falls = dyndata.get("fallbacks") or dyndata.get("waterfall_fallbacks", [])
            if prim:
                cands = [prim] + falls
                assigned_acct = prim.get("assigned_account", "pion_main")
                width = float(prim.get("width", 5.0))
                
                # Dynamic 35% Capital Envelope Sizing (Fast-Track Acceleration Blueprint)
                # Pion Main ($11,705 Cash): $4,000 risk envelope = 8C for $5 width, 20C for $2 width
                # Pion2 Sub ($3,416 Cash): $1,200 risk envelope = 6C for $2 width, 2C for $5 width
                if assigned_acct == "pion_main":
                    contracts = int(4000.0 / (width * 100)) if width > 0 else 8
                else:
                    contracts = int(1200.0 / (width * 100)) if width > 0 else 2
                
                contracts = max(1, min(contracts, 10)) # Safety bound
                
                target = {
                    "candidates": cands,
                    "account": assigned_acct,
                    "contracts": contracts,
                    "name": f"Dynamic Quant Lead: {prim['symbol']} ({prim.get('name', prim['symbol'])}) [Score: {prim.get('total_score', 90)}] | Sizing: {contracts}C"
                }
                print(f"  🧠 Loaded Dynamic Quant Candidate Waterfall: Lead {prim['symbol']} (Score: {prim.get('total_score')} pts | Account: {assigned_acct.upper()} | Sizing: {contracts}C)")
        except Exception as ex_dyn:
            print(f"  ℹ️ Dynamic target load notice: {ex_dyn}")

    if not target:
        print("  ℹ️ No scheduled automated entry for today.")
        return

    # Handle HARVEST action
    if target.get("action") == "HARVEST":
        print("  🌾 Wednesday Fast-Harvest Active — Executing live take-profit orders on Alpaca...")
        run_all_harvests()
        return

    target_acct = target["account"]
    cand_list = target["candidates"]
    lead_sym = cand_list[0]["symbol"]
    contracts = target.get("contracts", 2)

    # 0. Pre-Flight Harvest Sweep: Auto-clear orphaned legs or ready profits at 20:30 ICT
    try:
        run_all_harvests()
    except Exception: pass

    print(f"  • Day Target       : {target['name']} ({lead_sym}) — {contracts} Contracts")
    print(f"  • Target Account   : {target_acct.upper()}")

    # 1. Connect to Centralized Broker Client
    try:
        broker = AlpacaClient(target_acct)
        acct_info = broker.get_account()
        acct_num = acct_info.get("account_number", "")
        print(f"  ✅ Authenticated to Broker: {acct_num} (Equity: ${float(acct_info.get('equity', 0)):,.2f})")
    except AlpacaAuthError as e:
        print(f"  🔴 FATAL AUTH ERROR: {e}")
        _send_telegram_alert(f"⚠️ AUTONOMOUS EXECUTION BLOCKED: Authentication failure on {target_acct}: {e}")
        return

    # 2. Execute via Autonomous Waterfall Engine (RULE-048 & RULE-049)
    print(f"\n  ⚡ Submitting {contracts}-Contract Spread via Autonomous Waterfall...")
    success, res_data, msg = broker.execute_waterfall_spread(cand_list, contracts=contracts)

    if not success or not res_data:
        print(f"  🔴 WATERFALL EXECUTION FAILED: {msg}")
        _send_telegram_alert(f"⚠️ BROKER ORDER REJECTED on {target_acct}:\n{msg}")
        return

    sym_filled = res_data.get("symbol")
    short_strike = res_data.get("short_strike")
    long_strike = res_data.get("long_strike")
    exp_date = res_data.get("exp_date")
    order_ids = res_data.get("order_ids", [])
    print(f"  🎉 SUCCESS: {msg}")

    # RULE-053: Autonomous Self-Healing Verification
    try:
        healed = broker.auto_heal_unmatched_positions()
        if healed:
            print(f"  🩺 Healed actions: {healed}")
    except Exception: pass

    # 3. Update active_trades.json
    base_dir = Path("/home/ubuntu/shared")
    if not base_dir.exists(): base_dir = Path("/Users/SkonP/AI_Prompt/Obsidient/SkonVault/shared")
    trades_file = base_dir / "active_trades.json"
    if trades_file.exists():
        try:
            tdata = json.loads(trades_file.read_text(encoding="utf-8"))
            accts = tdata.get("accounts", {})
            acct_data = accts.get(target_acct, {"positions": []})
            acct_data.setdefault("positions", []).append({
                "symbol": sym_filled,
                "strategy": f"Bull Put Spread ({short_strike:.0f}/{long_strike:.0f})",
                "short_strike": short_strike,
                "long_strike": long_strike,
                "contracts": contracts,
                "entry_date": now.strftime("%Y-%m-%d"),
                "expiration": exp_date,
                "alpaca_order_ids": order_ids,
                "unrealized_profit_pct": 0.0,
                "status": "OPEN_ACTIVE",
                "mode": "24H_EXPRESS_OPTION_A",
                "harvest_target": "50% Take-Profit"
            })
            accts[target_acct] = acct_data
            tdata["accounts"] = accts
            trades_file.write_text(json.dumps(tdata, indent=2), encoding="utf-8")
        except Exception: pass

    # 4. Telegram Notification
    total_risk = contracts * (short_strike - long_strike) * 100
    alert_text = f"""🔔 AUTONOMOUS MARKET OPEN FILL CONFIRMATION — {now_ict}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚀 NEW TRADE EXECUTED ON BROKER: {sym_filled}
• Asset          : {sym_filled} ({contracts}-Contract Bull Put Spread)
• Strikes        : ${short_strike:.0f}P / ${long_strike:.0f}P
• Target Account : {target_acct.upper()} ({broker.expected_account_id})
• Expiration     : {exp_date}
• Defined Width  : ${short_strike - long_strike:.2f} (${total_risk:,.0f} Total Risk Envelope)
• Alpaca Status  : LIVE ORDER FILLED ON BROKER API ✅
• Alpaca Order IDs: {', '.join(order_ids) if order_ids else 'N/A'}
• Mode           : 24h Express Option A Active ⚡

Top Positions on {target_acct.upper()} dashboard is now populated! 🚀📈"""
    _send_telegram_alert(alert_text)

    # 5. Mirror to Anna via AgentBridge
    try:
        bridge = AgentBridge("hermes")
        bridge.send("anna", alert_text, channel="trade_execution", subject=f"TRADE_FILLED_{sym_filled}")
    except Exception: pass

    # 6. Autonomous Excel Transaction Journal Update (RULE-051)
    try:
        import transaction_journal_manager
        transaction_journal_manager.update_excel_journal()
        print("  📊 Updated SkonVault_Live_Transaction_Journal.xlsx with new market open entry!")
    except Exception as ex_j:
        print(f"  ℹ️ Journal sync notice: {ex_j}")

if __name__ == "__main__":
    run_autonomous_market_open()
