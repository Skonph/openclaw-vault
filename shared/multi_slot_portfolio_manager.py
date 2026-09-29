#!/usr/bin/env python3
"""
multi_slot_portfolio_manager.py — 6-Sector Multi-Slot Concurrency & Dual-Rod OCO Engine

Institutional Architecture:
1. Manages 6 Non-Overlapping Sector Slots:
   - Slot 1: AI_HARDWARE (NVDA, AVGO, AMD, TSM)
   - Slot 2: POWER_INFRA (XLU, CEG, VRT)
   - Slot 3: HARD_ASSETS (GLD, XLE)
   - Slot 4: HEALTHCARE (XLV, UNH, JNJ)
   - Slot 5: DEFENSE_STAPLES (XLP, LMT, GE)
   - Slot 6: FINANCIALS (XLF, JPM, V)

2. Slot Capacities & Risk Envelopes:
   - Alpaca Live ($30k): Up to 6 concurrent slots ($1,200-$1,500 risk/slot = $7.2k-$9.0k max risk)
   - Pion Main ($15k): Up to 3-4 concurrent slots ($4,000 max risk)
   - Pion2 Sub ($3.5k): Up to 1-2 slots ($1,000-$1,200 max risk)
   - Tradier Live ($2k): 1 slot micro-spread ($100-$200 risk)

3. Dual-Rod OCO (One-Cancels-Others) Auction:
   - For each open slot, fishes Top 2 candidates from live_liquidity_matrix.json.
   - Places working limit orders at Optimal EV Limit (Natural + 0.40 * Gap).
   - As soon as one fills in a slot, the listener instantly cancels the companion order.
"""

import sys
import os
import json
import time
import datetime
import re
import urllib.request
import ssl
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from alpaca_broker import AlpacaClient
try:
    from tradier_broker import TradierClient
except ImportError:
    TradierClient = None
from order_fill_tracker import (
    load_pending_orders,
    save_pending_orders,
    register_order,
    record_confirmed_fill,
    send_telegram,
    calculate_mac_floor
)

ICT = datetime.timezone(datetime.timedelta(hours=7))

SECTOR_SLOTS: Dict[str, Dict[str, Any]] = {
    "SLOT_1_AI_HARDWARE": {
        "theme_name": "1. AI Chips & Hardware",
        "symbols": ["NVDA", "AVGO", "AMD", "TSM", "SMH"],
        "max_risk_usd": 2500.0
    },
    "SLOT_2_POWER_INFRA": {
        "theme_name": "2. Data Center Power & Cooling",
        "symbols": ["XLU", "CEG", "VRT", "GE"],
        "max_risk_usd": 2500.0
    },
    "SLOT_3_HARD_ASSETS": {
        "theme_name": "3. Inflation Defense & Hard Assets",
        "symbols": ["GLD", "XLE", "SLV", "IBIT"],
        "max_risk_usd": 2500.0
    },
    "SLOT_4_HEALTHCARE": {
        "theme_name": "4. Longevity & Healthcare",
        "symbols": ["XLV", "UNH", "JNJ", "ABBV"],
        "max_risk_usd": 2500.0
    },
    "SLOT_5_DEFENSE_STAPLES": {
        "theme_name": "5. Consumer Staples & National Defense",
        "symbols": ["XLP", "LMT", "COST", "CAT", "RTX"],
        "max_risk_usd": 2500.0
    },
    "SLOT_6_FINANCIALS": {
        "theme_name": "6. Financial Services & Payment Rails",
        "symbols": ["XLF", "JPM", "V", "BAC"],
        "max_risk_usd": 2500.0
    },
    "SLOT_7_BROAD_INDEX": {
        "theme_name": "7. Broad Index & Market Hedging",
        "symbols": ["SPY", "QQQ", "IWM"],
        "max_risk_usd": 2500.0
    },
    "SLOT_8_MEGA_PLATFORMS": {
        "theme_name": "8. Mega-Cap Cloud & Software Platform",
        "symbols": ["MSFT", "AAPL", "GOOGL", "AMZN", "META", "PANW", "TSLA"],
        "max_risk_usd": 2500.0
    }
}

ACCOUNT_MAX_CONCURRENT_SLOTS = {
    "alpaca_live": 8,
    "pion_main": 4,
    "pion2_sub": 2,
    "tradier_live": 2
}


def audit_active_slots(client: AlpacaClient) -> Tuple[Dict[str, Any], List[str]]:
    """
    Audits live broker positions and maps them to the 6 Sector Slots.
    Returns: (occupied_slots_dict, vacant_slot_keys_list)
    """
    occupied_slots: Dict[str, Any] = {}
    active_symbols = set()

    try:
        positions = client.get_positions()
        for p in positions:
            sym_raw = p.get("symbol", "")
            m = re.match(r"^([A-Z]+)", sym_raw)
            if m:
                active_symbols.add(m.group(1))
            elif sym_raw:
                active_symbols.add(sym_raw)
    except Exception as ex:
        print(f"  ℹ️ Broker position audit notice: {ex}")

    # Map active symbols to slots
    for slot_id, slot_def in SECTOR_SLOTS.items():
        for sym in slot_def["symbols"]:
            if sym in active_symbols:
                occupied_slots[slot_id] = {
                    "slot_id": slot_id,
                    "theme_name": slot_def["theme_name"],
                    "occupied_symbol": sym
                }
                break

    vacant_slots = [sid for sid in SECTOR_SLOTS if sid not in occupied_slots]
    return occupied_slots, vacant_slots


def deploy_multi_slot_auction(
    account_type: str = "pion2_sub",
    max_new_slots: Optional[int] = None,
    enable_dual_rod: bool = True
) -> Dict[str, Any]:
    """
    Executes the 21:15 ICT Multi-Slot Dual-Rod OCO Auction.
    """
    now_ict = datetime.datetime.now(ICT).strftime("%Y-%m-%d %H:%M:%S ICT")
    base_dir = Path("/home/ubuntu/shared")
    if not base_dir.exists():
        base_dir = Path("/Users/SkonP/AI_Prompt/Obsidient/SkonVault/shared")

    client = AlpacaClient(account_type)
    print("=" * 85)
    print(f"🏛️ MULTI-SLOT DUAL-ROD OCO AUCTION ENGINE — {now_ict} ({account_type.upper()})")
    print("=" * 85)

    from dynamic_regime_manager import evaluate_market_regime
    regime_info = evaluate_market_regime()
    print(f"  🌡️ Dynamic Regime: {regime_info['regime_name']} (VIX {regime_info['vix']:.2f}) | Barbell: {int(regime_info['sprint_ratio']*100)}% Sprint / {int(regime_info['anchor_ratio']*100)}% Anchor | Margin Ceiling: {int(regime_info['margin_ceiling_pct']*100)}%")

    if not client.is_market_open():
        print("🛑 US Options Market is CLOSED. Standing down.")
        return {}

    # 1. Audit current slot utilization
    occupied, vacant = audit_active_slots(client)
    print(f"  • Occupied Slots ({len(occupied)}/{len(SECTOR_SLOTS)}): {[s['occupied_symbol'] for s in occupied.values()]}")
    print(f"  • Vacant Slots   ({len(vacant)}/{len(SECTOR_SLOTS)}): {vacant}")

    max_allowed = ACCOUNT_MAX_CONCURRENT_SLOTS.get(account_type.lower(), 2)
    available_capacity = max(0, max_allowed - len(occupied))

    if available_capacity == 0:
        print(f"  🛡️ Account {account_type.upper()} is at full slot capacity ({len(occupied)}/{max_allowed}). All slots active!")
        return {"status": "FULL_CAPACITY", "occupied": occupied}

    slots_to_reload = vacant[:available_capacity]
    if max_new_slots is not None:
        slots_to_reload = slots_to_reload[:max_new_slots]

    print(f"  🎯 Reloading {len(slots_to_reload)} Vacant Slots: {slots_to_reload}")

    # 2. Ingest Live Liquidity Matrix (20:50 ICT output)
    matrix_file = base_dir / "live_liquidity_matrix.json"
    if not matrix_file.exists():
        # Fallback to dynamic screener
        print("  ⚠️ live_liquidity_matrix.json missing. Running quick live sweep...")
        from live_liquidity_matrix_scanner import scan_live_liquidity_matrix
        scan_live_liquidity_matrix(account_type, notify_telegram=False)

    matrix_data = json.loads(matrix_file.read_text(encoding="utf-8"))
    sector_catalog = matrix_data.get("sectors", {})

    submitted_orders = []

    # 3. For each vacant slot, submit dual-rod OCO auction
    for slot_id in slots_to_reload:
        slot_def = SECTOR_SLOTS[slot_id]
        theme_name = slot_def["theme_name"]
        candidates = sector_catalog.get(theme_name, [])

        if not candidates:
            print(f"  ⚠️ No viable candidates found in live matrix for {theme_name}.")
            continue

        # Select Top 2 candidates for this slot
        primary = candidates[0]
        companion = candidates[1] if (len(candidates) > 1 and enable_dual_rod) else None

        targets = [primary]
        if companion and companion["symbol"] != primary["symbol"]:
            targets.append(companion)

        for rank_idx, cand in enumerate(targets, start=1):
            sym = cand["symbol"]
            w = cand["width"]
            short_s = cand["short_strike"]
            long_s = cand["long_strike"]
            exp_date = cand["exp_date"]
            short_sym = cand["short_sym"]
            long_sym = cand["long_sym"]
            nat_credit = cand["natural_credit"]
            mid_credit = cand["mid_credit"]
            spread_gap = cand["spread_gap"]
            mac_floor = cand["mac_floor"]

            # Compute Optimal EV Limit: Natural + 0.40 * Gap
            if spread_gap > 0.05 and nat_credit > 0:
                target_limit = max(mac_floor, round(nat_credit + 0.40 * spread_gap, 2))
            else:
                target_limit = max(mac_floor, mid_credit)

            # Sizing: dynamic contract calculation based on regime slot target risk
            alpaca_slot_risk = regime_info.get("max_slot_risk_alpaca", 2500.0)
            tradier_slot_risk = regime_info.get("max_slot_risk_tradier", 600.0)
            if account_type == "alpaca_live":
                contracts = max(2, min(8, int(alpaca_slot_risk // (w * 100.0))))
            elif "tradier" in account_type:
                contracts = max(1, min(3, int(tradier_slot_risk // (w * 100.0))))
            elif account_type == "pion_main":
                contracts = max(2, min(4, int(1500.0 // (w * 100.0))))
            else:  # pion2_sub
                contracts = max(1, min(2, int(1000.0 // (w * 100.0))))

            print(f"  ⚡ [{slot_id}] Rod #{rank_idx}: Deploying {sym} ${short_s:.0f}P/${long_s:.0f}P @ limit ${target_limit:.2f} (Nat: ${nat_credit:.2f} | Mid: ${mid_credit:.2f})")

            # Multi-Leg Limit Order
            mleg_payload = {
                "order_class": "mleg",
                "type": "limit",
                "time_in_force": "day",
                "legs": [
                    {"symbol": short_sym, "ratio_qty": 1, "side": "sell", "position_intent": "sell_to_open"},
                    {"symbol": long_sym, "ratio_qty": 1, "side": "buy", "position_intent": "buy_to_open"}
                ],
                "qty": str(contracts),
                "limit_price": f"-{target_limit:.2f}"
            }

            try:
                req_mleg = urllib.request.Request(
                    f"{client.base_url}/v2/orders",
                    data=json.dumps(mleg_payload).encode("utf-8"),
                    headers=client._headers()
                )
                with urllib.request.urlopen(req_mleg, context=client.ssl_ctx, timeout=10) as r_mleg:
                    resp = json.loads(r_mleg.read().decode())
                    oid = resp.get("id")
                    if oid:
                        order_entry = register_order(
                            account=account_type,
                            symbol=sym,
                            short_sym=short_sym,
                            long_sym=long_sym,
                            short_strike=short_s,
                            long_strike=long_s,
                            width=w,
                            contracts=contracts,
                            exp_date=exp_date,
                            order_id=oid,
                            limit_credit=target_limit
                        )
                        # Tag with slot_id for OCO cancellation pairing
                        order_entry["slot_id"] = slot_id
                        orders = load_pending_orders()
                        for od in orders:
                            if od.get("order_id") == oid:
                                od["slot_id"] = slot_id
                        save_pending_orders(orders)

                        submitted_orders.append({
                            "slot_id": slot_id,
                            "order_id": oid,
                            "symbol": sym,
                            "contracts": contracts,
                            "limit_credit": target_limit,
                            "rod_rank": rank_idx
                        })
            except Exception as ex_sub:
                print(f"  🔴 Order submission error for {sym}: {ex_sub}")

    # Dispatch Telegram Notice
    if submitted_orders:
        lines = [f"• [{o['slot_id'].replace('SLOT_', 'S')}] {o['symbol']}: ${o['limit_credit']:.2f} ({o['contracts']}C) | Rod #{o['rod_rank']}" for o in submitted_orders]
        lines_txt = "\n".join(lines)
        send_telegram(
            f"🏛️ 6-SECTOR DUAL-ROD AUCTION DEPLOYED\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 Date: {now_ict}\n"
            f"💼 Account: {account_type.upper()}\n"
            f"🎯 Deployed Rods ({len(submitted_orders)}):\n{lines_txt}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ Software OCO Listener: Active (1st Fill Cancels Companion) 🟢"
        )

    return {"status": "SUCCESS", "submitted_orders": submitted_orders}


def run_slot_oco_listener(account_type: Optional[str] = None):
    """
    Checks working orders across accounts. If any order in a slot fills, immediately cancels
    companion orders in the same slot to eliminate over-allocation risk.
    Supports Alpaca Live, Tradier Live, Pion Main, and Pion2 Sub.
    """
    now_ict = datetime.datetime.now(ICT).strftime("%Y-%m-%d %H:%M ICT")
    orders = load_pending_orders()
    if account_type:
        working = [o for o in orders if o.get("status") == "working" and o.get("account") == account_type]
    else:
        working = [o for o in orders if o.get("status") == "working"]

    if not working:
        return

    # Group by (account, slot_id)
    slots_map: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for o in working:
        acct = o.get("account", "unknown")
        sid = o.get("slot_id", "DEFAULT_SLOT")
        slots_map.setdefault((acct, sid), []).append(o)

    for (acct, sid), slot_orders in slots_map.items():
        filled_order = None
        for o in slot_orders:
            oid = str(o["order_id"])
            broker_type = str(o.get("broker_type", "")).lower()
            is_tradier = "tradier" in broker_type or "tradier" in acct.lower()

            status = ""
            filled_qty = 0.0
            fill_price = abs(float(o.get("current_limit", 0.95)))

            if is_tradier:
                if TradierClient:
                    try:
                        t_cli = TradierClient("live" if "live" in acct.lower() else "sandbox")
                        t_res = t_cli.get_order_status(oid)
                        t_ord = t_res.get("order", {})
                        status = t_ord.get("status", "").lower()
                        filled_qty = float(t_ord.get("exec_quantity", 0))
                        fill_price = abs(float(t_ord.get("avg_fill_price") or t_ord.get("price") or fill_price))
                    except Exception as ex_t:
                        print(f"  ℹ️ Tradier OCO check notice for {oid}: {ex_t}")
            else:
                try:
                    a_cli = AlpacaClient(acct)
                    info = a_cli.get_order(oid)
                    status = info.get("status", "").lower()
                    filled_qty = float(info.get("filled_qty", 0))
                    fill_price = abs(float(info.get("filled_avg_price") or fill_price))
                except Exception as ex_a:
                    print(f"  ℹ️ Alpaca OCO check notice for {oid}: {ex_a}")

            if status == "filled" or filled_qty >= o.get("contracts", 1):
                o["status"] = "filled"
                o["filled_at"] = now_ict
                o["filled_avg_price"] = fill_price
                record_confirmed_fill(o, fill_price)
                filled_order = o
                print(f"🎉 OCO FILL DETECTED in [{sid}] ({acct}): {o['symbol']} filled at ${fill_price:.2f}!")
                break

        # If one filled in this slot, cancel all other companions in this slot
        if filled_order:
            for companion in slot_orders:
                c_oid = str(companion["order_id"])
                if c_oid != str(filled_order["order_id"]) and companion.get("status") == "working":
                    c_acct = companion.get("account", acct)
                    c_broker = str(companion.get("broker_type", "")).lower()
                    print(f"  ⚡ OCO Cancel: Cancelling companion order {c_oid} ({companion['symbol']}) in [{sid}] ({c_acct})...")
                    if "tradier" in c_broker or "tradier" in c_acct.lower():
                        if TradierClient:
                            try:
                                TradierClient("live" if "live" in c_acct.lower() else "sandbox").cancel_order(c_oid)
                            except Exception: pass
                    else:
                        try:
                            AlpacaClient(c_acct).cancel_order(c_oid)
                        except Exception: pass
                    companion["status"] = "canceled_oco_companion"
            save_pending_orders(orders)


if __name__ == "__main__":
    if "--oco" in sys.argv:
        run_slot_oco_listener(None)
    else:
        deploy_multi_slot_auction("pion2_sub")
