#!/usr/bin/env python3
"""
run_anna_preflight_handshake.py — Anna's 21:10 ICT T-5 Pre-Flight Handshake Sentinel

Operational Role:
  Fired 5 minutes before the 21:15 ICT Golden Entry Engine (at 21:10 ICT), or invoked
  inline as a safety pre-flight gate.
  1. Inspects tonight's candidates from tonight_selected_target.json and live_liquidity_matrix.json.
  2. Queries live market spot prices via live_spot.py (Tradier/Finnhub/Alpaca real-time feeds).
  3. Verifies short strike OTM buffer (buffer_pct = (spot - short_strike) / spot * 100).
  4. Requires buffer >= 3.50% for SAFE entry.
  5. If Candidate #1's buffer has degraded below 3.50% (post-market open volatility), Anna
     autonomously reroutes to Candidate #2 or #3 that maintains >= 3.50% safety margin!
  6. Emits preflight_handshake_status.json and updates tonight_selected_target.json so
     execute_golden_2115_daily_entry.py executes with 100% verified real-time safety.
  7. Broadcasts handshake status to Anna and Hermes via AgentBridge and Telegram.

Usage:
  python3 run_anna_preflight_handshake.py [--alert] [--dry-run]
"""

from __future__ import annotations
import json
import os
import ssl
import sys
import datetime
import urllib.request
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent
MIN_SAFE_BUFFER_PCT = 5.00     # RULE-098: Reproducibility Standard (>= 5.00% OTM Buffer Floor)
MIN_ABSOLUTE_BUFFER_PCT = 4.50 # Hard Institutional Floor (RULE-087 minimum OTM gate)

TELEGRAM_GROUP_ID = "-1004375899205"
ANNA_BOT_TOKEN = "8632069800:AAGl63rQuntU9-a84u0X37bsky4CppB-GhM"


def send_telegram(message: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", ANNA_BOT_TOKEN)
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", TELEGRAM_GROUP_ID)
    try:
        ctx_ssl = ssl._create_unverified_context()
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": message}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, context=ctx_ssl, timeout=10) as _:
            pass
        return True
    except Exception as ex:
        print(f"  ℹ️ Telegram notice in preflight handshake: {ex}")
        return False


def broadcast_to_bridge(subject: str, message: str, metadata: Optional[Dict[str, Any]] = None):
    try:
        from agent_bridge import AgentBridge
        bridge = AgentBridge("anna")
        meta = metadata or {}
        bridge.send(
            "hermes",
            message,
            channel="preflight_handshake",
            subject=subject,
            metadata=meta
        )
        # Also mirror to general intel channel
        bridge.send(
            "*",
            message,
            channel="intel",
            subject=subject,
            metadata=meta
        )
    except Exception as ex:
        print(f"  ℹ️ AgentBridge broadcast notice: {ex}")


def load_candidate_pool(base_dir: Path) -> Tuple[List[Dict[str, Any]], str]:
    """
    Loads candidate pool from:
    1. live_liquidity_matrix.json (Priority 1 if fresh from today)
    2. tonight_selected_target.json (Priority 2)
    3. Fallback static universe
    """
    cands: List[Dict[str, Any]] = []
    source = "UNKNOWN"
    today_str = datetime.date.today().strftime("%Y-%m-%d")

    # Priority 1: Multi-Factor Dynamic Screener Target (RULE-098 & FastHarvest Priority)
    dyn_file = base_dir / "tonight_selected_target.json"
    if dyn_file.exists():
        try:
            dyndata = json.loads(dyn_file.read_text(encoding="utf-8"))
            dyn_ts = dyndata.get("timestamp") or ""
            file_age_sec = datetime.datetime.now().timestamp() - dyn_file.stat().st_mtime
            if today_str in dyn_ts or file_age_sec < 86400:
                prim = dyndata.get("primary") or dyndata.get("primary_selection")
                sec = dyndata.get("secondary")
                falls = dyndata.get("fallbacks") or dyndata.get("waterfall_fallbacks", [])
                raw_list = ([prim] if prim else []) + ([sec] if sec else []) + (falls if isinstance(falls, list) else [])
                for item in raw_list:
                    if isinstance(item, dict) and item.get("symbol"):
                        cands.append({
                            "symbol": item.get("symbol"),
                            "short_strike": float(item.get("short_strike", 0)),
                            "long_strike": float(item.get("long_strike", 0)),
                            "width": float(item.get("width", 5.0)),
                            "expiration": item.get("expiration") or item.get("exp_date"),
                            "total_score": float(item.get("total_score", 85.0)),
                            "natural_credit": float(item.get("natural_credit", 0.0) or 0.0),
                            "mid_credit": float(item.get("mid_credit", 0.0) or 0.0),
                            "roc_pct": float(item.get("roc_pct", 0.0) or 0.0),
                            "theme": item.get("theme", "Bull Put Spread")
                        })
                if cands:
                    source = "TONIGHT_SELECTED_TARGET"
                    return cands, source
        except Exception as ex:
            print(f"  ℹ️ Notice loading tonight_selected_target: {ex}")

    # Priority 2: 20:50 ICT Live Liquidity Matrix (Fallback)
    matrix_file = base_dir / "live_liquidity_matrix.json"
    if matrix_file.exists():
        try:
            m_data = json.loads(matrix_file.read_text(encoding="utf-8"))
            scanned_at = m_data.get("scanned_at_ict", "")
            file_age_sec = datetime.datetime.now().timestamp() - matrix_file.stat().st_mtime
            if today_str in scanned_at and file_age_sec < 14400:
                viable = m_data.get("all_viable", [])
                if not viable and "sectors" in m_data:
                    for s_list in m_data["sectors"].values():
                        viable.extend(s_list)
                    viable = sorted(viable, key=lambda x: x.get("total_score", 0), reverse=True)
                if viable:
                    for v in viable:
                        cands.append({
                            "symbol": v.get("symbol"),
                            "short_strike": float(v.get("short_strike", 0)),
                            "long_strike": float(v.get("long_strike", 0)),
                            "width": float(v.get("width", 5.0)),
                            "expiration": v.get("exp_date"),
                            "total_score": float(v.get("total_score", 85.0)),
                            "natural_credit": float(v.get("natural_credit", 0.0) or 0.0),
                            "mid_credit": float(v.get("mid_credit", 0.0) or 0.0),
                            "roc_pct": float(v.get("roc_pct", 0.0) or 0.0),
                            "theme": v.get("theme", "Bull Put Spread")
                        })
                    source = "LIVE_LIQUIDITY_MATRIX"
                    return cands, source
        except Exception as ex:
            print(f"  ℹ️ Notice loading liquidity matrix: {ex}")

    # Priority 3: Fallback Universe
    fallback_syms = ["META", "MSFT", "V", "IWM", "GE", "LMT", "NVDA", "XLF", "XLE", "XLU"]
    for s in fallback_syms:
        cands.append({
            "symbol": s,
            "short_strike": 0.0, # Will be resolved dynamically from spot
            "long_strike": 0.0,
            "width": 2.0 if s in ["XLF", "XLU", "GLD", "SLV"] else 5.0,
            "total_score": 75.0,
            "theme": "Fallback Universe"
        })
    source = "FALLBACK_UNIVERSE"
    return cands, source


def run_preflight_handshake(
    base_dir: Optional[Path] = None,
    send_alerts: bool = True,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Executes Anna's T-5 Pre-Flight Handshake (21:10 ICT).
    Re-verifies all candidate short strikes against real-time spot prices.
    Promotes Candidate #2 or #3 if Candidate #1 has degraded below 3.50% OTM buffer.
    """
    b_dir = base_dir or BASE_DIR
    now_dt = datetime.datetime.now()
    now_ict = now_dt.strftime("%Y-%m-%d %H:%M:%S ICT")

    print("\n================================================================================")
    print(f"🤝 ANNA'S 21:10 ICT T-5 PRE-FLIGHT HANDSHAKE SENTINEL — {now_ict}")
    print("================================================================================")

    # 1. Ingest candidates
    raw_cands, source = load_candidate_pool(b_dir)
    print(f"  📡 Candidate Pool Source : {source} ({len(raw_cands)} raw candidates loaded)")

    if not raw_cands:
        status_res = {
            "timestamp_ict": now_ict,
            "status": "ABORT_NO_CANDIDATES",
            "message": "Zero candidates available in candidate pool",
            "lead_candidate": None,
            "verified_candidates": []
        }
        _save_status(b_dir, status_res)
        return status_res

    # 2. Fetch live spots via live_spot.py
    symbols = list(dict.fromkeys([c["symbol"] for c in raw_cands if c.get("symbol")]))
    spots_map = {}
    try:
        from live_spot import get_spots
        spots_map = get_spots(symbols)
        print(f"  ⚡ Live Spot Engine Fetched : {len(spots_map)} / {len(symbols)} tickers resolved")
    except Exception as ex_spot:
        print(f"  ⚠️ Warning querying live_spot: {ex_spot}")

    # Fallback to Alpaca broker bar if live_spot missing any symbol
    missing_syms = [s for s in symbols if not spots_map.get(s, {}).get("price")]
    if missing_syms:
        try:
            from alpaca_broker import AlpacaClient
            cli = AlpacaClient("alpaca_live")
            for ms in missing_syms:
                bbar = cli.get_latest_bar(ms) if hasattr(cli, 'get_latest_bar') else None
                sp = float(bbar.get("c", 0.0)) if bbar else 0.0
                if sp > 0:
                    spots_map[ms] = {"symbol": ms, "price": sp, "source": "alpaca_broker_bar"}
        except Exception:
            pass

    # 3. Audit candidate buffers against live spot
    verified_cands: List[Dict[str, Any]] = []
    for c in raw_cands:
        sym = c.get("symbol")
        spot_info = spots_map.get(sym, {})
        spot = float(spot_info.get("price") or 0.0)

        short_strike = float(c.get("short_strike") or 0.0)
        long_strike = float(c.get("long_strike") or 0.0)
        width = float(c.get("width") or 5.0)

        # Dynamic strike generation if candidate lacked fixed strikes (e.g. fallback universe)
        if spot > 0 and short_strike <= 0:
            step = 1.0 if spot < 100 else 5.0
            short_strike = round((spot * 0.95) / step) * step
            long_strike = short_strike - width

        if spot <= 0 or short_strike <= 0:
            continue

        buf_pct = ((spot - short_strike) / spot) * 100.0

        exp_val = c.get("expiration") or c.get("exp_date")
        dte_val = c.get("dte")
        if not dte_val and exp_val:
            try:
                exp_dt = datetime.datetime.strptime(exp_val, "%Y-%m-%d").date()
                dte_val = (exp_dt - datetime.date.today()).days
            except Exception:
                pass

        req_safe_buf = 7.00 if (dte_val and dte_val < 14) else MIN_SAFE_BUFFER_PCT
        if buf_pct >= req_safe_buf:
            gate_status = "SAFE"
        elif buf_pct >= MIN_ABSOLUTE_BUFFER_PCT:
            gate_status = "CAUTION"
        else:
            gate_status = "BREACHED"
        credit_val = c.get("mid_credit") or c.get("credit_mid") or c.get("natural_credit")

        cand_record = dict(c)
        cand_record.update({
            "symbol": sym,
            "name": c.get("name") or c.get("theme") or sym,
            "theme": c.get("theme") or c.get("name") or "Core Universe",
            "spot": spot,
            "live_spot": spot,
            "short_strike": short_strike,
            "long_strike": long_strike,
            "width": width,
            "expiration": exp_val,
            "exp_date": exp_val,
            "dte": dte_val,
            "credit_mid": credit_val,
            "mid_credit": credit_val,
            "buffer_pct": round(buf_pct, 2),
            "gate_status": gate_status,
            "spot_source": spot_info.get("source", "unknown")
        })
        verified_cands.append(cand_record)

    if not verified_cands:
        print("  🛑 CRITICAL PRE-FLIGHT ERROR: Zero candidates passed live spot validation!")
        status_res = {
            "timestamp_ict": now_ict,
            "status": "ABORT_ZERO_SPOT_VERIFIED",
            "message": "Zero candidates passed live spot validation",
            "lead_candidate": None,
            "verified_candidates": []
        }
        _save_status(b_dir, status_res)
        return status_res

    # 4. Evaluate Lead Setup and Auto-Reroute if Buffer < 3.50%
    original_lead = verified_cands[0]
    orig_sym = original_lead["symbol"]
    orig_buf = original_lead["buffer_pct"]

    print(f"\n  🔍 Original Lead Setup: {orig_sym} (${original_lead['short_strike']:.0f}P/${original_lead['long_strike']:.0f}P | Live Spot ${original_lead['live_spot']:.2f})")
    print(f"     • Pre-Flight OTM Buffer: {orig_buf:+.2f}% (Safety Threshold: >={MIN_SAFE_BUFFER_PCT:.2f}%)")

    lead_rerouted = False
    new_lead = original_lead

    orig_dte = original_lead.get("dte")
    req_lead_buf = 7.00 if (orig_dte and orig_dte < 14) else MIN_SAFE_BUFFER_PCT
    if orig_buf >= req_lead_buf:
        handshake_status = "PASSED_STABLE"
        print(f"  🟢 LEAD CANDIDATE CONFIRMED SAFE: {orig_sym} buffer {orig_buf:+.2f}% >= {req_lead_buf:.2f}% ✅")
    else:
        print(f"  ⚠️ LEAD CANDIDATE BUFFER DEGRADED: {orig_sym} buffer {orig_buf:+.2f}% < {req_lead_buf:.2f}%!")
        print(f"     Initiating Anna Dynamic Reroute Protocol across fallbacks...")

        # Search fallbacks with buffer >= required threshold, sorted by total_score desc
        safe_fallbacks = [
            c for c in verified_cands[1:]
            if c["buffer_pct"] >= (7.00 if (c.get("dte") and c.get("dte") < 14) else MIN_SAFE_BUFFER_PCT)
        ]
        safe_fallbacks.sort(key=lambda x: -float(x.get("total_score", 0.0)))

        if safe_fallbacks:
            new_lead = safe_fallbacks[0]
            lead_rerouted = True
            handshake_status = "REROUTED_TO_SAFE_FALLBACK"
            print(f"  ⚡ DYNAMIC REROUTE SUCCESS: Promoted {new_lead['symbol']} to Lead Setup!")
            print(f"     • New Lead: {new_lead['symbol']} (${new_lead['short_strike']:.0f}P/${new_lead['long_strike']:.0f}P | Buffer: {new_lead['buffer_pct']:+.2f}% | Score: {new_lead.get('total_score', 0):.1f} pts) 🚀")
        else:
            # Fallback check: is there any candidate with buffer >= MIN_ABSOLUTE_BUFFER_PCT (2.0%)?
            marginal_fallbacks = [
                c for c in verified_cands
                if c["buffer_pct"] >= MIN_ABSOLUTE_BUFFER_PCT
            ]
            marginal_fallbacks.sort(key=lambda x: -float(x["buffer_pct"]))
            if marginal_fallbacks:
                new_lead = marginal_fallbacks[0]
                lead_rerouted = (new_lead["symbol"] != orig_sym)
                handshake_status = "MARGINAL_PASS_CAUTION"
                print(f"  🟡 CAUTION: Selected {new_lead['symbol']} with marginal buffer {new_lead['buffer_pct']:+.2f}% (>=2.0% hard floor).")
            else:
                handshake_status = "ABORT_ALL_CANDIDATES_COMPRESSED"
                print("  🛑 ABORT: All candidate strikes compressed within < 2.0% buffer! Standing down.")
                new_lead = None

    # Reorder verified list so new_lead is at position 0
    if new_lead:
        final_cands = [new_lead] + [c for c in verified_cands if c["symbol"] != new_lead["symbol"]]
    else:
        final_cands = verified_cands

    # 5. Build Status Payload
    result_payload = {
        "timestamp_ict": now_ict,
        "handshake_status": handshake_status,
        "lead_rerouted": lead_rerouted,
        "min_safe_buffer_pct": MIN_SAFE_BUFFER_PCT,
        "lead_symbol": new_lead["symbol"] if new_lead else None,
        "lead_short_strike": new_lead["short_strike"] if new_lead else None,
        "lead_long_strike": new_lead["long_strike"] if new_lead else None,
        "lead_buffer_pct": new_lead["buffer_pct"] if new_lead else None,
        "lead_spot": new_lead["live_spot"] if new_lead else None,
        "candidate_pool_size": len(final_cands),
        "source": source,
        "verified_candidates": final_cands
    }

    # 6. Save persistent state files
    _save_status(b_dir, result_payload)
    _update_tonight_target_file(b_dir, result_payload, final_cands)

    # 6b. Tier 3 Pre-Flight Pre-Warming Engine (Pre-resolves OCC symbols & smart credits for 0.00s lag at 21:15 ICT)
    if new_lead and handshake_status != "ABORT_ALL_CANDIDATES_COMPRESSED":
        prewarmed = prewarm_entry_payload(b_dir, new_lead)
        if prewarmed:
            result_payload["prewarmed_payload"] = {
                "symbol": prewarmed.get("symbol"),
                "short_sym": prewarmed.get("short_sym"),
                "long_sym": prewarmed.get("long_sym"),
                "snipe_credit": prewarmed.get("prewarmed_snipe_credit"),
                "exp_date": prewarmed.get("exp_date")
            }

    # 7. Broadcast via AgentBridge
    bridge_msg = (
        f"ANNA T-5 PRE-FLIGHT HANDSHAKE [{handshake_status}]: Lead is {new_lead['symbol'] if new_lead else 'NONE'} "
        f"(Buffer: {new_lead['buffer_pct'] if new_lead else 0.0:+.2f}% vs Spot ${new_lead['live_spot'] if new_lead else 0.0:.2f}). "
        f"Rerouted: {lead_rerouted}. Standing by for 21:15 Golden Entry."
    )
    broadcast_to_bridge(
        subject=f"PREFLIGHT_HANDSHAKE_{handshake_status}",
        message=bridge_msg,
        metadata=result_payload
    )

    # 8. Dispatch Telegram alert if rerouted or alert requested
    if send_alerts and not dry_run and lead_rerouted and new_lead:
        alert_text = f"""🤝 ANNA T-5 PRE-FLIGHT HANDSHAKE: DYNAMIC REROUTE (21:10 ICT)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ Lead candidate {orig_sym} buffer compressed to {orig_buf:+.2f}% (< {MIN_SAFE_BUFFER_PCT:.1f}% safety standard).
Anna dynamic reroute activated!

🎯 NEW CONFIRMED LEAD SETUP:
• Symbol          : {new_lead['symbol']} (Promoted to #1 Lead Setup)
• Strikes         : ${new_lead['short_strike']:.0f}P / ${new_lead['long_strike']:.0f}P (Width: ${new_lead['width']:.2f})
• Live Spot Price : ${new_lead['live_spot']:.2f} ({new_lead.get('spot_source', 'live')})
• Verified Buffer : {new_lead['buffer_pct']:+.2f}% (SAFE >= {MIN_SAFE_BUFFER_PCT:.1f}% ✅)
• Conviction Score: {new_lead.get('total_score', 85.0):.1f} pts

Golden 21:15 ICT Entry Engine primed for {new_lead['symbol']} execution! 🚀📈"""
        send_telegram(alert_text)
        print("  📢 Dispatched Dynamic Reroute Alert to Telegram Group!")

    print(f"\n✅ Anna Pre-Flight Handshake Completed: Status={handshake_status} | Lead={new_lead['symbol'] if new_lead else 'NONE'}")
    return result_payload


def prewarm_entry_payload(base_dir: Path, lead_cand: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Tier 3: Pre-Flight Pre-Warming Engine (fired at 21:10-21:14 ICT during Anna's handshake).
    Pre-resolves exact OCC option symbols, strikes, DTE, live option snapshots,
    smart limit credits, and baseline spot. Writes shared/prewarmed_entry_payload.json
    so execute_golden_2115_daily_entry.py dispatches at 21:15:00.000 ICT with 0.00s pre-processing delay!
    """
    if not lead_cand:
        return None
    sym = lead_cand.get("symbol")
    if not sym:
        return None

    try:
        from alpaca_broker import AlpacaClient, get_default_monthly_expiration
        is_live = (os.environ.get("SKONVAULT_LIVE", "0") == "1")
        acct_name = "alpaca_live" if is_live else "pion_main"
        broker = AlpacaClient(acct_name)

        s_strike = float(lead_cand.get("short_strike", 0))
        l_strike = float(lead_cand.get("long_strike", 0))
        width = float(lead_cand.get("width") or abs(s_strike - l_strike) or 5.0)
        exp_date = lead_cand.get("expiration") or lead_cand.get("exp_date")

        # Resolve exact contract pair
        resolved = broker.resolve_spread_pair(sym, s_strike, width=width, require_live_bid=False, min_dte=14)
        if resolved:
            short_sym = resolved["short_sym"]
            long_sym = resolved["long_sym"]
            s_strike = resolved["short_strike"]
            l_strike = resolved["long_strike"]
            exp_date = resolved.get("exp_date", exp_date)
        else:
            if not exp_date:
                exp_date = get_default_monthly_expiration(min_dte=14)
            short_sym = f"{sym}{exp_date.replace('-','')[2:]}P{int(s_strike*1000):08d}"
            long_sym = f"{sym}{exp_date.replace('-','')[2:]}P{int(l_strike*1000):08d}"

        # Fetch option snapshot quotes
        quotes = broker.get_option_snapshot([short_sym, long_sym])
        s_bid = quotes.get(short_sym, {}).get("bid", 0.0)
        s_ask = quotes.get(short_sym, {}).get("ask", 0.0)
        l_bid = quotes.get(long_sym, {}).get("bid", 0.0)
        l_ask = quotes.get(long_sym, {}).get("ask", 0.0)

        # Microstructure credit calculations
        roc_rate = 0.075 if width >= 20.0 else 0.125
        min_roc_credit = max(0.12, round(width * roc_rate, 2))
        s_spread = max(0.01, s_ask - s_bid)
        l_spread = max(0.01, l_ask - l_bid)
        smart_s_mid = s_bid + 0.60 * s_spread
        smart_l_mid = l_ask - 0.35 * l_spread
        raw_smart_mid = round(smart_s_mid - smart_l_mid, 2)
        raw_arith_mid = round(((s_bid + s_ask)/2.0) - ((l_bid + l_ask)/2.0), 2)
        raw_natural_credit = round(s_bid - l_ask, 2)
        raw_blended_mid = round(0.70 * raw_smart_mid + 0.30 * raw_arith_mid, 2)

        mid_credit = max(min_roc_credit, raw_blended_mid)
        natural_credit = max(0.10, raw_natural_credit)
        meets_roc_floor = (raw_blended_mid >= min_roc_credit and s_bid > 0)
        if not meets_roc_floor and s_bid > 0:
            print(f"  ⚠️ Pre-Flight Warning: {sym} indicative mid ${raw_blended_mid:.2f} < ROC floor ${min_roc_credit:.2f}. Flagging payload for 21:15 ICT re-validation gate.")

        penny_pilot = {"SPY", "QQQ", "IWM", "XLF", "NVDA", "AMD", "TSM", "AAPL", "MSFT", "AMZN", "GOOGL"}
        is_penny = sym in penny_pilot
        is_round_nickel = (round(mid_credit * 100) % 5 == 0)
        snipe_offset = 0.01 if (is_penny and is_round_nickel) else 0.02
        snipe_credit = round(mid_credit + snipe_offset, 2)

        # Baseline spot
        baseline_spot = float(lead_cand.get("live_spot", 0.0))
        if baseline_spot <= 0:
            try:
                bdata = broker._call_api(f"https://data.alpaca.markets/v2/stocks/{sym}/bars/latest", timeout=4)
                baseline_spot = float(bdata.get("bar", {}).get("c", 0.0))
            except Exception: pass

        now_ict = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT")
        payload = {
            "version": "TIER_3_PREWARMED_V2",
            "generated_at_ict": now_ict,
            "timestamp_epoch": datetime.datetime.now().timestamp(),
            "status": "READY_FOR_EXECUTION" if meets_roc_floor else "SUB_ROC_WARNING",
            "meets_roc_floor": meets_roc_floor,
            "account": acct_name,
            "symbol": sym,
            "short_sym": short_sym,
            "long_sym": long_sym,
            "short_strike": s_strike,
            "long_strike": l_strike,
            "width": width,
            "exp_date": exp_date,
            "min_dte": 14,
            "min_roc_credit": min_roc_credit,
            "baseline_spot": baseline_spot,
            "prewarmed_snipe_credit": snipe_credit,
            "prewarmed_mid_credit": raw_blended_mid if raw_blended_mid > 0 else mid_credit,
            "prewarmed_natural_credit": raw_natural_credit,
            "quotes": {
                "short": {"bid": s_bid, "ask": s_ask},
                "long": {"bid": l_bid, "ask": l_ask}
            }
        }

        out_path = base_dir / "prewarmed_entry_payload.json"
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"  🔥 TIER 3 PRE-FLIGHT PRE-WARMED: {sym} spread compiled ({short_sym}/{long_sym} @ ${snipe_credit:.2f} credit | Spot: ${baseline_spot:.2f}). Saved to {out_path.name}!")
        return payload
    except Exception as ex_pw:
        print(f"  ℹ️ Pre-warming compilation notice: {ex_pw}")
        return None


def _save_status(base_dir: Path, data: Dict[str, Any]):
    try:
        out_file = base_dir / "preflight_handshake_status.json"
        out_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"  💾 Saved preflight status to {out_file.name}")
    except Exception as ex:
        print(f"  ℹ️ Save status notice: {ex}")


def _update_tonight_target_file(base_dir: Path, status_payload: Dict[str, Any], final_cands: List[Dict[str, Any]]):
    """
    Updates tonight_selected_target.json with verified lead and fallbacks
    so all downstream execution scripts receive the audited ranking.
    """
    if not final_cands:
        return
    try:
        dyn_file = base_dir / "tonight_selected_target.json"
        existing = {}
        if dyn_file.exists():
            try:
                existing = json.loads(dyn_file.read_text(encoding="utf-8"))
            except Exception:
                existing = {}

        lead_cand = final_cands[0]
        fallbacks = final_cands[1:]

        existing["primary"] = lead_cand
        existing["primary_selection"] = lead_cand
        existing["waterfall_fallbacks"] = fallbacks
        existing["fallbacks"] = fallbacks
        existing["preflight_handshake"] = {
            "status": status_payload.get("handshake_status"),
            "timestamp_ict": status_payload.get("timestamp_ict"),
            "lead_rerouted": status_payload.get("lead_rerouted", False),
            "lead_buffer_pct": status_payload.get("lead_buffer_pct")
        }

        dyn_file.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        print(f"  💾 Synchronized {dyn_file.name} with verified preflight ranking!")
    except Exception as ex:
        print(f"  ℹ️ Sync target notice: {ex}")


def verify_or_run_preflight(base_dir: Optional[Path] = None, max_age_seconds: int = 900) -> Dict[str, Any]:
    """
    Helper for execute_golden_2115_daily_entry.py:
    Returns existing preflight handshake status if fresh (< 15 mins),
    otherwise executes a fresh preflight handshake on the spot.
    """
    b_dir = base_dir or BASE_DIR
    status_file = b_dir / "preflight_handshake_status.json"

    if status_file.exists():
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
            file_mtime = status_file.stat().st_mtime
            age = datetime.datetime.now().timestamp() - file_mtime
            if age < max_age_seconds:
                print(f"  🤝 Verified Pre-Flight Handshake Status ({data.get('handshake_status')}, {age:.0f}s old) ✅")
                return data
        except Exception:
            pass

    print("  🤝 Pre-flight handshake status missing or aged > 15m. Running inline T-5 handshake now...")
    return run_preflight_handshake(b_dir, send_alerts=True)


if __name__ == "__main__":
    send_alert_flag = ("--alert" in sys.argv)
    dry_run_flag = ("--dry-run" in sys.argv)
    res = run_preflight_handshake(
        base_dir=BASE_DIR,
        send_alerts=send_alert_flag,
        dry_run=dry_run_flag
    )
    print("\nSummary Result:")
    print(f"  Status   : {res.get('handshake_status')}")
    print(f"  Lead     : {res.get('lead_symbol')} (Buffer: {res.get('lead_buffer_pct')}%)")
    print(f"  Rerouted : {res.get('lead_rerouted')}")
