#!/usr/bin/env python3
"""
execute_golden_2115_daily_entry.py — Master 21:15 ICT Autonomous Institutional Execution Engine

Features:
1. Optimal Timing: Fires at 21:15 ICT (10:15 EST) immediately following the 21:10 ICT UOA Sweep #1.
2. Max Safe Allocation (RULE-072 Accelerated Sprint): Sizes up to 65% capital envelope ($6,500 Pion Main/Live, $2,500 Pion2 Sub) while keeping 35% permanent cash defense floor (Tumbler 5) with >=92% target win rate.
3. Pre-Flight Liquidity Gate (RULE-052): Verifies live Bid >= $0.15 on short strikes and calculates Natural Net Credit.
4. Native Atomic Multi-Leg (RULE-047): Uses order_class: "mleg" with sequential fallback.
5. Autonomous Self-Healing (RULE-053): T+10s verification sweep pairs or liquidates orphaned single legs.
6. Excel Journal Auto-Logging (RULE-051): Automatically records entries in SkonVault_Live_Transaction_Journal.xlsx.
"""

import sys
import os
import json
import datetime
import time
import urllib.request
import ssl
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from alpaca_broker import AlpacaClient
from auto_harvest_positions import run_all_harvests
from transaction_journal_manager import update_excel_journal

TELEGRAM_BOT_TOKEN = "8991076258:AAGHyb-jEnp29BQ5O5V0mo4vFOmgXfgnmTg"
TELEGRAM_CHAT_ID = "-1004375899205"

def _send_telegram(message: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)
    try:
        ctx_ssl = ssl._create_unverified_context()
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message}
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, context=ctx_ssl, timeout=10) as r:
            pass
        print("  ✅ Dispatched 21:15 Execution Notice to Telegram Group!")
    except Exception as ex_t:
        print(f"  ℹ️ Telegram notice: {ex_t}")

def _broadcast_to_anna(subject: str, message: str, payload: Optional[Dict[str, Any]] = None):
    """RULE-083: Mirrors all entry attempts, skips, and fills to Anna via AgentBridge."""
    try:
        from agent_bridge import AgentBridge
        bridge = AgentBridge("hermes")
        body_text = message
        if payload:
            body_text += "\n\nJSON_METADATA:\n" + json.dumps(payload, indent=2)
        bridge.send("anna", body_text, channel="trade_execution", subject=subject)
        print(f"  ⚡ Mirrored entry event '{subject}' to Anna via AgentBridge!")
    except Exception as ex_b:
        print(f"  ℹ️ Bridge mirror notice: {ex_b}")

    # Persist latest entry state to last_entry_status.json
    try:
        base_dir = Path("/home/ubuntu/shared")
        if not base_dir.exists(): base_dir = Path(__file__).parent
        status_file = base_dir / "last_entry_status.json"
        data = {
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT"),
            "subject": subject,
            "message": message,
            "payload": payload or {}
        }
        status_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print("  💾 Saved entry status to last_entry_status.json!")
    except Exception: pass

def calculate_conviction_tiered_sizing(
    candidate: Dict[str, Any],
    cash: float,
    bp: float,
    account_name: str,
    margin_ceiling_pct: float = 0.65
) -> Tuple[int, float, str]:
    """
    DIR-10 / RULE-055: Quantitative Conviction-Tiered Capital Allocation Engine.
    Sizing dynamically scales based on candidate score and asset class,
    honoring the 35% Cash Defense Floor and 5-minute Strike Sentinel protection.

    Tier 3 (Apex >= 90.0 pts): Full Tranche ($5,000-$6,500 Alpaca / $400-$500 Tradier)
    Tier 2 (Solid 80.0-89.9 pts): Medium Tranche ($3,000-$3,500 Alpaca / $200-$300 Tradier)
    Tier 1 (Defensive < 80.0 pts): Controlled Pilot ($1,500 Alpaca / $100 Tradier)
    """
    sym = candidate.get("symbol", "")
    score = float(candidate.get("effective_score") or candidate.get("total_score") or candidate.get("score") or 80.0)
    width = float(candidate.get("width", 5.0))
    if width <= 0:
        width = 5.0

    is_live_alpaca = account_name in ["pion_main", "alpaca_live", "live"]
    is_tradier = "tradier" in account_name
    is_broad_index = sym in ["SPY", "QQQ", "IWM"]

    # Institutional Single-Asset Cap: Max 35% of total capital in any single asset
    single_asset_cap = cash * 0.35

    if score >= 85.0 or is_broad_index:
        tier_label = "TIER 3: APEX CONVICTION / INDEX ENVELOPE (MAX 3C TRANCHE 🚀)" if is_broad_index else "TIER 3: APEX CONVICTION (SWEET SPOT MAX 3C TRANCHE 🚀)"
        # 3 contracts on $10w = $3,000 risk (~9.5% capital); up to $6,000 max tranche on $20w
        max_tranche = min(6000.0, single_asset_cap) if is_live_alpaca else (500.0 if is_tradier else 3000.0)
        max_contracts = 3 if is_live_alpaca else (1 if width >= 5.0 else 2)
    elif score >= 75.0:
        tier_label = "TIER 2: SOLID PRODUCTION (STANDARD 2-3C TRANCHE ⚖️)"
        max_tranche = min(3500.0, single_asset_cap) if is_live_alpaca else (350.0 if is_tradier else 1500.0)
        max_contracts = 3 if is_live_alpaca else (1 if width >= 5.0 else 2)
    else:
        tier_label = "TIER 1: DEFENSIVE PILOT (CONTROLLED 1C PROBE 🛡️)"
        max_tranche = min(1500.0, single_asset_cap) if is_live_alpaca else (150.0 if is_tradier else 1000.0)
        max_contracts = 1 if is_live_alpaca else 1

    # Absolute bounds: Never exceed 35% single-asset cap, 65% total cash envelope, or buying power
    target_risk = min(max_tranche, single_asset_cap, cash * margin_ceiling_pct, bp)
    contracts = max(1, int(target_risk / (width * 100.0)))
    contracts = min(contracts, max_contracts, 3)  # Hard Institutional Floor/Ceiling: Max 3 Contracts
    if is_tradier:
        # Sweet-Spot Fee Efficiency: 1 contract on $5w/$10w, max 2 contracts on $2w
        contracts = min(contracts, 1 if width >= 5.0 else 2)

    return contracts, target_risk, tier_label

def execute_account_entry(
    account_name: str,
    candidate_list: List[Dict[str, Any]],
    now_ict: str,
    base_dir: Path,
    portfolio_tickers: set
) -> Optional[Dict[str, Any]]:
    """
    RULE-091: Executes institutional entry for a designated account (pion_main or pion2_sub).
    - pion_main: Core Anchor (30-45 DTE, max 3 active spreads, max safe allocation up to $4,000)
    - pion2_sub: Short-Cycle Sprint (7-14 DTE, max 2 active spreads, max safe allocation up to $1,200)
    """
    broker = AlpacaClient(account_name)
    try:
        acct_info = broker.get_account()
        cash = float(acct_info.get("cash", 0))
        equity = float(acct_info.get("equity", 0))
        bp = float(acct_info.get("buying_power", 0))
        acct_num = acct_info.get("account_number", "")
        print(f"  ✅ Authenticated to {account_name.upper()} ({acct_num}): Cash: ${cash:,.2f} | BP: ${bp:,.2f}")
    except Exception as ex_auth:
        print(f"  🔴 Authentication Error on {account_name}: {ex_auth}")
        _send_telegram(f"⚠️ 21:15 ICT EXECUTION BLOCKED: Auth failure on {account_name}: {ex_auth}")
        _broadcast_to_anna("AUTH_FAILURE", str(ex_auth), {"account": account_name})
        return None

    # Audit existing spreads and pending resting orders
    pos = broker.get_positions()
    short_puts = [p for p in pos if float(p.get("qty", 0)) < 0 and "P0" in p.get("symbol", "")]
    active_spread_count = len(short_puts)

    pending_count = 0
    try:
        from order_fill_tracker import load_pending_orders
        pending = load_pending_orders()
        pending_count = len([
            o for o in pending
            if o.get("account") == account_name and str(o.get("status", "")).lower() in ["working", "new", "pending", "resting"]
        ])
    except Exception:
        pass

    if account_name == "alpaca_live":
        max_spreads = 5  # Option A: 5 Core Barbell slots ($19,691 max envelope, $11,249 cash floor)
        min_bp_required = 1500.0
    elif account_name == "tradier_live":
        max_spreads = 2
        min_bp_required = 500.0
    elif account_name == "pion_main":
        max_spreads = 4
        min_bp_required = 1000.0
    else:  # pion2_sub
        max_spreads = 2
        min_bp_required = 500.0

    if (active_spread_count + pending_count) >= max_spreads:
        print(f"  🛑 {account_name.upper()} AT CAPACITY: {active_spread_count} active spreads + {pending_count} pending orders >= {max_spreads} max slot limit. Standing down cleanly.")
        return None

    if bp < min_bp_required:
        print(f"  🛑 {account_name.upper()} INSUFFICIENT BUYING POWER: ${bp:,.2f} < ${min_bp_required:,.2f} threshold. Standing down cleanly.")
        return None

    # Filter out tickers that reached maximum concentration cap (MAX_PER_TICKER = 3)
    MAX_PER_TICKER = 3
    available_cands = []
    for c in candidate_list:
        sym = c.get("symbol")
        held_count = list(portfolio_tickers).count(sym) if isinstance(portfolio_tickers, (set, list, tuple)) else (1 if sym in portfolio_tickers else 0)
        if held_count >= MAX_PER_TICKER:
            continue
        
        # Calculate Effective Penalized Score (RULE-072 Merit-Based Progressive Scaling)
        base_sc = float(c.get("total_score", 0.0))
        penalty = 0.0
        if held_count == 1:
            penalty = 15.0  # Tranche 2 re-entry penalty (-15 pts)
        elif held_count == 2:
            penalty = 25.0  # Tranche 3 re-entry penalty (-25 pts)
            
        c_copy = dict(c)
        c_copy["effective_score"] = round(base_sc - penalty, 2)
        c_copy["reentry_penalty"] = penalty
        if penalty > 0:
            print(f"  ⚖️ RE-ENTRY CANDIDATE: {sym} held ({held_count}x) -> Base: {base_sc:.1f} - {penalty:.0f}pt penalty = Effective Score: {c_copy['effective_score']:.1f}")
        available_cands.append(c_copy)

    if not available_cands:
        print(f"  ℹ️ {account_name.upper()}: All candidates reached concentration cap ({portfolio_tickers}). Standing down cleanly.")
        return None

    # Account-specific candidate sorting by Effective Score (RULE-091 & Merit-First)
    if account_name in ["pion2_sub", "tradier_live"]:
        # Prioritize Sprint candidates (ETFs, commodities, high-velocity beta, DTE <= 16)
        sprint_syms = {"GLD", "XLF", "IWM", "AMD", "SLV", "XLU", "XLE"}
        available_cands = sorted(
            available_cands,
            key=lambda c: (
                0 if c.get("symbol") in sprint_syms or c.get("assigned_account") == "pion2_sub" or float(c.get("width", 5.0)) <= 2.0 or int(c.get("dte", 21)) <= 16 else 1,
                -float(c.get("effective_score", 0.0))
            )
        )
    else:
        # Prioritize Core Anchor candidates (mega-cap tech, broad index)
        core_syms = {"QQQ", "NVDA", "SPY", "MSFT", "META", "V", "AVGO", "LMT"}
        available_cands = sorted(
            available_cands,
            key=lambda c: (
                0 if c.get("symbol") in core_syms or c.get("assigned_account") == "pion_main" else 1,
                -float(c.get("effective_score", 0.0))
            )
        )

    # RULE-087 MANDATORY OTM PRE-FLIGHT SANITY FILTER:
    valid_otm_cands = []
    for c in available_cands:
        sym = c.get("symbol")
        s_strike = float(c.get("short_strike", 0))
        cur_spot = 0.0
        try:
            from live_spot import get_spot
            spot_res = get_spot(sym)
            if isinstance(spot_res, dict):
                cur_spot = float(spot_res.get("price") or 0.0)
            else:
                cur_spot = float(spot_res or 0.0)
        except Exception:
            pass

        if cur_spot <= 0.0:
            try:
                bbar = broker.get_latest_bar(sym) if hasattr(broker, 'get_latest_bar') else None
                cur_spot = float(bbar.get("c", 0.0)) if bbar else 0.0
            except Exception:
                cur_spot = 0.0

        if cur_spot > 0:
            buf_pct = ((cur_spot - s_strike) / cur_spot) * 100.0
            if s_strike >= (cur_spot * 0.98) or buf_pct < 2.0:
                print(f"  🛑 RULE-087 SANITY REJECTED {sym} on {account_name}: Short strike ${s_strike:.2f} vs live spot ${cur_spot:.2f} (Buffer: {buf_pct:+.1f}% < 2.0% min OTM)!")
                continue

            # DIR-11: Hard pre-selection width & credit floors
            cand_w = float(c.get("width") or (s_strike - float(c.get("long_strike", 0))))
            cand_cr = float(c.get("natural_credit") or c.get("mid_credit") or c.get("credit_mid") or 0.0)
            if cand_w > 0 and cand_w < 2.0:
                print(f"  🛑 DIR-11 REJECTED {sym} on {account_name}: Spread width ${cand_w:.2f} < $2.00 minimum floor!")
                continue
            if cand_cr > 0 and cand_cr < 0.25:
                print(f"  🛑 DIR-11 REJECTED {sym} on {account_name}: Candidate credit ${cand_cr:.2f} < $0.25 minimum floor!")
                continue

            c["live_spot"] = cur_spot
            c["buffer_pct"] = buf_pct
            c["inception_spot"] = cur_spot
            c["inception_buffer_pct"] = round(buf_pct, 2)
            valid_otm_cands.append(c)
        else:
            print(f"  🛑 RULE-087 ZERO SPOT GUARD: Cannot verify live spot for {sym}! Rejecting to prevent blind execution.")
            continue

    if not valid_otm_cands:
        print(f"  🛑 RULE-087 ERROR: Zero candidates passed OTM sanity gate for {account_name.upper()}! Standing down cleanly.")
        return None

    # Dynamic Regime-Adaptive Tuning (Market-Aware Rule & RULE-072):
    from dynamic_regime_manager import evaluate_market_regime
    regime_info = evaluate_market_regime()
    margin_ceiling_pct = regime_info.get("margin_ceiling_pct", 0.65)
    cash_defense_floor_pct = regime_info.get("cash_defense_floor_pct", 0.35)

    width = float(valid_otm_cands[0].get("width", 5.0))
    contracts, target_risk, tier_label = calculate_conviction_tiered_sizing(
        candidate=valid_otm_cands[0],
        cash=cash,
        bp=bp,
        account_name=account_name,
        margin_ceiling_pct=margin_ceiling_pct
    )

    if bp < (width * 100.0):
        print(f"  🛑 {account_name.upper()}: Buying power ${bp:,.2f} insufficient for 1 contract (requires ${width * 100:,.2f}).")
        return None

    print(f"  📐 DYNAMIC CONVICTION SIZING ({account_name.upper()} | {regime_info['regime_id']}): {contracts} Contracts (${contracts * width * 100:,.2f} Defined Risk = {contracts * width * 100 / cash * 100:.1f}% Cash Allocation | {cash_defense_floor_pct*100:.0f}% Permanent Cash Floor | VIX {regime_info['vix']:.2f})")
    print(f"     • Sizing Protocol: {tier_label}")

    # Clean orphaned legs
    try:
        broker.auto_heal_unmatched_positions()
    except Exception: pass

    # Tier 3: Ingest Pre-Warmed Entry Payload if fresh (< 600s)
    prewarmed_payload = None
    pw_file = base_dir / "prewarmed_entry_payload.json"
    if pw_file.exists():
        try:
            pw_data = json.loads(pw_file.read_text(encoding="utf-8"))
            pw_age = time.time() - float(pw_data.get("timestamp_epoch", 0))
            if pw_age < 600 and pw_data.get("symbol") == valid_otm_cands[0].get("symbol"):
                prewarmed_payload = pw_data
                print(f"  ⚡ TIER 3 PRE-WARMED PAYLOAD DETECTED: {prewarmed_payload.get('symbol')} ({prewarmed_payload.get('short_sym')}/{prewarmed_payload.get('long_sym')} @ ${prewarmed_payload.get('prewarmed_snipe_credit', 0):.2f}) | Age: {pw_age:.1f}s. Dispatching with 0.00s pre-processing lag!")
            else:
                if pw_age >= 600:
                    print(f"  ℹ️ Pre-warmed payload aged {pw_age:.0f}s >= 600s. Bypassing stale payload.")
        except Exception as ex_pw_load:
            print(f"  ℹ️ Notice loading pre-warmed payload: {ex_pw_load}")

    # Execute via Autonomous Waterfall Engine (RULE-052 & RULE-047, with Tier 3 fast-path)
    print(f"\n  ⚡ Submitting {contracts}-Contract Spread on {account_name.upper()} via Waterfall Engine...")
    success, res_data, msg = broker.execute_waterfall_spread(valid_otm_cands, contracts=contracts, prewarmed_payload=prewarmed_payload)

    if not success or not res_data:
        print(f"  🔴 WATERFALL EXECUTION FAILED on {account_name.upper()}: {msg}")
        _send_telegram(f"⚠️ 21:15 ICT ORDER REJECTED on {account_name.upper()}:\n{msg}")
        _broadcast_to_anna("WATERFALL_EXECUTION_FAILED", msg, {"account": account_name, "candidates": [c.get("symbol") for c in valid_otm_cands]})
        return None

    sym_filled = res_data.get("symbol")
    short_strike = res_data.get("short_strike")
    long_strike = res_data.get("long_strike")
    short_sym = res_data.get("short_sym", "")
    long_sym = res_data.get("long_sym", "")
    width = float(res_data.get("width", short_strike - long_strike))
    exp_date = res_data.get("exp_date")
    order_ids = res_data.get("order_ids", [])
    limit_credit = float(res_data.get("limit_credit", 0.15))
    total_risk = contracts * width * 100
    main_oid = order_ids[0] if order_ids else ""

    print(f"  🎉 SUCCESS on {account_name.upper()}: {msg}")

    # RULE-053: Autonomous Self-Healing Verification
    try:
        healed = broker.auto_heal_unmatched_positions()
        if healed:
            print(f"  🩺 RULE-053 Healed Actions on {account_name.upper()}: {healed}")
    except Exception: pass

    # Check Real-Time Broker Order Status (RULE-075 & RULE-084)
    is_filled_now = res_data.get("is_filled_now", False)
    order_info = broker.get_order(main_oid) if main_oid else {}
    order_status = order_info.get("status", "new")
    filled_qty = float(order_info.get("filled_qty", 0))

    if is_filled_now or order_status == "filled" or filled_qty >= contracts:
        # CASE A: Micro-Walk Fill Confirmed (RULE-084)
        fill_price = abs(float(order_info.get("filled_avg_price") or res_data.get("filled_avg_price") or limit_credit))
        from order_fill_tracker import record_confirmed_fill
        dummy_order = {
            "order_id": main_oid, "account": account_name, "symbol": sym_filled,
            "short_strike": short_strike, "long_strike": long_strike,
            "contracts": contracts, "exp_date": exp_date
        }
        record_confirmed_fill(dummy_order, fill_price)

        alert_text = f"""🔔 AUTONOMOUS 21:15 ICT GOLDEN ENTRY FILL — {now_ict}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚀 NEW TRADE EXECUTED ON BROKER: {sym_filled} (RULE-084 ACTIVE MICRO-WALK)
• Asset          : {sym_filled} ({contracts}-Contract Bull Put Spread)
• Strikes        : ${short_strike:.0f}P / ${long_strike:.0f}P (Width: ${width:.2f})
• Target Account : {account_name.upper()} ({acct_num})
• Expiration     : {exp_date}
• Defined Risk   : ${total_risk:,.2f} (RULE-072 Sprint: 65% Utilization / 35% Reserved 📐)
• Alpaca Status  : LIVE ORDER FILLED IN MICRO-WALK ✅
• Alpaca Order ID: {main_oid}
• Execution Price: +${fill_price:.2f} / share
• Journal Status : Auto-Logged to SkonVault_Live_Transaction_Journal.xlsx 📊

Position is active and capturing theta decay! 🚀📈"""
        _send_telegram(alert_text)
        _broadcast_to_anna(f"TRADE_FILLED_{sym_filled}", alert_text, {
            "symbol": sym_filled,
            "status": "FILLED",
            "account": account_name,
            "order_id": main_oid,
            "price": fill_price
        })

        try:
            last_entry_file = base_dir / "last_entry_status.json"
            last_entry_file.write_text(json.dumps({
                "timestamp": now_ict,
                "subject": f"ORDER_FILLED_{sym_filled}",
                "message": f"Micro-Walk FILLED: {contracts}C {sym_filled} (${short_strike:.0f}P/${long_strike:.0f}P) @ +${fill_price:.2f}/sh in {account_name.upper()}!"
            }, indent=2), encoding="utf-8")
        except Exception: pass
        print(f"  📢 Immediate Micro-Walk Fill Telegram & Bridge Confirmation Dispatched for {account_name.upper()}!")

    else:
        # CASE B: Resting on Exchange Book -> Register with RULE-075 3-Stage Tracker
        from order_fill_tracker import register_order
        register_order(
            account=account_name,
            symbol=sym_filled,
            short_sym=short_sym,
            long_sym=long_sym,
            short_strike=short_strike,
            long_strike=long_strike,
            width=width,
            contracts=contracts,
            exp_date=exp_date,
            order_id=main_oid,
            limit_credit=limit_credit,
            broker_type="alpaca"
        )

        alert_text = f"""🚀 AUTONOMOUS 21:15 ICT GOLDEN ENTRY SUBMITTED — {now_ict}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 PASSIVE SPREAD RESTING ON BOOK (RULE-084)
• Asset          : {sym_filled} ({contracts}-Contract Bull Put Spread)
• Strikes        : ${short_strike:.0f}P / ${long_strike:.0f}P (Width: ${width:.2f})
• Conviction Tier: {tier_label}
• Target Account : {account_name.upper()} ({acct_num})
• Expiration     : {exp_date}
• Current Limit  : +${limit_credit:.2f} Credit
• Defined Risk   : ${total_risk - contracts * limit_credit * 100:,.2f}
• Alpaca Order ID: {main_oid}
• Order Status   : WORKING / RESTING ON COMPLEX BOOK ⏳
• Tracker Engine : RULE-075 / RULE-084 Adaptive Fill Protocol Armed
  * Stage 1 (21:30 ICT): Micro-Nudge & Delta-Drift Monitor
  * Stage 2 (21:46 ICT): UOA #2 Conviction Gate (Marketable Limit)
  * Stage 3 (22:00 ICT): Hard Standoff (Zero Overnight Ghosts)

Zero phantom trades logged. Awaiting confirmed broker fill! 🛡️"""
        _send_telegram(alert_text)
        _broadcast_to_anna(f"ORDER_RESTING_{sym_filled}", alert_text, {
            "symbol": sym_filled,
            "status": "RESTING",
            "account": account_name,
            "order_id": main_oid,
            "limit_credit": limit_credit
        })

        try:
            last_entry_file = base_dir / "last_entry_status.json"
            last_entry_file.write_text(json.dumps({
                "timestamp": now_ict,
                "subject": f"ORDER_RESTING_{sym_filled}",
                "message": alert_text
            }, indent=2), encoding="utf-8")
        except Exception: pass
        print(f"  📢 Passive Handoff Telegram & Bridge Alert Dispatched for {account_name.upper()}!")

    return res_data

def execute_tradier_live_entry(
    candidate_list: List[Dict[str, Any]],
    now_ict: str,
    base_dir: Path,
    portfolio_tickers: set
) -> Optional[Dict[str, Any]]:
    """
    DIR-04 & RULE-073: High-Velocity Satellite Sprint Entry for Tradier Live (#6YB80974).
    Enforces max 2 active sprint slots ($1,200 max margin) with atomic multi-leg execution.
    """
    from tradier_broker import TradierClient
    client = TradierClient("live")
    try:
        acct_info = client.get_account()
        cash = float(acct_info.get("cash", 0))
        bp = float(acct_info.get("option_buying_power", 0))
        acct_num = acct_info.get("account_number", "6YB80974")
        print(f"  ✅ Authenticated to TRADIER_LIVE ({acct_num}): Cash: ${cash:,.2f} | BP: ${bp:,.2f}")
    except Exception as ex_auth:
        print(f"  🔴 Authentication Error on Tradier Live: {ex_auth}")
        _send_telegram(f"⚠️ 21:15 ICT EXECUTION BLOCKED: Auth failure on Tradier Live: {ex_auth}")
        _broadcast_to_anna("AUTH_FAILURE", str(ex_auth), {"account": "tradier_live"})
        return None

    # Audit active positions
    pos = client.get_positions()
    short_puts = [p for p in pos if float(p.get("quantity", 0)) < 0 and "P0" in p.get("symbol", "")]
    active_spread_count = len(short_puts)
    max_spreads = 2
    min_bp_required = 500.0

    if active_spread_count >= max_spreads:
        print(f"  🛑 TRADIER_LIVE AT CAPACITY: {active_spread_count} active spreads >= {max_spreads} slot limit. Standing down.")
        return None

    if bp < min_bp_required:
        print(f"  🛑 TRADIER_LIVE INSUFFICIENT BUYING POWER: ${bp:,.2f} < ${min_bp_required:,.2f}. Standing down.")
        return None

    # Filter candidates: prioritize Sprint assets (GLD, IWM, AMD, SLV, XLU, XLE, XLF, SPY, QQQ) or short DTE tenors (10-16 DTE)
    sprint_syms = {"GLD", "XLF", "IWM", "AMD", "SLV", "XLU", "XLE", "SPY", "QQQ"}
    sprint_cands = [
        c for c in candidate_list
        if c.get("symbol") not in portfolio_tickers and (c.get("symbol") in sprint_syms or int(c.get("dte", 21)) <= 16)
    ]
    if not sprint_cands:
        sprint_cands = [c for c in candidate_list if c.get("symbol") not in portfolio_tickers]

    if not sprint_cands:
        print("  ℹ️ TRADIER_LIVE: Zero eligible Sprint candidates available. Standing down cleanly.")
        return None

    sprint_cands = sorted(sprint_cands, key=lambda c: -float(c.get("total_score", 0.0)))
    target_cand = sprint_cands[0]
    sym = target_cand.get("symbol")

    # Resolve target strikes & expiration under Executive Efficiency Principle:
    # Favor wider spreads ($3.00 to $5.00 width) with 1 single contract to eliminate per-contract fee drag
    short_strike = float(target_cand.get("short_strike", 0))
    cand_width = float(target_cand.get("width", 0) or 0)
    if 2.0 <= cand_width <= 5.0:
        width = cand_width
    elif cand_width > 5.0:
        width = 5.0  # Cap Tradier at $5.0 max width for $500 max slot risk envelope
    else:
        width = 2.0 if sym in ["XLF", "SLV"] else (cand_width if cand_width > 0 else 3.0)
    long_strike = short_strike - width

    # Dynamic Multi-Tenor Expiration Selection (honors screener/radar selection or targets 10-14 DTE sprint)
    expirations = client.get_option_expirations(sym)
    cand_exp = target_cand.get("expiration")
    if cand_exp and cand_exp in expirations:
        exp_date = cand_exp
    else:
        # Resolve closest available expiration to Sprint target (10-14 DTE)
        today = datetime.date.today()
        dtes = []
        for e in expirations:
            try:
                dt = datetime.datetime.strptime(e, "%Y-%m-%d").date()
                d = (dt - today).days
                if 8 <= d <= 35:
                    dtes.append((e, d))
            except Exception: pass
        if dtes:
            # Prioritize 14 DTE sprint, then 10 DTE
            closest = min(dtes, key=lambda x: abs(x[1] - 14))
            exp_date = closest[0]
        else:
            exp_date = expirations[0] if expirations else "2026-10-16"

    # OCC format
    date_compact = exp_date.replace("-", "")[2:]
    strike_short_str = f"{int(short_strike * 1000):08d}"
    strike_long_str = f"{int(long_strike * 1000):08d}"
    short_occ = f"{sym}{date_compact}P{strike_short_str}"
    long_occ = f"{sym}{date_compact}P{strike_long_str}"

    # Query quotes & calculate midpoint credit from live chain
    try:
        chain = client.get_option_chain(sym, exp_date, greeks=True)
        s_opt = next((o for o in chain if o.get("symbol") == short_occ), {})
        l_opt = next((o for o in chain if o.get("symbol") == long_occ), {})
        if not s_opt:
            s_opt = next((o for o in chain if o.get("option_type") == "put" and abs(float(o.get("strike", 0)) - short_strike) < 0.05), {})
            if s_opt: short_occ = s_opt.get("symbol", short_occ)
        if not l_opt:
            l_opt = next((o for o in chain if o.get("option_type") == "put" and abs(float(o.get("strike", 0)) - long_strike) < 0.05), {})
            if l_opt: long_occ = l_opt.get("symbol", long_occ)

        s_bid = float(s_opt.get("bid") or 0.0)
        s_ask = float(s_opt.get("ask") or 0.0)
        l_bid = float(l_opt.get("bid") or 0.0)
        l_ask = float(l_opt.get("ask") or 0.0)
        if s_bid > 0 and l_ask > 0:
            short_mid = (s_bid + s_ask) / 2.0
            long_mid = (l_bid + l_ask) / 2.0
            limit_credit = max(0.05, round(short_mid - long_mid, 2))
        else:
            limit_credit = float(target_cand.get("natural_credit") or target_cand.get("credit_mid") or (0.25 if width <= 2.0 else 0.65))
    except Exception as ex_chain:
        print(f"  ⚠️ Tradier chain fetch notice: {ex_chain}")
        limit_credit = float(target_cand.get("natural_credit") or target_cand.get("credit_mid") or (0.25 if width <= 2.0 else 0.65))

    # DIR-11: Hard Pre-Submission Veto (Reject Micro-Credit & Sub-$2 Width Traps)
    MIN_ENTRY_CREDIT = 0.25
    if limit_credit < MIN_ENTRY_CREDIT:
        print(f"  🛑 EXECUTION VETO (DIR-11): Resolved limit credit ${limit_credit:.2f} < ${MIN_ENTRY_CREDIT:.2f} floor! Aborting Tradier Live submission.")
        _send_telegram(f"⚠️ Tradier Live 21:15 ICT Order Aborted: {sym} credit ${limit_credit:.2f} < ${MIN_ENTRY_CREDIT:.2f} (DIR-11 floor)")
        return {"symbol": sym, "order_id": None, "status": "rejected_micro_credit", "credit": limit_credit, "account": "tradier_live"}
    if width < 2.0:
        print(f"  🛑 EXECUTION VETO (DIR-11): Spread width ${width:.2f} < $2.00 floor! Aborting Tradier Live submission.")
        return {"symbol": sym, "order_id": None, "status": "rejected_narrow_width", "width": width, "account": "tradier_live"}

    # Dynamic Conviction-Tiered Sizing for Tradier Live Sprint (DIR-10 & RULE-055)
    # Executive Efficiency Principle: Pass exact resolved width to ensure 1-contract wider spread execution
    from dynamic_regime_manager import evaluate_market_regime
    regime_info = evaluate_market_regime()
    margin_ceiling_pct = regime_info.get("margin_ceiling_pct", 0.65)
    cand_with_width = dict(target_cand)
    cand_with_width["width"] = width
    contracts, target_risk, tier_label = calculate_conviction_tiered_sizing(
        candidate=cand_with_width,
        cash=cash,
        bp=bp,
        account_name="tradier_live",
        margin_ceiling_pct=margin_ceiling_pct
    )
    total_risk = round((width - limit_credit) * contracts * 100.0, 2)
    print(f"  ⚡ SUBMITTING ATOMIC VERTICAL SPREAD TO TRADIER LIVE ({tier_label}):")
    print(f"     • Symbol: {sym} ({contracts}C Bull Put Spread ${short_strike:.1f}P / ${long_strike:.1f}P, Exp {exp_date})")
    print(f"     • Target Limit Credit: ${limit_credit:.2f} | Defined Risk: ${total_risk:.2f}")

    try:
        res = client.execute_vertical_spread(
            symbol=sym,
            short_occ=short_occ,
            long_occ=long_occ,
            qty=contracts,
            limit_credit=limit_credit,
            duration="day"
        )
        order_id = res.get("order_id")
        status = res.get("status")
        print(f"  🎉 TRADIER LIVE ORDER SUBMITTED! ID: {order_id} (Status: {status})")

        # Register with RULE-075 3-Stage Tracker if not filled immediately
        if status != "filled":
            try:
                from order_fill_tracker import register_order
                register_order(
                    account="tradier_live",
                    symbol=sym,
                    short_sym=short_occ,
                    long_sym=long_occ,
                    short_strike=short_strike,
                    long_strike=long_strike,
                    width=width,
                    contracts=contracts,
                    exp_date=exp_date,
                    order_id=str(order_id),
                    limit_credit=limit_credit,
                    broker_type="tradier"
                )
            except Exception as ex_reg:
                print(f"  ℹ️ Tradier order registration notice: {ex_reg}")

        alert_text = f"""🚀 AUTONOMOUS 21:15 ICT GOLDEN ENTRY SUBMITTED (TRADIER LIVE) — {now_ict}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 HIGH-VELOCITY SATELLITE SPRINT (OPTION A)
• Asset          : {sym} ({contracts}-Contract Bull Put Spread)
• Strikes        : ${short_strike:.1f}P / ${long_strike:.1f}P (Width: ${width:.2f})
• Conviction Tier: {tier_label}
• Target Account : TRADIER LIVE ({acct_num})
• Expiration     : {exp_date}
• Limit Credit   : +${limit_credit:.2f} Credit (${limit_credit * contracts * 100:.2f} total)
• Defined Risk   : ${total_risk:.2f}
• Order ID       : {order_id} ({status})
• Protocol Engine: DIR-04 Satellite Sprint Active ⚡"""
        _send_telegram(alert_text)
        _broadcast_to_anna(f"ORDER_SUBMITTED_{sym}_TRADIER", alert_text, {
            "symbol": sym,
            "account": "tradier_live",
            "order_id": order_id,
            "status": status,
            "limit_credit": limit_credit
        })
        return {"symbol": sym, "order_id": order_id, "status": status, "credit": limit_credit, "account": "tradier_live"}
    except Exception as e:
        print(f"  🔴 Tradier Live Submission Error: {e}")
        return None

def run_2115_golden_execution():
    now = datetime.datetime.now()
    now_ict = now.strftime("%Y-%m-%d %H:%M ICT")
    weekday_idx = now.weekday() # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri

    print("================================================================================")
    print(f"🏛️ MASTER 21:15 ICT AUTONOMOUS INSTITUTIONAL EXECUTION ENGINE — {now_ict}")
    print("================================================================================")

    # 0. RULE-074: US Exchange Holiday & Market-Clock Circuit Breaker
    is_live = ("--live" in sys.argv) or (os.environ.get("SKONVAULT_LIVE", "0") == "1")
    chk_broker = AlpacaClient("alpaca_live" if is_live else "pion_main")
    is_holiday, holiday_name = chk_broker.is_market_holiday()
    if is_holiday:
        print(f"  🛑 US EXCHANGE HOLIDAY DETECTED: {holiday_name.upper()}!")
        print("     RULE-074 Holiday Circuit Breaker: All US exchanges are 100% CLOSED.")
        print("     Zero order submissions permitted. Standing down autonomously. Cash reserves 100% protected.")
        _send_telegram(
            f"🇺🇸 SKONVAULT 21:15 ICT CIRCUIT BREAKER ACTIVATED\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 Date: {now_ict}\n"
            f"🏛️ US Holiday: {holiday_name}\n"
            f"🛑 Status: All US Exchanges (NYSE, NASDAQ, CBOE) 100% CLOSED.\n"
            f"🛡️ Action: Golden Entry Engine standing down. Zero orders submitted.\n"
            f"💵 Total Capital: 100% Protected & Earning SGOV Yield."
        )
        return

    if not chk_broker.is_market_open():
        clock = chk_broker.get_clock()
        next_open = clock.get("next_open", "Unknown")
        print(f"  🛑 US Options Market is CLOSED ({now_ict}). Next open: {next_open}. Standing down.")
        return

    # 1. Pre-Flight Mid-Week Harvest Sweep
    if weekday_idx == 2:
        print("  🌾 Wednesday Mid-Week Harvest Sweep — Auditing existing spreads...")
        run_all_harvests()

    # 2. Ingest 21:10 ICT UOA Institutional Flow & Dynamic Screener Target
    base_dir = Path("/home/ubuntu/shared")
    if not base_dir.exists(): base_dir = Path("/Users/SkonP/AI_Prompt/Obsidient/SkonVault/shared")
    
    # Load UOA flow to verify institutional direction
    uoa_file = base_dir / "uoa_live_cache.json"
    uoa_sweeps = []
    if uoa_file.exists():
        try:
            uoa_data = json.loads(uoa_file.read_text(encoding="utf-8"))
            uoa_sweeps = uoa_data.get("sweeps", [])
            print(f"  📡 Loaded 21:10 ICT Institutional UOA Flow: {len(uoa_sweeps)} active block sweeps.")
        except Exception: pass

    # 2a. ANNA 21:10 ICT T-5 PRE-FLIGHT HANDSHAKE SENTINEL
    # Verifies short strike OTM buffer >= 3.50% against live spot, auto-rerouting if compressed
    cands = []
    try:
        from run_anna_preflight_handshake import verify_or_run_preflight
        preflight_data = verify_or_run_preflight(base_dir=base_dir, max_age_seconds=900)
        p_cands = preflight_data.get("verified_candidates", [])
        if p_cands:
            cands = p_cands
            lead_sym = cands[0]["symbol"]
            lead_score = float(cands[0].get("total_score", 90.0))
            p_status = preflight_data.get("handshake_status", "UNKNOWN")
            print(f"  🤝 Ingested Anna T-5 Pre-Flight Handshake ({p_status}): {len(cands)} verified safe setups!")
            print(f"     Top Pre-Flight Lead: {lead_sym} (${cands[0]['short_strike']:.0f}P/${cands[0]['long_strike']:.0f}P | Buffer: {cands[0].get('buffer_pct', 0):+.2f}% | Score: {lead_score:.1f} pts)")
    except Exception as ex_pf:
        print(f"  ℹ️ Pre-flight handshake verification notice: {ex_pf}")

    # 2b. Ingest Ground-Truth Live Liquidity Radar (20:50 ICT) OR Dynamic Screener Target
    matrix_file = base_dir / "live_liquidity_matrix.json"
    dyn_file = base_dir / "tonight_selected_target.json"

    today_str = datetime.date.today().strftime("%Y-%m-%d")

    # PRIORITY 1: Ingest 20:50 ICT Live Liquidity Matrix (if preflight didn't populate)
    if not cands and matrix_file.exists():
        try:
            m_data = json.loads(matrix_file.read_text(encoding="utf-8"))
            scanned_at_str = m_data.get("scanned_at_ict", "")
            # RULE-087: File freshness gate — matrix MUST be from today to avoid stale strike entries
            is_fresh = today_str in scanned_at_str and (time.time() - matrix_file.stat().st_mtime < 14400)
            if not is_fresh:
                print(f"  ⚠️ STALE LIQUIDITY MATRIX DETECTED ({scanned_at_str or 'unknown timestamp'}).")
                print(f"     Rejecting stale matrix to prevent obsolete strike entry (RULE-087). Falling back to fresh screener/radar!")
            else:
                viable = m_data.get("all_viable", [])
                if not viable and "sectors" in m_data:
                    for s_list in m_data["sectors"].values():
                        viable.extend(s_list)
                    viable = sorted(viable, key=lambda x: x.get("total_score", 0), reverse=True)

                if viable:
                    for v in viable:
                        cands.append({
                            "symbol": v.get("symbol"),
                            "short_strike": v.get("short_strike"),
                            "long_strike": v.get("long_strike"),
                            "width": v.get("width", 5.0),
                            "expiration": v.get("exp_date"),
                            "total_score": v.get("total_score", 85.0),
                            "natural_credit": v.get("natural_credit"),
                            "mid_credit": v.get("mid_credit"),
                            "roc_pct": v.get("roc_pct"),
                            "theme": v.get("theme")
                        })
                    lead_sym = cands[0]["symbol"]
                    lead_score = float(cands[0].get("total_score", 90.0))
                    print(f"  📡 Prioritized Live Liquidity Radar (20:50 ICT): {len(cands)} confirmed fertile candidates!")
                    print(f"     Top Live Liquid Lead: {lead_sym} (${cands[0]['short_strike']:.0f}P/${cands[0]['long_strike']:.0f}P | Score: {lead_score:.1f} pts | Nat: ${cands[0].get('natural_credit', 0):.2f})")
        except Exception as ex_mat:
            print(f"  ℹ️ Live liquidity matrix load notice: {ex_mat}")

    # PRIORITY 2: Fallback to Pre-Market Dynamic Screener Target
    if not cands and dyn_file.exists():
        try:
            dyndata = json.loads(dyn_file.read_text(encoding="utf-8"))
            dyn_ts = dyndata.get("timestamp") or dyndata.get("scanned_at") or ""
            is_dyn_fresh = today_str in dyn_ts or (time.time() - dyn_file.stat().st_mtime < 86400)
            if not is_dyn_fresh:
                print(f"  ⚠️ Stale dynamic screener file detected ({dyn_ts}). Falling back to live spot radar.")
            else:
                prim = dyndata.get("primary") or dyndata.get("primary_selection")
                falls = dyndata.get("fallbacks") or dyndata.get("waterfall_fallbacks", [])
                if prim:
                    cands = [prim] + falls
                    lead_sym = prim.get("symbol", "NVDA")
                    lead_score = float(prim.get("total_score", 90.0))
                    print(f"  🧠 Loaded Screener Target: {lead_sym} ({lead_score:.1f} pts)")
        except Exception as ex_dyn:
            print(f"  ℹ️ Target load notice: {ex_dyn}")

    # PRIORITY 3: Dynamic Waterfall fallback if screener not found (RULE-048 & RULE-049)
    if not cands:
        try:
            from live_spot import get_spots
            fallback_syms = ["META", "MSFT", "V", "IWM", "GE", "LMT", "NVDA", "XLF"]
            spots_map = get_spots(fallback_syms)
            cands = []
            for s in fallback_syms:
                s_info = spots_map.get(s, {})
                sp = float(s_info.get("price", 0.0))
                if sp > 0:
                    w = 2.0 if s in ["XLF", "XLU", "GLD", "SLV"] else 5.0
                    step = 1.0 if sp < 100 else 5.0
                    target_s = round((sp * 0.95) / step) * step
                    acct = "pion2_sub" if s in ["XLF", "GLD", "IWM"] or weekday_idx == 4 else "pion_main"
                    cands.append({
                        "symbol": s,
                        "short_strike": target_s,
                        "width": w,
                        "assigned_account": acct
                    })
        except Exception as ex_fb:
            print(f"  ℹ️ Dynamic fallback generation notice: {ex_fb}")

        if not cands:
            # Fallback using broker client directly if live_spot failed
            try:
                chk_cli = AlpacaClient("pion_main")
                for s in ["QQQ", "SPY", "IWM", "META", "MSFT", "XLF"]:
                    bbar = chk_cli.get_latest_bar(s) if hasattr(chk_cli, 'get_latest_bar') else None
                    sp = float(bbar.get("c", 0.0)) if bbar else 0.0
                    if sp > 0:
                        w = 2.0 if s in ["XLF", "XLU", "GLD", "SLV"] else 5.0
                        step = 1.0 if sp < 100 else 5.0
                        target_s = round((sp * 0.95) / step) * step
                        acct = "pion2_sub" if s in ["XLF", "GLD", "IWM"] or weekday_idx == 4 else "pion_main"
                        cands.append({
                            "symbol": s,
                            "short_strike": target_s,
                            "width": w,
                            "assigned_account": acct
                        })
            except Exception as ex_bbar:
                print(f"  ℹ️ Secondary broker spot fallback notice: {ex_bbar}")

        if not cands:
            print(f"  🛑 RULE-087 CRITICAL: Unable to fetch live spot for universe tickers. No hardcoded static strikes allowed!")

    # 3. RULE-091 Dual-Account Concurrent Dispatch & Idle Cash Elimination Protocol:
    # Build active portfolio tickers list across all broker accounts and pending orders
    portfolio_tickers = []
    is_live = ("--live" in sys.argv) or (os.environ.get("SKONVAULT_LIVE", "0") == "1")
    accounts_to_check = ["alpaca_live"] if is_live else ["pion_main", "pion2_sub"]
    for acct_chk in accounts_to_check:
        try:
            chk_cli = AlpacaClient(acct_chk)
            for p in chk_cli.get_positions():
                s = p.get("symbol", "")
                for known_sym in ["SPY", "QQQ", "LMT", "AVGO", "NVDA", "GLD", "XLU", "XLF", "JPM", "AMD", "CEG", "TSM", "VRT", "GE", "XLE", "XLV", "UNH", "JNJ", "XLP", "V", "MSFT", "META", "IWM"]:
                    if known_sym in s:
                        portfolio_tickers.append(known_sym)
        except Exception: pass

    if is_live:
        try:
            from tradier_broker import TradierClient
            t_cli = TradierClient("live")
            for p in t_cli.get_positions():
                s = p.get("symbol", "")
                for known_sym in ["SPY", "QQQ", "LMT", "AVGO", "NVDA", "GLD", "XLU", "XLF", "JPM", "AMD", "CEG", "TSM", "VRT", "GE", "XLE", "XLV", "UNH", "JNJ", "XLP", "V", "MSFT", "META", "IWM"]:
                    if known_sym in s:
                        portfolio_tickers.append(known_sym)
        except Exception: pass

    try:
        from order_fill_tracker import load_pending_orders
        for po in load_pending_orders():
            st = str(po.get("status", "")).lower()
            po_acct = po.get("account", "")
            # Only count active working orders belonging to accounts being checked
            if st in ["working", "new", "pending", "resting"] and (not is_live or po_acct in ["alpaca_live", "tradier_live"]):
                psym = po.get("symbol")
                if psym:
                    portfolio_tickers.append(psym)
    except Exception: pass

    print(f"  🛡️ Pre-Flight Active Portfolio Holdings: {sorted(list(set(portfolio_tickers)))}")

    dispatched_any = False
    primary_account = "alpaca_live" if is_live else "pion_main"
    secondary_account = "tradier_live" if is_live else "pion2_sub"
    mode_str = "LIVE REAL-MONEY PRODUCTION 🔴" if is_live else "PAPER SIMULATION 🟡"
    print(f"  🎯 Execution Target Mode: {mode_str} ({primary_account} & {secondary_account})")

    # Account 1: Primary Engine (Alpaca Live / Pion Main: Hybrid Barbell, up to 6 slots)
    print(f"\n  🏛️ Evaluating {primary_account.upper()} for Hybrid Barbell Entry...")
    main_res = execute_account_entry(primary_account, cands, now_ict, base_dir, portfolio_tickers)
    if main_res:
        dispatched_any = True
        main_sym = main_res.get("symbol")
        if main_sym:
            portfolio_tickers.append(main_sym)

    # Account 2: Secondary Satellite (Tradier Live / Pion2 Sub: High-Velocity Sprint, up to 2 slots)
    if is_live:
        print(f"\n  ⚡ Evaluating TRADIER_LIVE for High-Velocity Satellite Sprint Entry (DIR-04)...")
        sub_res = execute_tradier_live_entry(cands, now_ict, base_dir, portfolio_tickers)
        if sub_res:
            dispatched_any = True
    else:
        print(f"\n  ⚡ Evaluating PION2_SUB for High-Velocity Sprint Entry (RULE-091)...")
        sub_res = execute_account_entry(secondary_account, cands, now_ict, base_dir, portfolio_tickers)
        if sub_res:
            dispatched_any = True

    if not dispatched_any:
        print("  ℹ️ Zero new entries dispatched tonight (accounts at capacity or waiting for next cycle).")

    print("\n================================================================================")
    print(f"🏁 21:15 ICT GOLDEN ENTRY ENGINE COMPLETE — {now_ict}")
    print("================================================================================")

if __name__ == "__main__":
    run_2115_golden_execution()

