#!/usr/bin/env python3
"""
live_liquidity_matrix_scanner.py — 20:50 ICT Live Liquidity Radar & Dynamic Options Matrix

Runs every evening at 20:50 ICT (20 mins post-US open) via Linux Cron:
1. Ingests verified_universe_catalog.json (18 institutional candidates across 6 sectors).
2. Queries live options snapshots from Alpaca to evaluate ground-truth market maker books.
3. Computes:
   - Natural Market Credit (Immediate Fill price)
   - Midpoint Credit (Zero Drag benchmark)
   - Spread Gap (Bid-Ask drag)
   - ROC % on Capital at Risk
   - Minimum Acceptable Credit (MAC) Floor
4. Eliminates dead-liquidity traps (Bid == 0.0, Credit < MAC Floor, Blown-out spreads).
5. Ranks fertile candidates within each of the 6 Sector Slots.
6. Emits live_liquidity_matrix.json for the 21:15 Golden Entry Engine.
7. Dispatches the 20:50 ICT Liquidity Radar Brief to Telegram.
"""

import sys
import os
import json
import datetime
import urllib.request
import ssl
from pathlib import Path
from typing import Dict, Any, List, Optional

sys.path.insert(0, str(Path(__file__).parent))
from alpaca_broker import AlpacaClient
from order_fill_tracker import calculate_mac_floor, send_telegram
from live_market_data import trend_sma20, _candidate_expiries

ICT = datetime.timezone(datetime.timedelta(hours=7))

THEME_WEIGHTS = {
    "1. AI Chips & Hardware": 95.0,
    "2. Data Center Power & Cooling": 88.0,
    "3. Inflation Defense & Hard Assets": 80.0,
    "4. Longevity & Healthcare": 85.0,
    "5. Consumer Staples & National Defense": 90.0,
    "6. Financial Services & Payment Rails": 82.0,
    "7. Broad Index & Market Hedging": 92.0,
    "8. Mega-Cap Cloud & Software Platform": 94.0
}


def scan_live_liquidity_matrix(account_type: str = "pion2_sub", notify_telegram: bool = True) -> Dict[str, Any]:
    now_ict = datetime.datetime.now(ICT).strftime("%Y-%m-%d %H:%M:%S ICT")
    base_dir = Path("/home/ubuntu/shared")
    if not base_dir.exists():
        base_dir = Path("/Users/SkonP/AI_Prompt/Obsidient/SkonVault/shared")

    cat_file = base_dir / "verified_universe_catalog.json"
    if not cat_file.exists():
        print(f"❌ Error: {cat_file} not found.")
        return {}

    catalog = json.loads(cat_file.read_text(encoding="utf-8"))
    client = AlpacaClient(account_type)

    print("=" * 85)
    print(f"📡 20:50 ICT LIVE LIQUIDITY RADAR & OPTIONS MATRIX SCANNER — {now_ict}")
    print("=" * 85)

    # 1. Market Open Check
    if not client.is_market_open():
        print("🛑 US Options Market is CLOSED. Aborting scan.")
        return {}

    sector_results: Dict[str, List[Dict[str, Any]]] = {}
    all_viable: List[Dict[str, Any]] = []

    for theme, cands in catalog.items():
        sector_results[theme] = []
        for cand in cands:
            sym = cand.get("symbol", "").upper()
            w = float(cand.get("width", 5.0))

            # RULE-089 & RULE-087: Resolve live spot and SMA20 trend FIRST
            t_res = trend_sma20(sym)
            is_above_sma20 = t_res.get("is_above_sma20", True) if t_res.get("ok") else True
            cur_spot = t_res.get("spot", 0.0) if t_res.get("ok") else 0.0

            # Fallback to broker latest bar if trend_sma20 spot is 0
            if cur_spot <= 0.0:
                try:
                    bbar = client.get_latest_bar(sym) if hasattr(client, 'get_latest_bar') else None
                    cur_spot = float(bbar.get("c", 0.0)) if bbar else 0.0
                except Exception:
                    cur_spot = 0.0

            if cur_spot <= 0.0:
                print(f"  🔴 {sym:<5} | 🛑 ZERO SPOT: Cannot determine live spot. Skipping candidate.")
                continue

            # Dynamically derive target short strike at ~95% of live spot
            step = 1.0 if cur_spot < 100 else 5.0
            s = round((cur_spot * 0.95) / step) * step

            # Multi-Tenor Liquidity Sweep across 10, 14, 30, 45 DTE
            exp_cands = _candidate_expiries(sym)
            if not exp_cands:
                exp_cands = [{"expiration": None, "dte": 28, "target_tenor": 30}]

            best_entry = None
            best_cvr = -1.0

            for exp_item in exp_cands:
                tgt_exp = exp_item.get("expiration")
                dte = exp_item.get("dte", 28)
                t_tag = exp_item.get("target_tenor", dte)

                resolved = client.resolve_spread_pair(sym, s, width=w, require_live_bid=False, target_expiration=tgt_exp)
                if not resolved:
                    continue

                short_sym = resolved["short_sym"]
                long_sym = resolved["long_sym"]
                quotes = client.get_option_snapshot([short_sym, long_sym])

                s_bid = quotes.get(short_sym, {}).get("bid", 0.0)
                s_ask = quotes.get(short_sym, {}).get("ask", 0.0)
                l_bid = quotes.get(long_sym, {}).get("bid", 0.0)
                l_ask = quotes.get(long_sym, {}).get("ask", 0.0)

                natural_credit = round(s_bid - l_ask, 2)
                s_mid = (s_bid + s_ask) / 2.0
                l_mid = (l_bid + l_ask) / 2.0
                mid_credit = round(s_mid - l_mid, 2)
                spread_gap = max(0.0, round(mid_credit - natural_credit, 2))
                mac_floor = calculate_mac_floor(w)

                roc_pct = round((natural_credit / w) * 100.0, 1) if w > 0 else 0.0
                daily_cvr = round(roc_pct / max(dte, 1), 2)

                # Strict OTM Sanity Filter
                is_otm = (resolved["short_strike"] < cur_spot * 0.98) and (cur_spot > 0)

                is_viable = (s_bid >= 0.08 and natural_credit >= mac_floor and spread_gap <= 0.60 and is_above_sma20 and is_otm)

                # 5-Factor Quant Scoring + Premium Density Factor + Daily Velocity Boost
                base_score = THEME_WEIGHTS.get(theme, 80.0) * 0.25
                density_score = min(25.0, (natural_credit / w) * 100.0 * 0.8) # Premium density up to 25 pts
                gap_penalty = min(15.0, spread_gap * 25.0)                    # Penalty for wide bid-ask spread
                liquidity_score = 25.0 if s_bid >= 0.20 else 15.0
                diversification_score = 20.0
                velocity_boost = min(10.0, max(0.0, (daily_cvr - 0.5) * 8.0))

                total_score = round(base_score + density_score + liquidity_score + diversification_score + velocity_boost - gap_penalty, 2)

                entry = {
                    "symbol": sym,
                    "theme": theme,
                    "short_strike": resolved["short_strike"],
                    "long_strike": resolved["long_strike"],
                    "width": w,
                    "exp_date": resolved["exp_date"],
                    "dte": dte,
                    "target_tenor": t_tag,
                    "daily_cvr": daily_cvr,
                    "short_sym": short_sym,
                    "long_sym": long_sym,
                    "short_bid": s_bid,
                    "short_ask": s_ask,
                    "long_bid": l_bid,
                    "long_ask": l_ask,
                    "natural_credit": natural_credit,
                    "mid_credit": mid_credit,
                    "spread_gap": spread_gap,
                    "mac_floor": mac_floor,
                    "roc_pct": roc_pct,
                    "total_score": total_score,
                    "is_viable": is_viable
                }

                if is_viable and daily_cvr > best_cvr:
                    best_cvr = daily_cvr
                    best_entry = entry
                elif best_entry is None:
                    best_entry = entry

            if best_entry:
                status_icon = "🟢" if best_entry["is_viable"] else "🔴"
                print(f"  {status_icon} {sym:<5} ({best_entry['exp_date']} [{best_entry['dte']}d]) | ${best_entry['short_strike']:.0f}P/${best_entry['long_strike']:.0f}P | Nat: ${best_entry['natural_credit']:.2f} | Mid: ${best_entry['mid_credit']:.2f} | CVR: {best_entry['daily_cvr']} | Score: {best_entry['total_score']:.1f}")
                if best_entry["is_viable"]:
                    sector_results[theme].append(best_entry)
                    all_viable.append(best_entry)

    # Sort each sector by total_score descending
    for theme in sector_results:
        sector_results[theme] = sorted(sector_results[theme], key=lambda x: x["total_score"], reverse=True)

    all_viable = sorted(all_viable, key=lambda x: x["total_score"], reverse=True)

    matrix_data = {
        "scanned_at_ict": now_ict,
        "scanned_account": account_type,
        "total_viable_candidates": len(all_viable),
        "top_overall_winner": all_viable[0] if all_viable else None,
        "all_viable": all_viable,
        "sectors": sector_results
    }

    # Save to live matrix file
    matrix_file = base_dir / "live_liquidity_matrix.json"
    matrix_file.write_text(json.dumps(matrix_data, indent=2), encoding="utf-8")
    print("=" * 85)
    print(f"✅ Emitted {matrix_file} with {len(all_viable)} confirmed fertile candidates!")

    # Telegram Notification
    if notify_telegram and all_viable:
        top_lines = []
        for v in all_viable[:5]:
            top_lines.append(f"• {v['symbol']} (${v['short_strike']:.0f}P/${v['long_strike']:.0f}P): Nat ${v['natural_credit']:.2f} | ROC {v['roc_pct']:.1f}% ({v['total_score']:.1f} pts)")
        
        top_text = "\n".join(top_lines)
        send_telegram(
            f"📡 20:50 ICT LIVE LIQUIDITY RADAR BRIEF\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 Date: {now_ict}\n"
            f"🟢 Viable Candidates: {len(all_viable)} / 18 active\n"
            f"🏆 Top 5 Live Fertile Setups:\n{top_text}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 21:15 Golden Entry primed for high-density execution!"
        )

    return matrix_data


if __name__ == "__main__":
    acct = "pion2_sub" if "--sub" in sys.argv else "pion_main"
    scan_live_liquidity_matrix(account_type=acct, notify_telegram=("--notify" in sys.argv))
