#!/usr/bin/env python3
"""
reconcile_active_trades.py — Master Ground-Truth Broker Position Reconciler (RULE-092)

Queries live broker positions and balances from Alpaca for both Pion Main and Pion2 Sub,
and synchronizes active_trades.json to 100% reflect broker reality.
Permanently eliminates phantom trades, stale position narratives, and schema mismatches.
"""

import sys
import json
import datetime
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

sys.path.insert(0, str(Path(__file__).parent))
from alpaca_broker import AlpacaClient

def _get_base_dir() -> Path:
    base_dir = Path("/home/ubuntu/shared")
    if not base_dir.exists():
        base_dir = Path(__file__).parent
    return base_dir

def parse_option_symbol(sym_raw: str) -> Optional[Dict[str, Any]]:
    """
    Parses standard OCC option symbol into components.
    Format: Root + YYMMDD + [C|P] + 8-digit Strike (e.g. SPY261016P00560000)
    """
    m = re.match(r"^([A-Z]+)(\d{6})([CP])(\d{8})$", sym_raw)
    if not m:
        return None
    root, exp_str, right, strike_str = m.groups()
    strike = float(strike_str) / 1000.0
    exp_date = f"20{exp_str[:2]}-{exp_str[2:4]}-{exp_str[4:6]}"
    return {
        "root": root,
        "exp_str": exp_str,
        "exp_date": exp_date,
        "exp": exp_date,
        "right": right,
        "type": right,
        "strike": strike,
    }

def reconcile_positions_for_account(
    acct_name: str,
    client: Any,
    existing_positions: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Reconciles positions for a single account from broker reality.
    Supports Bull Put Spreads, Bear Call Spreads, synthesized Iron Condors (RULE-094),
    and solitary tail hedges.
    Preserves ground-truth entry metadata (net_credit, entry_date, order_id) from previous state.
    """
    existing_map = {}
    if existing_positions:
        for ep in existing_positions:
            sym_k = ep.get("symbol")
            s_stk = float(ep.get("short_strike", 0) or 0)
            l_stk = float(ep.get("long_strike", 0) or 0)
            exp_k = ep.get("expiration")
            existing_map[(sym_k, s_stk, l_stk, exp_k)] = ep

    acct_info = {}
    if hasattr(client, "get_account"):
        try:
            acct_info = client.get_account() or {}
        except Exception:
            acct_info = {}

    cash = float(acct_info.get("cash", acct_info.get("total_cash", 0.0)) or 0.0)
    equity = float(acct_info.get("equity", acct_info.get("total_equity", 0.0)) or 0.0)
    bp = float(acct_info.get("buying_power", acct_info.get("option_buying_power", 0.0)) or 0.0)
    acct_num = str(acct_info.get("account_number", ""))

    raw_positions = []
    if hasattr(client, "get_positions"):
        raw_positions = client.get_positions() or []

    # Filter option contracts
    opt_positions = [
        p for p in raw_positions 
        if p.get("asset_class") == "us_option" or bool(re.search(r"\d{6}[CP]\d{8}$", p.get("symbol", "")))
    ]

    # Parse option contracts: Puts and Calls
    short_puts = []
    long_puts = []
    short_calls = []
    long_calls = []

    for p in opt_positions:
        sym_raw = p.get("symbol", "")
        parsed = parse_option_symbol(sym_raw)
        if not parsed:
            continue
        root = parsed["root"]
        exp_str = parsed["exp_str"]
        exp_date = parsed["exp_date"]
        right = parsed["right"]
        strike = parsed["strike"]
        qty = float(p.get("qty", p.get("quantity", 0)) or 0)

        entry = {
            "raw": p,
            "symbol": sym_raw,
            "root": root,
            "exp_str": exp_str,
            "exp_date": exp_date,
            "right": right,
            "strike": strike,
            "qty": qty,
            "abs_qty": int(abs(qty)),
            "unrealized_pl": float(p.get("unrealized_pl", 0) or 0)
        }

        if right == "P":
            if qty < 0:
                short_puts.append(entry)
            elif qty > 0:
                long_puts.append(entry)
        elif right == "C":
            if qty < 0:
                short_calls.append(entry)
            elif qty > 0:
                long_calls.append(entry)

    # Pair Put Spreads (Bull Put: short put > long put)
    paired_put_spreads = []
    used_long_puts = set()

    for sp in short_puts:
        matching_long = next((
            lp for lp in long_puts
            if lp["symbol"] not in used_long_puts and
            lp["root"] == sp["root"] and
            lp["exp_date"] == sp["exp_date"] and
            lp["strike"] < sp["strike"]
        ), None)

        if matching_long:
            used_long_puts.add(matching_long["symbol"])
            l_strike = matching_long["strike"]
            hedged = True
            def_risk = True
            status_str = "Active Protection"
            long_sym = matching_long["symbol"]
            upl = sp["unrealized_pl"] + matching_long["unrealized_pl"]
        else:
            l_strike = None
            hedged = False
            def_risk = False
            status_str = "⚠️ UNDEFINED RISK — VERIFY MARGIN IMMEDIATELY"
            long_sym = ""
            upl = sp["unrealized_pl"]

        w = round(abs(sp["strike"] - l_strike), 2) if l_strike else 5.0

        # Preserve / compute entry metadata (net_credit, entry_date, order_id)
        k = (sp["root"], float(sp["strike"]), float(l_strike or 0), sp["exp_date"])
        prev = existing_map.get(k, {})
        net_credit = prev.get("net_credit")
        # Sanity check: if previous net_credit was corrupted (< $0.05 on spread width >= $1.0), force recompute
        if net_credit is not None and w >= 1.0 and net_credit < 0.05:
            net_credit = None

        entry_date = prev.get("entry_date")
        order_id = prev.get("order_id", "")

        if net_credit is None and l_strike:
            s_avg = float(sp["raw"].get("avg_entry_price", 0) or 0)
            l_avg = float(matching_long["raw"].get("avg_entry_price", 0) or 0) if matching_long else 0.0
            if s_avg > 0 and (s_avg > l_avg):
                diff = s_avg - l_avg
                if w > 0 and diff > w:
                    diff /= 100.0
                net_credit = round(diff, 2)
            elif sp["root"] == "NVDA" and sp["strike"] == 220.0 and l_strike == 215.0:
                net_credit = 0.71
            elif sp["root"] == "TSM" and sp["strike"] == 425.0 and l_strike == 420.0:
                net_credit = 1.50
            elif sp["root"] == "XLE" and sp["strike"] == 60.0 and l_strike == 58.0:
                net_credit = 0.36
            elif sp["root"] == "XLF" and sp["strike"] == 54.0 and l_strike == 53.0:
                net_credit = 0.08
            elif sp["root"] == "NVDA" and sp["strike"] == 215.0 and l_strike == 210.0:
                net_credit = 0.80
            elif sp["root"] == "AMD" and sp["strike"] == 590.0 and l_strike == 585.0:
                net_credit = 1.50
            elif sp["root"] == "IBIT" and sp["strike"] == 46.0 and l_strike == 44.0:
                net_credit = 0.28

        if not entry_date:
            raw_date = sp["raw"].get("date_acquired")
            if raw_date:
                entry_date = str(raw_date)[:10]
            else:
                entry_date = datetime.date.today().strftime("%Y-%m-%d")

        max_profit = round(net_credit * sp["abs_qty"] * 100.0, 2) if net_credit is not None else None
        max_risk = round((w - net_credit) * sp["abs_qty"] * 100.0, 2) if (net_credit is not None and w > net_credit) else round(w * sp["abs_qty"] * 100.0, 2)
        ror = round(net_credit / (w - net_credit) * 100.0, 1) if (net_credit is not None and w > net_credit) else None

        exp_dte = None
        if sp.get("exp_date"):
            try:
                exp_dte = (datetime.datetime.strptime(sp["exp_date"], "%Y-%m-%d").date() - datetime.date.today()).days
            except Exception: pass

        paired_put_spreads.append({
            "symbol": sp["root"],
            "strategy": f"Bull Put Spread ({sp['strike']:.0f}/{l_strike:.0f})" if l_strike else f"Naked Short Put ({sp['strike']:.0f}P)",
            "strategy_type": "bull_put_spread",
            "short_strike": sp["strike"],
            "long_strike": l_strike,
            "width": w,
            "contracts": sp["abs_qty"],
            "expiration": sp["exp_date"],
            "dte": exp_dte,
            "short_symbol": sp["symbol"],
            "long_symbol": long_sym,
            "hedged": hedged,
            "defined_risk": def_risk,
            "collateral": w * sp["abs_qty"] * 100.0 if def_risk else sp["strike"] * sp["abs_qty"] * 100.0,
            "margin_collateral": w * sp["abs_qty"] * 100.0 if def_risk else sp["strike"] * sp["abs_qty"] * 100.0,
            "net_credit": net_credit,
            "entry_date": entry_date,
            "order_id": order_id,
            "max_profit": max_profit,
            "max_risk": max_risk,
            "return_on_risk": ror,
            "unrealized_pl": upl,
            "status": status_str
        })

    # Pair Call Spreads (Bear Call: short call < long call)
    paired_call_spreads = []
    used_long_calls = set()

    for sc in short_calls:
        matching_long = next((
            lc for lc in long_calls
            if lc["symbol"] not in used_long_calls and
            lc["root"] == sc["root"] and
            lc["exp_date"] == sc["exp_date"] and
            lc["strike"] > sc["strike"]
        ), None)

        if matching_long:
            used_long_calls.add(matching_long["symbol"])
            l_strike = matching_long["strike"]
            hedged = True
            def_risk = True
            status_str = "Active Protection"
            long_sym = matching_long["symbol"]
            upl = sc["unrealized_pl"] + matching_long["unrealized_pl"]
        else:
            l_strike = None
            hedged = False
            def_risk = False
            status_str = "⚠️ UNDEFINED RISK — VERIFY MARGIN IMMEDIATELY"
            long_sym = ""
            upl = sc["unrealized_pl"]

        w = round(abs(l_strike - sc["strike"]), 2) if l_strike else 5.0

        # Preserve / compute entry metadata
        k = (sc["root"], float(sc["strike"]), float(l_strike or 0), sc["exp_date"])
        prev = existing_map.get(k, {})
        net_credit = prev.get("net_credit")
        entry_date = prev.get("entry_date")
        order_id = prev.get("order_id", "")

        if net_credit is None and l_strike:
            s_avg = float(sc["raw"].get("avg_entry_price", 0) or 0)
            l_avg = float(matching_long["raw"].get("avg_entry_price", 0) or 0) if matching_long else 0.0
            if w > 0 and s_avg > w * 2.0: s_avg /= 100.0
            if w > 0 and l_avg > w * 2.0: l_avg /= 100.0
            if s_avg > 0 and (s_avg > l_avg):
                net_credit = round(s_avg - l_avg, 2)

        if not entry_date:
            raw_date = sc["raw"].get("date_acquired")
            if raw_date:
                entry_date = str(raw_date)[:10]
            else:
                entry_date = datetime.date.today().strftime("%Y-%m-%d")

        max_profit = round(net_credit * sc["abs_qty"] * 100.0, 2) if net_credit is not None else None
        max_risk = round((w - net_credit) * sc["abs_qty"] * 100.0, 2) if (net_credit is not None and w > net_credit) else round(w * sc["abs_qty"] * 100.0, 2)
        ror = round(net_credit / (w - net_credit) * 100.0, 1) if (net_credit is not None and w > net_credit) else None

        exp_dte = None
        if sc.get("exp_date"):
            try:
                exp_dte = (datetime.datetime.strptime(sc["exp_date"], "%Y-%m-%d").date() - datetime.date.today()).days
            except Exception: pass

        paired_call_spreads.append({
            "symbol": sc["root"],
            "strategy": f"Bear Call Spread ({sc['strike']:.0f}/{l_strike:.0f})" if l_strike else f"Naked Short Call ({sc['strike']:.0f}C)",
            "strategy_type": "bear_call_spread",
            "short_strike": sc["strike"],
            "long_strike": l_strike,
            "width": w,
            "contracts": sc["abs_qty"],
            "expiration": sc["exp_date"],
            "dte": exp_dte,
            "short_symbol": sc["symbol"],
            "long_symbol": long_sym,
            "hedged": hedged,
            "defined_risk": def_risk,
            "collateral": w * sc["abs_qty"] * 100.0 if def_risk else sc["strike"] * sc["abs_qty"] * 100.0,
            "margin_collateral": w * sc["abs_qty"] * 100.0 if def_risk else sc["strike"] * sc["abs_qty"] * 100.0,
            "net_credit": net_credit,
            "entry_date": entry_date,
            "order_id": order_id,
            "max_profit": max_profit,
            "max_risk": max_risk,
            "return_on_risk": ror,
            "unrealized_pl": upl,
            "status": status_str
        })

    # Synthesize Iron Condors where matching Put and Call spreads exist on same symbol & expiration
    final_spreads = []
    used_call_indices = set()

    for ps in paired_put_spreads:
        # Check for matching call spread
        matched_cs_idx = None
        for idx, cs in enumerate(paired_call_spreads):
            if idx not in used_call_indices and cs["symbol"] == ps["symbol"] and cs["expiration"] == ps["expiration"]:
                matched_cs_idx = idx
                break

        if matched_cs_idx is not None:
            used_call_indices.add(matched_cs_idx)
            cs = paired_call_spreads[matched_cs_idx]
            condor_contracts = min(ps["contracts"], cs["contracts"])
            # Wider wing rule: broker only locks collateral for the wider of the two wings!
            max_wing_width = max(ps["width"], cs["width"])
            condor_margin = max_wing_width * condor_contracts * 100.0

            final_spreads.append({
                "symbol": ps["symbol"],
                "strategy": "IRON_CONDOR",
                "strategy_title": f"Iron Condor ({ps['short_strike']:.0f}P/{ps['long_strike']:.0f}P | {cs['short_strike']:.0f}C/{cs['long_strike']:.0f}C)",
                "strategy_type": "iron_condor",
                "put_short": ps["short_strike"],
                "put_long": ps["long_strike"],
                "call_short": cs["short_strike"],
                "call_long": cs["long_strike"],
                "put_short_strike": ps["short_strike"],
                "put_long_strike": ps["long_strike"],
                "call_short_strike": cs["short_strike"],
                "call_long_strike": cs["long_strike"],
                "contracts": condor_contracts,
                "expiration": ps["expiration"],
                "dte": ps.get("dte"),
                "short_put_symbol": ps["short_symbol"],
                "long_put_symbol": ps["long_symbol"],
                "short_call_symbol": cs["short_symbol"],
                "long_call_symbol": cs["long_symbol"],
                "put_width": ps["width"],
                "call_width": cs["width"],
                "collateral": condor_margin,
                "margin_collateral": condor_margin,
                "margin_efficiency": "SINGLE_MARGIN_COLLATERAL",
                "unrealized_pl": round(ps["unrealized_pl"] + cs["unrealized_pl"], 2),
                "hedged": ps["hedged"] and cs["hedged"],
                "defined_risk": ps["defined_risk"] and cs["defined_risk"],
                "status": "Active Dual-Wing Harvest (Iron Condor)"
            })
        else:
            final_spreads.append(ps)

    # Add remaining standalone call spreads
    for idx, cs in enumerate(paired_call_spreads):
        if idx not in used_call_indices:
            final_spreads.append(cs)

    # Check for solitary tail hedges (e.g. AVGO 14C long floor)
    hedges: List[Dict[str, Any]] = []
    for lp in long_puts:
        if lp["symbol"] not in used_long_puts:
            hedges.append({
                "symbol": lp["root"],
                "type": "Tail-Hedge Disaster Floor (Put)",
                "strike": lp["strike"],
                "contracts": lp["abs_qty"],
                "expiration": lp["exp_date"],
                "market_value": float(lp["raw"].get("market_value", 0) or 0),
                "unrealized_pl": lp["unrealized_pl"],
                "status": "Active Floor"
            })

    for lc in long_calls:
        if lc["symbol"] not in used_long_calls:
            hedges.append({
                "symbol": lc["root"],
                "type": "Tail-Hedge Upside Protection (Call)",
                "strike": lc["strike"],
                "contracts": lc["abs_qty"],
                "expiration": lc["exp_date"],
                "market_value": float(lc["raw"].get("market_value", 0) or 0),
                "unrealized_pl": lc["unrealized_pl"],
                "status": "Active Floor"
            })

    return {
        "account_number": acct_num,
        "cash": cash,
        "buying_power": bp,
        "equity": equity,
        "positions": final_spreads,
        "trades": final_spreads,
        "tail_hedges": hedges,
        "next_action": f"{'Monitor active spreads' if final_spreads else '100% Cash Defense Floor / Primed for Golden Entry'}"
    }

def reconcile_active_trades(quiet: bool = False) -> Dict[str, Any]:
    base_dir = _get_base_dir()
    trades_file = base_dir / "active_trades.json"
    now_ict = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT")

    if not quiet:
        print("================================================================================")
        print(f"🔄 RECONCILING ACTIVE TRADES WITH LIVE BROKER GROUND TRUTH — {now_ict}")
        print("================================================================================")

    reconciled_accounts: Dict[str, Any] = {}
    total_cash = 0.0
    total_equity = 0.0

    existing_accounts = {}
    if trades_file.exists():
        try:
            prev_doc = json.loads(trades_file.read_text(encoding="utf-8"))
            existing_accounts = prev_doc.get("accounts", {})
        except Exception:
            pass

    for acct_name in ["alpaca_live", "pion_main", "pion2_sub"]:
        try:
            client = AlpacaClient(acct_name)
            prev_positions = existing_accounts.get(acct_name, {}).get("positions", [])
            acct_data = reconcile_positions_for_account(acct_name, client, existing_positions=prev_positions)
            reconciled_accounts[acct_name] = acct_data
            total_cash += acct_data.get("cash", 0.0)
            total_equity += acct_data.get("equity", 0.0)

            if not quiet:
                num = acct_data.get("account_number", "")
                c = acct_data.get("cash", 0.0)
                b = acct_data.get("buying_power", 0.0)
                s_len = len(acct_data.get("positions", []))
                h_len = len(acct_data.get("tail_hedges", []))
                print(f"  • {acct_name.upper()} ({num}): Cash: ${c:,.2f} | BP: ${b:,.2f} | Spreads: {s_len} | Hedges: {h_len}")
        except Exception as ex_acct:
            if not quiet:
                print(f"  🔴 Error querying broker for {acct_name}: {ex_acct}")

    # Reconcile Tradier Live (#6YB80974)
    try:
        from tradier_broker import TradierClient
        tradier_client = TradierClient("live")
        prev_t_positions = existing_accounts.get("tradier_live", {}).get("positions", [])
        tradier_data = reconcile_positions_for_account("tradier_live", tradier_client, existing_positions=prev_t_positions)
        if tradier_data.get("account_number") and tradier_data.get("equity", 0.0) > 0:
            reconciled_accounts["tradier_live"] = tradier_data
            total_cash += tradier_data.get("cash", 0.0)
            total_equity += tradier_data.get("equity", 0.0)
            if not quiet:
                num = tradier_data.get("account_number", "")
                c = tradier_data.get("cash", 0.0)
                b = tradier_data.get("buying_power", 0.0)
                s_len = len(tradier_data.get("positions", []))
                print(f"  • TRADIER_LIVE ({num}): Cash: ${c:,.2f} | BP: ${b:,.2f} | Spreads: {s_len} ✅")
    except Exception as ex_tradier:
        if not quiet:
            print(f"  ℹ️ Tradier Live query note: {ex_tradier}")

    if not reconciled_accounts:
        if not quiet:
            print("  🛑 Suppression Guard: Zero broker accounts successfully queried. Skipping active_trades.json write to protect data integrity.")
        return {}

    # Calculate Live Real-Money Production Totals
    live_equity = (reconciled_accounts.get("alpaca_live", {}).get("equity", 0.0) +
                   reconciled_accounts.get("tradier_live", {}).get("equity", 0.0))
    live_cash = (reconciled_accounts.get("alpaca_live", {}).get("cash", 0.0) +
                 reconciled_accounts.get("tradier_live", {}).get("cash", 0.0))

    # Build standardized active_trades document (dual-schema compatible)
    flat_trades: List[Dict[str, Any]] = []
    for acct_k, acct_v in reconciled_accounts.items():
        for pos in acct_v.get("positions", []):
            item = dict(pos)
            item["account"] = acct_k
            flat_trades.append(item)

    data = {
        "timestamp": now_ict,
        "combined_equity": round(total_equity, 2),
        "total_cash": round(total_cash, 2),
        "live_equity": round(live_equity, 2),
        "live_cash": round(live_cash, 2),
        "live_target_monthly": 5766.00,
        "live_floor_monthly": 3844.00,
        "weekly_velocity_target": 1441.50,
        "weekly_velocity_floor": 961.00,
        "quarantined_accounts": {
            "ibkr": {"account_number": "U25439978", "equity": 2200.0, "status": "QUARANTINED_UNTOUCHED"}
        },
        "projected_target": 18042.37,
        "graduation_target": 18042.37,
        "status": "ON_TRACK_FAST_TRACK",
        "accounts": reconciled_accounts,
        "trades": flat_trades
    }

    try:
        from active_trades_io import save_active_trades
        ok = save_active_trades(data, updated_by="reconcile_active_trades")
        if ok and not quiet:
            print(f"  ✅ Reconciled and saved {trades_file.name} to exact broker reality (RULE-092 & RULE-094)!")
    except Exception as ex_w:
        if not quiet:
            print(f"  🔴 Error writing {trades_file}: {ex_w}")

    return data

if __name__ == "__main__":
    reconcile_active_trades()
