#!/usr/bin/env python3
"""
run_uoa_scan.py — LIVE Institutional UOA Sweeper (Sweep #1 @ 21:10 ICT / Sweep #2 @ 21:45 ICT)

REBUILT 2026-09-23 (RULE-093 compliance): the previous version published FABRICATED telemetry —
call/put walls synthesised as spot ±8 rounded to $5, hardcoded `volume_oi_ratio: 2.85`,
`institutional_sweeps_count: 16`, wall OI 68k/49k, and a hardcoded $580 fallback spot when the live
quote failed (the exact failure RULE-087 exists to prevent). Downstream gates then "confirmed"
institutional conviction from those literals.

This version measures everything from the LIVE SPY option chain (Tradier PROD):
  • call/put walls  = strikes with the largest real open interest
  • max pain        = strike minimising total ITM payout
  • volume/OI ratio = real aggregate contract volume ÷ open interest for the expiry
  • UOA sweeps      = strikes with volume ≥ 3× OI (and ≥ 500 contracts) — written as `sweeps[]`,
                      the key the 21:15 Golden Entry engine actually consumes
  • No live spot / no chain  -> status "UOA_UNAVAILABLE", all metrics null, and the scan ABORTS its
                      conviction claims (never a fabricated price or ratio).
"""

import sys
import os
import json
import ssl
import datetime
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from alpaca_broker import AlpacaClient                      # noqa: E402
from live_spot import get_spot                              # noqa: E402
from live_market_data import expirations, chain             # noqa: E402

MIN_DTE = 3
SWEEP_VOL_OI = 3.0          # UOA detection: volume >= 3x open interest
SWEEP_MIN_VOLUME = 500      # ...on meaningful size
SECONDARY = ["QQQ", "TLT", "GLD", "XLF"]


def _pick_expiry(sym: str, min_dte: int = MIN_DTE):
    today = datetime.date.today()
    best = None
    for e in expirations(sym):
        try:
            dte = (datetime.datetime.strptime(e, "%Y-%m-%d").date() - today).days
        except Exception:
            continue
        if dte >= min_dte and (best is None or dte < best[1]):
            best = (e, dte)
    return best


def _max_pain(rows):
    """Strike minimising total in-the-money payout across all strikes."""
    strikes = sorted({r["strike"] for r in rows})
    if not strikes:
        return None
    best_k, best_val = None, None
    for s in strikes:
        total = 0.0
        for r in rows:
            oi = float(r.get("oi") or 0)
            if r["type"].startswith("c") and s > r["strike"]:
                total += (s - r["strike"]) * oi
            elif r["type"].startswith("p") and s < r["strike"]:
                total += (r["strike"] - s) * oi
        if best_val is None or total < best_val:
            best_k, best_val = s, total
    return best_k


def measure_flow(sym: str = "SPY"):
    """LIVE UOA measurement. Returns dict with ok=False + reason if data is unavailable."""
    spot = get_spot(sym).get("price")
    if not spot:
        return {"ok": False, "reason": "no live spot (RULE-087 zero-spot guard)", "symbol": sym}
    exp = _pick_expiry(sym, MIN_DTE)
    if not exp:
        return {"ok": False, "reason": "no listed expiry with DTE >= 3", "symbol": sym}
    exp_date, dte = exp
    rows = chain(sym, exp_date)
    if not rows:
        return {"ok": False, "reason": "empty option chain", "symbol": sym}
    calls = [r for r in rows if r["type"].startswith("c")]
    puts = [r for r in rows if r["type"].startswith("p")]

    def _wall(legs):
        legs = [r for r in legs if (r.get("oi") or 0) > 0]
        if not legs:
            return None, None
        top = max(legs, key=lambda r: r.get("oi") or 0)
        return top["strike"], top.get("oi")

    call_wall, call_oi = _wall(calls)
    put_wall, put_oi = _wall(puts)
    tot_vol = sum(float(r.get("volume") or 0) for r in rows)
    tot_oi = sum(float(r.get("oi") or 0) for r in rows)
    vol_oi = round(tot_vol / tot_oi, 2) if tot_oi > 0 else None

    sweeps = [{"symbol": sym, "expiration": exp_date, "type": r["type"].upper(),
               "strike": r["strike"], "volume": int(r.get("volume") or 0),
               "open_interest": int(r.get("oi") or 0),
               "vol_oi_ratio": round(float(r["volume"]) / float(r["oi"]), 2),
               "iv": r.get("iv"), "mid": r.get("mid")}
              for r in rows
              if (r.get("oi") or 0) > 0 and (r.get("volume") or 0) >= SWEEP_MIN_VOLUME
              and (float(r["volume"]) / float(r["oi"])) >= SWEEP_VOL_OI]
    sweeps.sort(key=lambda x: -x["vol_oi_ratio"])

    bias = "NEUTRAL"
    if put_wall and call_wall and spot > put_wall:
        bias = f"SUPPORTED_ABOVE_{int(put_wall)}_PUT_WALL"
    if put_wall and spot < put_wall:
        bias = f"BELOW_{int(put_wall)}_PUT_WALL_DEFENSIVE"
    return {"ok": True, "symbol": sym, "spot": spot, "expiry": exp_date, "dte": dte,
            "call_wall_strike": call_wall, "call_wall_oi": call_oi,
            "put_wall_strike": put_wall, "put_wall_oi": put_oi,
            "max_pain_strike": _max_pain(rows),
            "volume_oi_ratio": vol_oi, "total_volume": int(tot_vol), "total_oi": int(tot_oi),
            "institutional_sweeps_count": len(sweeps), "sweeps": sweeps[:12],
            "institutional_bias": bias, "min_dte_filter": MIN_DTE}


def execute_uoa_scan():
    print("=" * 60)
    print("🚀 EXECUTING LIVE UOA INSTITUTIONAL SWEEPER (measured, not modelled)")
    print("=" * 60)

    chk_broker = AlpacaClient("pion_main")
    is_holiday, holiday_name = chk_broker.is_market_holiday()
    if is_holiday:
        print(f"  🛑 US EXCHANGE HOLIDAY DETECTED: {holiday_name.upper()}! RULE-074 circuit breaker — "
              f"UOA sweeper standing down (zero flow checks on closed markets).")
        return

    now_ict = datetime.datetime.now().strftime("%Y-%m-%d %H:%M ICT")
    base_dir = Path("/home/ubuntu/shared") if Path("/home/ubuntu/shared").exists() else Path(__file__).parent

    # Lead candidate from the live screener (never a default symbol)
    lead_sym, lead_str, lead_buf = None, "No screener lead published", "n/a"
    t_file = base_dir / "tonight_selected_target.json"
    if t_file.exists():
        try:
            tdata = json.loads(t_file.read_text(encoding="utf-8"))
            prim = tdata.get("primary_selection") or tdata.get("primary") or {}
            if prim.get("symbol"):
                lead_sym = prim["symbol"]
                credit = prim.get("mid_credit") if prim.get("mid_credit") is not None else prim.get("natural_credit")
                if credit is None:
                    credit = prim.get("credit_mid") or prim.get("credit")
                buf = prim.get("buffer_pct") if prim.get("buffer_pct") is not None else prim.get("otm_pct")
                credit_str = f"+${credit:.2f}" if credit is not None else "n/a"
                buf_str = f"+{buf:.2f}%" if buf is not None else "n/a"
                lead_str = (f"{lead_sym} Bull Put Spread (${prim.get('short_strike'):.0f}P/"
                            f"${prim.get('long_strike'):.0f}P | exp {prim.get('expiration')} | "
                            f"credit {credit_str})")
                lead_buf = f"{buf_str} OTM"
        except Exception:
            pass

    flow = measure_flow("SPY")
    now_utc_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if flow.get("ok"):
        payload = {
            "timestamp": now_utc_str, "target_symbol": "SPY", "secondary_symbols": SECONDARY,
            "spot": flow["spot"], "expiration": flow["expiry"], "dte": flow["dte"],
            "call_wall_strike": flow["call_wall_strike"], "call_wall_oi": flow["call_wall_oi"],
            "put_wall_strike": flow["put_wall_strike"], "put_wall_oi": flow["put_wall_oi"],
            "max_pain_strike": flow["max_pain_strike"],
            "volume_oi_ratio": flow["volume_oi_ratio"],
            "total_volume": flow["total_volume"], "total_oi": flow["total_oi"],
            "institutional_sweeps_count": flow["institutional_sweeps_count"],
            "sweeps": flow["sweeps"], "min_dte_filter": flow["min_dte_filter"],
            "institutional_bias": flow["institutional_bias"],
            "data_source": "LIVE Tradier PROD chain (measured)",
            "status": "LIVE_BACKGROUND_CACHE_ACTIVE",
        }
        conviction = (f"{flow['volume_oi_ratio']}x aggregate Vol/OI | "
                      f"{flow['institutional_sweeps_count']} strike-level UOA prints (vol >= 3x OI)")
        report_text = f"""📡 LIVE UOA INSTITUTIONAL SWEEPER REPORT — {now_ict}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• SPY ${flow['spot']:,.2f} | Expiry {flow['expiry']} ({flow['dte']} DTE)
• Institutional Walls: Call ${flow['call_wall_strike']:.0f} (OI {flow['call_wall_oi']:,}) | Put ${flow['put_wall_strike']:.0f} (OI {flow['put_wall_oi']:,})
• Max Pain           : ${flow['max_pain_strike']:.0f}
• MEASURED Flow      : {conviction}
• Chains Aggregates  : volume {flow['total_volume']:,} / OI {flow['total_oi']:,}
• Institutional Bias : {flow['institutional_bias']}
• Lead Candidate     : {lead_str}
• Safety Buffer      : {lead_buf} below live spot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Tumbler ④ Trigger  : measured from live chain (no modelled walls)"""
    else:
        payload = {
            "timestamp": now_utc_str, "target_symbol": "SPY", "secondary_symbols": SECONDARY,
            "call_wall_strike": None, "call_wall_oi": None, "put_wall_strike": None,
            "put_wall_oi": None, "max_pain_strike": None, "volume_oi_ratio": None,
            "institutional_sweeps_count": None, "sweeps": [], "min_dte_filter": MIN_DTE,
            "institutional_bias": "UNKNOWN", "status": "UOA_UNAVAILABLE",
            "unavailable_reason": flow.get("reason"), "data_source": "LIVE (failed — no fabrication)",
        }
        report_text = f"""📡 UOA SWEEP — DATA UNAVAILABLE ({now_ict})
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Reason: {flow.get('reason')}
• Institutional flow conviction is NOT claimed this cycle (RULE-087/093: no blind execution,
  no modelled walls, no default spot).
• Lead Candidate: {lead_str} (unverified by flow)"""

    try:
        (base_dir / "uoa_live_cache.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"  ✅ uoa_live_cache.json written ({now_utc_str}) — status={payload['status']}")
    except Exception as ex_cache:
        print(f"  ⚠️ Could not update uoa_live_cache.json: {ex_cache}")

    print(report_text)

    DRY = ("--dry-run" in sys.argv) or os.getenv("UOA_DRY_RUN") == "1"
    if DRY:
        print("  🧪 DRY RUN — bridge mirror + Telegram dispatch suppressed")
        return

    try:
        from agent_bridge import AgentBridge
        AgentBridge("hermes").send("anna", report_text, channel="intel", subject="UOA Sweep Report")
        print("  ✅ Mirrored UOA report to bridge.db")
    except Exception as ex_b:
        print(f"  ℹ️ Bridge notice: {ex_b}")

    # Credentials from the env file (never hardcoded in source)
    try:
        from live_spot import _load_env
        env = _load_env()
    except Exception:
        env = {}
    token = env.get("HERMES_TELEGRAM_TOKEN") or env.get("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("TELEGRAM_GROUP_ID") or os.getenv("TELEGRAM_GROUP_ID", "") or "-1004375899205"
    if token and chat_id:
        try:
            ctx_ssl = ssl._create_unverified_context()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=json.dumps({"chat_id": chat_id, "text": report_text}).encode("utf-8"),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, context=ctx_ssl, timeout=10) as resp:
                if json.loads(resp.read().decode()).get("ok"):
                    print("  ✅ UOA report dispatched to Telegram")
        except Exception as e:
            print(f"  ⚠️ Telegram dispatch error: {e}")
    else:
        print("  ℹ️ Telegram token/chat not in env — skip dispatch")

    print("=" * 60)
    print("🎉 UOA SCAN COMPLETE (measured flow, honest status)")
    print("=" * 60)


if __name__ == "__main__":
    execute_uoa_scan()
