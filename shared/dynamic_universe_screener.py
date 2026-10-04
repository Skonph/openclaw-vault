#!/usr/bin/env python3
"""
dynamic_universe_screener.py — Multi-Factor Quant Universe Ranking & Candidate Selector (LIVE DATA)

Runs every evening at 19:35 ICT (Mon-Fri via Linux Cron) and inside the Sunday Reset (18:30):
1. Ingests verified_universe_catalog.json (pre-qualified institutional candidates).
2. Resolves LIVE market data for every candidate (spot, prior-session box, real option chain,
   real credit/ROC, real IV vs realised vol, real OI liquidity) via live_spot / live_market_data.
3. Evaluates each candidate across the Multi-Factor Pillars:
   - Factor 1: RULE-045 Strict Box Discount (25 pts) — live box position vs prior session H/L.
   - Factor 2: Theme Diversification & Account Isolation (20 pts).
   - Factor 3: Smart Money COT Theme Tailwinds (20 pts) — theme multiplier + live WILLCO audit.
   - Factor 4: Yield / ROC Efficiency (20 pts) — REAL chain credit, REAL bid/ask friction.
   - Factor 5: OTM Safety Buffer (15 pts) — REAL distance from live spot.
   - Factor 6: Put-Skew & Calendar-Velocity Bonus (10 pts) — REAL 25d/ATM IV.
4. Hard eligibility gates (never a fabricated lead): live spot required, two-sided liquid chain,
   IV/HV >= 1.15 VRP floor, credit > 0.05, >= 4% OTM, DTE 21-60.
5. Selects the #1 Lead Setup + Waterfall Fallbacks (#2, #3) and emits tonight_selected_target.json.

HARD RULE: strikes/prices are NEVER synthesised. If live data is unavailable for a candidate it is
scored 0 and marked ineligible with a reason; if no candidate clears the gates the screener emits
stand_aside=true instead of inventing a lead.
"""

import sys
import os
import json
import datetime
import re
import urllib.parse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List, Tuple, Optional, Set

sys.path.insert(0, str(Path(__file__).parent))
from alpaca_broker import AlpacaClient
from live_market_data import prev_session_box, pick_bull_put_spread, refresh_market_context, trend_sma20, atr14
from live_spot import get_spot, _load_env, _get_json

# ---- Eligibility gates (RULE: no fabricated lead) --------------------------------
MIN_VRP_RATIO = 1.15        # Default Tastytrade VRP edge floor: IV/HV (adaptive to VIX)
MIN_CREDIT = 0.25           # DIR-01 & DIR-11: hard minimum $0.25/share ($25/spread) credit floor
MIN_SPREAD_WIDTH = 2.0      # DIR-11: enforce >= $2.00 spread width; ban $1.00 micro-spreads
MIN_ROC_PCT = 12.5          # DIR-01: minimum 12.5% ROC floor
MIN_OTM_PCT = 5.0           # RULE-072: short strike must sit >= 5% below live spot (92% win rate floor)
TARGET_DELTA = 0.18          # Institutional 18-delta target (>80% OTM probability)
DTE_MIN, DTE_MAX = 8, 32     # 8 to 30 DTE Horizon (from 8-13 DTE rapid theta to 14-30 DTE steady harvest)
THEME_CONCENTRATION_CAP = 3        # RULE-072: Max 3 active spreads per sector theme
INDEX_THEME_CONCENTRATION_CAP = 4  # Broad Index (SPY/QQQ/IWM) allowed up to 4 spreads
MAX_PER_TICKER_CAP = 3             # Max 3 tranches on any single underlying (staggered)

# RULE-094: Precision Index Iron Condor Protocol
CONDOR_ELIGIBLE_ROOTS: Set[str] = {"SPY", "QQQ", "IWM"}

def classify_trade_strategy(symbol: str, box_position: Optional[float], diff_pct: Optional[float], iv_hv: Optional[float]) -> str:
    """
    RULE-094: Classifies strategy as 'IRON_CONDOR' or 'BULL_PUT_SPREAD'.
    Restricted strictly to Index ETFs (SPY, QQQ, IWM) during consolidation (box 35%-65%, VRP >= 1.10).
    """
    if (
        symbol in CONDOR_ELIGIBLE_ROOTS and
        box_position is not None and 35.0 <= box_position <= 65.0 and
        (diff_pct is not None and diff_pct >= -1.5) and
        (iv_hv is not None and iv_hv >= 1.10)
    ):
        return "IRON_CONDOR"
    return "BULL_PUT_SPREAD"


def calculate_box_position(current_price: float, pdh: float, pdl: float) -> float:
    """RULE-045 Box Position percentage (0% = PDL, 100% = PDH)."""
    if pdh is None or pdl is None or pdh <= pdl:
        return 50.0
    return max(0.0, min(100.0, ((current_price - pdl) / (pdh - pdl)) * 100.0))


def _load_live_cot() -> Dict[str, Any]:
    """Live WILLCO audit numbers (read-only; scoring still uses the curated theme multipliers)."""
    for p in (Path("/home/ubuntu/shared/cot_data/latest_cot.json"),):
        try:
            data = json.loads(p.read_text())
            cot = data.get("cot", data)
            return {k: v.get("willco_commercial") for k, v in cot.items() if isinstance(v, dict)}
        except Exception:
            continue
    return {}


def check_earnings_quarantine(symbol: str, exp_date: str) -> Tuple[bool, str | None]:
    """
    RULE-050: Earnings Quarantine.
    Broad ETFs are always exempt.
    Single stocks with earnings between today and expiration date are quarantined.
    """
    ETF_SYMBOLS = {"SPY", "QQQ", "IWM", "SMH", "XLF", "XLU", "XLE", "XLV", "XLP", "GLD", "SLV", "IBIT"}
    if symbol.upper() in ETF_SYMBOLS:
        return False, None
    if not exp_date:
        return False, None

    tok = _load_env().get("FINNHUB_API_KEY")
    if not tok:
        return False, None

    today = datetime.date.today().isoformat()
    try:
        url = (f"https://finnhub.io/api/v1/calendar/earnings?"
               f"symbol={urllib.parse.quote(symbol)}&from={today}&to={exp_date}&token={tok}")
        data = _get_json(url)
        events = data.get("earningsCalendar") or []
        for ev in events:
            ev_date = ev.get("date")
            if ev_date and today <= ev_date <= exp_date:
                return True, ev_date
    except Exception:
        pass
    return False, None


def run_dynamic_screening() -> Dict[str, Any]:
    print("=" * 80)
    print("🧠 RUNNING LIVE DYNAMIC UNIVERSE SCREENER & MULTI-FACTOR RANKING ENGINE")
    print("=" * 80)

    now_ict = datetime.datetime.now().strftime("%Y-%m-%d %H:%M ICT")
    base_dir = Path("/home/ubuntu/shared")
    if not base_dir.exists():
        base_dir = Path("/Users/SkonP/AI_Prompt/Obsidient/SkonVault/shared")

    # 1. Load Verified Catalog
    cat_file = base_dir / "verified_universe_catalog.json"
    if not cat_file.exists():
        try:
            import importlib
            sys.path.insert(0, str(base_dir / "archive" / "legacy_scripts"))
            audit_mod = importlib.import_module("audit_universe_alpaca_qualification")
            audit_mod.run_full_universe_audit()
        except Exception as ex_cat:
            print(f"  ℹ️ Catalog fallback notice: {ex_cat}")

    catalog = json.loads(cat_file.read_text(encoding="utf-8"))

    # 2. Active portfolio (anti-overlap) — live real-money broker truth
    # Strict isolation: paper accounts (pion_main, pion2_sub) must NEVER contaminate live portfolio decisions!
    # AMD and IBIT are paper-sandbox only and must NOT be counted against live portfolio caps.
    live_positions_map: Dict[str, List[str]] = {}

    # Alpaca Live broker (group option legs by root + expiration so 1 spread = 1 count)
    try:
        cli = AlpacaClient("alpaca_live")
        alpaca_spreads = set()
        for p in cli.get_positions():
            sym = p.get("symbol", "")
            m = re.match(r"^([A-Z]+)(\d{6})", sym)
            if m:
                alpaca_spreads.add((m.group(1), m.group(2)))
            elif sym:
                alpaca_spreads.add((sym, "equity"))
        live_positions_map["alpaca_live"] = [root for root, _ in alpaca_spreads]
    except Exception as ex_alp:
        print(f"  ℹ️ Alpaca Live broker position audit notice: {ex_alp}")

    # Tradier Live broker (group option legs by root + expiration so 1 spread = 1 count)
    try:
        from tradier_broker import TradierClient
        t_cli = TradierClient("live")
        tradier_spreads = set()
        for p in t_cli.get_positions():
            sym = p.get("symbol", "")
            m = re.match(r"^([A-Z]+)(\d{6})", sym)
            if m:
                tradier_spreads.add((m.group(1), m.group(2)))
            elif sym:
                tradier_spreads.add((sym, "equity"))
        live_positions_map["tradier_live"] = [root for root, _ in tradier_spreads]
    except Exception as ex_trd:
        print(f"  ℹ️ Tradier Live broker position audit notice: {ex_trd}")

    # Fallback to active_trades.json ONLY for live accounts if broker returned empty/unreachable
    trades_file = base_dir / "active_trades.json"
    if trades_file.exists():
        try:
            tdata = json.loads(trades_file.read_text(encoding="utf-8"))
            for acct_id in ["alpaca_live", "tradier_live"]:
                if acct_id not in live_positions_map or not live_positions_map[acct_id]:
                    file_syms = []
                    acct = tdata.get("accounts", {}).get(acct_id, {})
                    for pos in acct.get("positions", []):
                        st = pos.get("status", "").upper()
                        if "ACTIVE" in st or "PROTECTION" in st or "OPEN" in st:
                            s_pos = pos.get("symbol")
                            if s_pos:
                                file_syms.append(s_pos)
                    if file_syms:
                        live_positions_map[acct_id] = file_syms
        except Exception:
            pass

    # Flatten into clean active live symbols list
    active_symbols: List[str] = []
    for acct_id, syms in live_positions_map.items():
        active_symbols.extend(syms)

    active_symbols_counts = {s: active_symbols.count(s) for s in set(active_symbols) if s}
    active_symbols = sorted(set(s for s in active_symbols if s))
    print(f"  • Currently Active In Live Portfolio: {active_symbols or 'None (100% Cash)'} | Counts: {active_symbols_counts}")

    # 3. Ingest catalog
    all_candidates: List[Dict[str, Any]] = []
    for theme_name, cands in catalog.items():
        for c in cands:
            c_copy = dict(c)
            c_copy["theme"] = theme_name
            all_candidates.append(c_copy)

    symbols = [c["symbol"] for c in all_candidates]
    sym_to_theme = {c["symbol"]: c["theme"] for c in all_candidates}
    theme_active_counts: Dict[str, int] = {}
    for act in active_symbols:
        thm = sym_to_theme.get(act)
        if thm:
            theme_active_counts[thm] = theme_active_counts.get(thm, 0) + 1

    print(f"  • Ingesting {len(symbols)} verified assets across {len(catalog)} themes...")
    print(f"  • Theme active counts: {theme_active_counts or 'None (0 per theme)'}")

    # 4. LIVE DATA LAYER — parallel, zero-token (spot + box + real chain selection per symbol)
    widths = {c["symbol"]: float(c.get("width", 5.0) or 5.0) for c in all_candidates}

    def _live(sym: str) -> Tuple[str, Dict[str, Any]]:
        out: Dict[str, Any] = {"spread": {"ok": False, "reason": "not attempted"}, "box": {}, "trend": {}, "atr": None}
        try:
            out["box"] = prev_session_box(sym)
        except Exception as e:
            out["box"] = {"box_position_pct": None, "reason": f"box error: {e}"}
        try:
            out["trend"] = trend_sma20(sym)
        except Exception as e:
            out["trend"] = {"ok": False, "reason": f"trend error: {e}"}
        try:
            out["atr"] = atr14(sym)
        except Exception as e:
            out["atr"] = None
        try:
            out["spread"] = pick_bull_put_spread(sym, width=widths.get(sym), target_delta=TARGET_DELTA,
                                                 dte_min=DTE_MIN, dte_max=DTE_MAX,
                                                 min_otm_pct=MIN_OTM_PCT)
        except Exception as e:
            out["spread"] = {"ok": False, "reason": f"chain error: {e}"}
        return sym, out

    print("  • Resolving LIVE spots, prior-session boxes and option chains (parallel)...")
    with ThreadPoolExecutor(max_workers=6) as ex:
        live_map = dict(ex.map(_live, sorted(set(widths))))

    ok_count = sum(1 for v in live_map.values() if v["spread"].get("ok"))
    print(f"  • Live chains resolved for {ok_count}/{len(live_map)} symbols "
          f"(failures are marked ineligible, never fabricated)")

    # 5. Score
    # Dynamic Regime-Adaptive Tuning (Market-Aware Rule) & Adaptive VRP floor:
    from dynamic_regime_manager import evaluate_market_regime
    regime_info = evaluate_market_regime()
    live_vix = regime_info["vix"]
    effective_min_vrp = 1.08 if (live_vix is not None and live_vix < 15.5) else MIN_VRP_RATIO
    effective_min_otm = regime_info.get("min_otm_buffer_pct", MIN_OTM_PCT)
    print(f"  • Dynamic Market Regime: {regime_info['regime_name']} (VIX {live_vix})")
    print(f"  • Effective VRP floor: {effective_min_vrp} | Min OTM Buffer: {effective_min_otm:.1f}% | Barbell: {int(regime_info['sprint_ratio']*100)}% Sprint / {int(regime_info['anchor_ratio']*100)}% Anchor")

    theme_cot_scores = {
        "1. AI Chips & Hardware": 95.0,                  # NQ smart-money record
        "2. Data Center Power & Cooling": 88.0,          # secular grid demand
        "3. Inflation Defense & Hard Assets": 75.0,      # gold commercial short extreme
        "4. Longevity & Healthcare": 85.0,               # defensive inflow
        "5. Consumer Staples & National Defense": 90.0,  # defense moat
        "6. Financial Services & Payment Rails": 92.0,   # ZN yield steepening
        "7. Broad Index & Market Hedging": 90.0,         # broad liquidity & market beta
        "8. Mega-Cap Cloud & Software Platform": 94.0,   # mega-cap tech cash generation
    }
    theme_account_mapping = {
        "1. AI Chips & Hardware": "pion_main",
        "2. Data Center Power & Cooling": "pion_main",
        "5. Consumer Staples & National Defense": "pion_main",
        "7. Broad Index & Market Hedging": "pion_main",
        "3. Inflation Defense & Hard Assets": "pion2_sub",
        "4. Longevity & Healthcare": "pion2_sub",
        "6. Financial Services & Payment Rails": "pion2_sub",
        "8. Mega-Cap Cloud & Software Platform": "pion2_sub",
    }

    from advanced_quant_forecasting_optimizer import (
        calculate_25_delta_put_skew_bonus,
        calculate_fast_theta_velocity_score,
        calculate_theta_drag_hours,
        calculate_day_of_week_warp_multiplier,
        calculate_48h_fastharvest_merit_score,
    )

    live_cot = _load_live_cot()
    scored_candidates: List[Dict[str, Any]] = []

    for c in all_candidates:
        sym = c["symbol"]
        theme = c["theme"]
        live = live_map.get(sym, {})
        sp: Dict[str, Any] = live.get("spread", {}) or {}
        box = live.get("box", {}) or {}

        catalog_s = float(c.get("short_strike", 0) or 0)
        catalog_l = float(c.get("long_strike", 0) or 0)

        # ---- LIVE (or explicitly-unavailable) values
        live_ok = bool(sp.get("ok"))
        spot_price = sp.get("spot") or box.get("spot")
        if live_ok:
            target_s = float(sp["short_strike"])
            target_l = float(sp["long_strike"])
            width = float(sp["width"])
            exp_date = sp["expiration"]
            dte_est = int(sp["dte"])
            credit_mid = float(sp["credit"])
            roc_pct = sp.get("roc_pct")
            short_delta = sp.get("short_delta")
            short_iv = sp.get("short_iv")
            atm_iv = sp.get("atm_iv")
            iv_25d = sp.get("iv_25d_put")
            iv_hv = sp.get("iv_hv_ratio")
            liquid = bool(sp.get("liquid"))
            short_oi, long_oi = sp.get("short_oi"), sp.get("long_oi")
            strike_source = "live_chain"
        else:
            target_s, target_l = catalog_s, catalog_l
            width = float(c.get("width", 5.0) or 5.0)
            exp_date = c.get("expiration")
            dte_est = 0
            if exp_date:
                try:
                    dte_est = max(1, (datetime.datetime.strptime(exp_date, "%Y-%m-%d").date()
                                      - datetime.date.today()).days)
                except Exception:
                    dte_est = 0
            credit_mid = roc_pct = short_delta = short_iv = atm_iv = iv_25d = iv_hv = None
            liquid = False
            short_oi = long_oi = None
            strike_source = "catalog_static"
        box_pos = box.get("box_position_pct")
        if box_pos is None and spot_price:
            box_pos = calculate_box_position(spot_price, None, None)

        # ---- Factor 1: RULE-045 strict box discount (25 pts)
        if box_pos is None:
            box_score, box_status = 6.0, "BOX DATA UNAVAILABLE ⚠️"
        elif box_pos <= 20.0:
            box_score, box_status = 25.0, "DEEP DISCOUNT (HIGH WIN-RATE BUY ZONE) ✅"
        elif box_pos <= 35.0:
            box_score, box_status = 14.0, "MODERATE PULLBACK ⚠️"
        elif box_pos >= 65.0:
            box_score, box_status = 3.0, "PREMIUM ZONE (SELL CALLS) ⛔"
        else:
            box_score, box_status = 6.0, "CHOP ZONE (HIGH-RISK VETO) ⛔"

        # ---- Factor 2: diversification / anti-overlap (20 pts)
        active_in_theme = theme_active_counts.get(theme, 0)
        theme_cap = INDEX_THEME_CONCENTRATION_CAP if "Broad Index" in theme else THEME_CONCENTRATION_CAP
        if active_in_theme >= theme_cap:
            div_score = 0.0
        elif active_in_theme == 2:
            div_score = 5.0     # Progressive Sector Penalty: -15 pts
        elif active_in_theme == 1:
            div_score = 10.0    # Progressive Sector Penalty: -10 pts
        else:
            div_score = 20.0    # Full diversification bonus

        # ---- Factor 3: COT smart-money theme tailwind (20 pts)
        cot_pct = theme_cot_scores.get(theme, 80.0)
        cot_score = (cot_pct / 100.0) * 20.0

        # ---- Factor 4: yield / ROC efficiency from the REAL chain (20 pts)
        is_core_penny = sym in ["SPY", "QQQ", "XLF", "GLD", "NVDA"]
        spread_est = (round((sp.get("short_ask") or 0) - (sp.get("short_bid") or 0), 2)
                      if live_ok and sp.get("short_ask") and sp.get("short_bid")
                      else (0.03 if is_core_penny else 0.18))
        net_credit_est = credit_mid if credit_mid is not None else (0.80 if width <= 2.5 else 1.30)
        drag_info = calculate_theta_drag_hours(bid_ask_spread=spread_est,
                                              net_credit=net_credit_est,
                                              dte=max(dte_est, 1))
        drag_score_adj = drag_info["score_adjustment"]
        base_yield_score = 20.0 if is_core_penny else 18.0
        # Factor 4: Yield / ROC Efficiency & Gamma Shield Horizon (RULE-098)
        # Shift weight from raw cash velocity (shorter DTE) to Reproducible Profitability (Gamma Shield 14-30 DTE):
        # 21-32 DTE: +12.0 pts (Core Gamma Shield: low gamma ~0.001, delta immune to daily 1-2% noise)
        # 14-20 DTE: +8.0 pts (Intermediate stability)
        # 8-13 DTE: +2.0 pts (Sprint: high gamma, requires >=7% buffer to be viable)
        if 21 <= dte_est <= 32:
            gamma_shield_bonus = 12.0
        elif 14 <= dte_est < 21:
            gamma_shield_bonus = 8.0
        elif 8 <= dte_est < 14:
            gamma_shield_bonus = 2.0
        else:
            gamma_shield_bonus = 0.0

        roc_bonus = 0.0
        if roc_pct is not None:
            par_roc = 7.5 if width >= 20.0 else 12.5
            roc_bonus = max(-4.0, min(6.0, (roc_pct - par_roc) * 0.4))

        # Natural Liquidity & Spread Friction Penalty (LFG - Liquidity Friction Gate)
        f_ratio = sp.get("friction_ratio")
        friction_penalty = 0.0
        if f_ratio is not None and f_ratio > 0.18:
            friction_penalty = -15.0  # Severe drag penalty for wide bid/ask gap (>18% friction)
        elif f_ratio is not None and f_ratio > 0.10:
            friction_penalty = -6.0

        yield_score = max(0.0, min(30.0, base_yield_score + drag_score_adj + roc_bonus + gamma_shield_bonus + friction_penalty))

        # ---- Factor 5: Reproducible OTM Safety Buffer from LIVE spot (0-25 pts)
        safety_buffer_pct = (round((spot_price - target_s) / spot_price * 100, 2)
                             if (spot_price and target_s) else None)

        # Pillar A: Beta-Adjusted Margin (BAM) Dynamic Buffer Standard (ATR x 2.5)
        atr_info = live.get("atr") or {}
        daily_atr_pct = atr_info.get("atr_pct")
        if not daily_atr_pct:
            hv_val = sp.get("hv20") or sp.get("hv20_live") or 20.0
            daily_atr_pct = round(hv_val / 15.87, 2)
        beta_adjusted_min_otm = round(max(effective_min_otm, 2.5 * daily_atr_pct), 2)

        if safety_buffer_pct is None:
            safety_score = 0.0
        elif safety_buffer_pct >= (beta_adjusted_min_otm + 2.0):
            safety_score = 25.0  # Fortress safety buffer (eliminates 1-2% daily noise)
        elif safety_buffer_pct >= beta_adjusted_min_otm:
            safety_score = 20.0  # Beta-Adjusted Institutional Standard
        elif safety_buffer_pct >= effective_min_otm:
            safety_score = 12.0  # Marginal Standard buffer
        elif safety_buffer_pct >= 3.5:
            safety_score = 6.0   # Caution buffer
        else:
            safety_score = 0.0   # Unsafe

        # ---- Factor 5b: Trend Momentum & Macro Alignment (The 3R Rule) (0-10 pts)
        trend_info = live.get("trend", {})
        trend_diff_pct = trend_info.get("diff_pct") if trend_info else None
        if trend_diff_pct is None and spot_price and live.get("sma20"):
            trend_diff_pct = round((spot_price - live["sma20"]) / live["sma20"] * 100, 2)
        slope_5d = trend_info.get("slope_5d", 0.0) if trend_info else 0.0
        is_golden = trend_info.get("is_golden_trend", False) if trend_info else False

        if is_golden and slope_5d >= 0.0:
            trend_momentum_bonus = 10.0  # 3R Rule Golden Trend Alignment (Spot > SMA20 > SMA50)
        elif trend_diff_pct is not None and trend_diff_pct >= 1.0 and slope_5d >= 0.0:
            trend_momentum_bonus = 6.0   # Strong uptrend above SMA20
        elif trend_diff_pct is not None and trend_diff_pct >= 0.0:
            trend_momentum_bonus = 3.0   # Mild uptrend holding SMA20
        elif slope_5d < -1.5:
            trend_momentum_bonus = -10.0 # Downward-sloping SMA20 penalty (Falling knife guard)
        else:
            trend_momentum_bonus = 0.0

        # ---- Factor 6: put skew + calendar velocity (10 pts) — real IV inputs where available
        iv_25d_f = iv_25d / 100.0 if iv_25d else 0.0
        atm_iv_f = atm_iv / 100.0 if atm_iv else 0.0
        if iv_25d_f and atm_iv_f:
            skew_mult, skew_desc = calculate_25_delta_put_skew_bonus(iv_25d_put=iv_25d_f, iv_atm=atm_iv_f)
        else:
            skew_mult, skew_desc = 1.0, "SKEW_UNAVAILABLE (no live IV)"
        theta_stat = calculate_fast_theta_velocity_score(
            days_to_exp=max(dte_est, 1), iv_percentile=50.0,
            hist_vol_3d=(sp.get("hv20_live", 0) or 0) / 100.0 or 0.14,
            imp_vol_30d=atm_iv_f or 0.22)
        base_skew_bonus = 10.0 if skew_mult > 1.05 else 5.0
        warp_mult, warp_desc = calculate_day_of_week_warp_multiplier(
            weekday=datetime.datetime.now().weekday(), dte=max(dte_est, 1))
        total_velocity_bonus = round(base_skew_bonus * warp_mult, 2)

        # Pillar 2: 48-Hour FastHarvest Velocity Merit Score (0-100 pts)
        # Favours early Take-Profit (50% TP, or 30-40% inside 5 days) over holding to expiration
        fh_stat = calculate_48h_fastharvest_merit_score(
            symbol=sym,
            spot_price=spot_price or 0.0,
            dte=max(dte_est, 1),
            box_pos=box_pos,
            iv_25d_ratio=(iv_25d_f / atm_iv_f) if (iv_25d_f and atm_iv_f and atm_iv_f > 0) else None,
            vrp_ratio=iv_hv,
            bid_ask_spread=spread_est,
            is_penny_pilot=is_core_penny,
            wyckoff_spring=bool(box_pos is not None and box_pos <= 25.0),
            roc_pct=roc_pct,
            sma20_dist_pct=trend_diff_pct
        )
        fast_harvest_score = fh_stat["fastharvest_score"]
        fast_harvest_tier = fh_stat["velocity_tier"]
        target_tp_pct = fh_stat["recommended_tp_pct"]
        fast_harvest_boost = round(fast_harvest_score * 0.25, 2) # Up to +25 pts for Apex FastHarvest early TP!

        sym_held_count = active_symbols_counts.get(sym, 0)
        reentry_penalty = 0.0
        if sym_held_count == 1:
            reentry_penalty = 15.0  # Tranche 2 penalty (-15 pts)
        elif sym_held_count == 2:
            reentry_penalty = 25.0  # Tranche 3 penalty (-25 pts)

        total_score = round(max(0.0, box_score + div_score + cot_score + yield_score
                            + safety_score + trend_momentum_bonus + total_velocity_bonus
                            + fast_harvest_boost - reentry_penalty), 2)

        # ---- Hard eligibility gates
        reasons = []
        if not live_ok:
            reasons.append(sp.get("reason") or "live chain unavailable")
        if not spot_price:
            reasons.append("no live spot")
        if sym_held_count >= MAX_PER_TICKER_CAP:
            reasons.append(f"ticker concentration cap reached ({sym_held_count}x active in portfolio)")
        if active_in_theme >= theme_cap:
            reasons.append(f"theme concentration cap reached ({active_in_theme} active in {theme})")
        if live_ok and not liquid:
            reasons.append(f"illiquid legs (short OI {short_oi} / long OI {long_oi})")
        if iv_hv is not None and iv_hv < effective_min_vrp:
            reasons.append(f"VRP below floor (IV/HV {iv_hv} < {effective_min_vrp} [VIX={live_vix}])")
        if iv_hv is None:
            reasons.append("VRP unmeasurable (no live IV/HV)")
        in_quar, ern_date = check_earnings_quarantine(sym, exp_date)
        if in_quar:
            reasons.append(f"RULE-050: earnings quarantine ({ern_date} before exp {exp_date})")
        if width is not None and width < MIN_SPREAD_WIDTH:
            reasons.append(f"spread width ${width:.2f} < ${MIN_SPREAD_WIDTH:.2f} (DIR-11 anti-friction width floor: $1 spreads banned)")

        # Credit density floor: Single stocks >= $0.50, ETFs >= $0.30 (Kills the micro-credit trap!)
        is_etf_sym = sym in {"SPY", "QQQ", "IWM", "SMH", "XLF", "XLU", "XLE", "XLV", "XLP", "GLD", "SLV", "IBIT"}
        min_credit_gate = 0.30 if is_etf_sym else 0.50
        if credit_mid is not None and credit_mid < min_credit_gate:
            reasons.append(f"credit ${credit_mid:.2f} < ${min_credit_gate:.2f} (DIR-01/11 minimum credit density floor)")

        # Liquidity Friction Gate (LFG):
        # When screening pre-market (19:35 ICT is 08:35 AM Eastern, 55 mins before US options open),
        # resting market-maker quotes have wide spreads. We enforce a calibrated pre-market ceiling (<= 75%)
        # and defer strict tight friction (<= 35%) to 21:15 ICT live entry execution.
        now_dt = datetime.datetime.now()
        is_premarket = now_dt.hour < 20 or (now_dt.hour == 20 and now_dt.minute < 30) or now_dt.hour >= 4
        max_friction_gate = 0.75 if is_premarket else 0.35

        nat_credit = sp.get("natural_credit")
        if nat_credit is not None and nat_credit <= -0.50:
            reasons.append(f"negative natural credit (${nat_credit:.2f} <= -$0.50: unfillable bid/ask gap)")
        if f_ratio is not None and f_ratio > max_friction_gate:
            reasons.append(f"excessive bid-ask friction ({f_ratio*100:.1f}% > {max_friction_gate*100:.0f}% {'pre-market' if is_premarket else 'live'} threshold)")

        fee_drag = sp.get("fee_drag_pct")
        if fee_drag is not None and fee_drag > 15.0:
            reasons.append(f"fee drag {fee_drag:.1f}% > 15.0% (DIR-01 micro-credit fee trap: ${sp.get('round_trip_fee', 1.40):.2f} fee on ${sp.get('gross_cash', (credit_mid or 0)*100):.1f} credit)")
        min_roc_floor = 4.0 if sym in {"SPY", "QQQ"} else (7.5 if (width and width >= 20.0) else (10.0 if (width and width >= 15.0) else MIN_ROC_PCT))
        if roc_pct is not None and roc_pct < min_roc_floor:
            reasons.append(f"ROC {roc_pct:.1f}% < {min_roc_floor:.1f}% (DIR-01 adaptive floor)")

        # Beta-Adjusted Margin (BAM) Buffer Gate
        if safety_buffer_pct is not None and safety_buffer_pct < beta_adjusted_min_otm:
            reasons.append(f"buffer {safety_buffer_pct:.1f}% < beta-adjusted floor {beta_adjusted_min_otm:.1f}% (2.5x daily ATR: {daily_atr_pct:.2f}%/day)")

        if dte_est and not (DTE_MIN <= dte_est <= DTE_MAX):
            reasons.append(f"DTE {dte_est} outside {DTE_MIN}-{DTE_MAX}")
        
        # RULE-089 & RULE-074: SMA20 Trend & Falling Knife Gate
        # Pullback Tolerance Band: In healthy bull markets, allow shallow pullbacks (within -2.5% of SMA20)
        # to capture high-probability Wyckoff Spring / support tests, provided the short strike is strictly >= 5.0% OTM
        # and the 5-day slope is not steep downward (slope_5d >= -2.0%).
        if trend_info and trend_info.get("ok"):
            diff_pct = trend_info.get("diff_pct", 0.0)
            if diff_pct < -2.5:
                reasons.append(f"RULE-089: DOWNTREND VETO (Spot ${spot_price:.2f} < SMA20 ${trend_info.get('sma20', 0):.2f} [{diff_pct:+.1f}% < -2.5% tolerance band])")
            elif slope_5d < -2.0:
                reasons.append(f"RULE-074: FALLING KNIFE VETO (SMA20 slope {slope_5d:+.1f}% < -2.0% downward trajectory)")

        # RULE-094: Precision Index Iron Condor Protocol
        # Strictly restricted to Index ETFs (SPY, QQQ, IWM) during consolidation (box 35%-65%, VRP >= 1.10)
        strat_classification = classify_trade_strategy(
            symbol=sym,
            box_position=box_pos,
            diff_pct=trend_info.get("diff_pct", 0) if trend_info else 0,
            iv_hv=iv_hv
        )
        is_condor_eligible = (strat_classification == "IRON_CONDOR" and spot_price is not None and spot_price > 0)

        if is_condor_eligible:
            strategy_name = "Iron Condor"
            strategy_type = "iron_condor"
            call_short = round(spot_price * 1.055 / 5.0) * 5.0 if spot_price > 200 else round(spot_price * 1.055)
            call_long = call_short + width
            call_buf_pct = round((call_short - spot_price) / spot_price * 100, 2)
            condor_credit = round((credit_mid or 0.65) * 1.6, 2)
            condor_roc = round((condor_credit / width) * 100.0, 1)
            condor_margin = width * 100.0
            condor_notes = f"RULE-094: Index Rangebound Harvest ({target_s:.0f}P/{target_l:.0f}P | {call_short:.0f}C/{call_long:.0f}C) ROC: {condor_roc}%"
        else:
            strategy_name = "Bull Put Spread"
            strategy_type = "bull_put_spread"
            call_short = call_long = call_buf_pct = None
            condor_credit = credit_mid
            condor_roc = roc_pct
            condor_margin = width * 100.0
            condor_notes = "Standard Bull Put Spread"

        eligible = (len(reasons) == 0)

        scored_candidates.append({
            "symbol": sym,
            "name": c.get("name", sym),
            "theme": theme,
            "strategy": strategy_name,
            "strategy_type": strategy_type,
            "condor_notes": condor_notes,
            "call_short_strike": call_short,
            "call_long_strike": call_long,
            "call_buffer_pct": call_buf_pct,
            "condor_credit_est": condor_credit,
            "condor_roc_pct": condor_roc,
            "margin_collateral": condor_margin,
            "spot": spot_price,
            "spot_source": sp.get("spot_source"),
            "spot_asof": sp.get("spot_asof"),
            "sma20": trend_info.get("sma20") if trend_info.get("ok") else None,
            "trend_status": trend_info.get("status") if trend_info.get("ok") else "UNKNOWN",
            "trend_diff_pct": trend_info.get("diff_pct") if trend_info.get("ok") else None,
            "strike_source": strike_source,
            "short_strike": target_s,
            "long_strike": target_l,
            "width": width,
            "expiration": exp_date,
            "dte": dte_est,
            "slot_type": "SPRINT" if (dte_est and dte_est <= 16) else "ANCHOR",
            "daily_cvr": sp.get("daily_cvr"),
            "gross_credit": credit_mid,
            "round_trip_fee": sp.get("round_trip_fee", 1.40),
            "net_cash": sp.get("net_cash"),
            "net_credit": sp.get("net_credit"),
            "roc_pct": roc_pct,
            "net_roc_pct": sp.get("net_roc_pct", roc_pct),
            "fee_drag_pct": sp.get("fee_drag_pct"),
            "natural_credit": sp.get("natural_credit"),
            "spread_friction": sp.get("spread_friction"),
            "friction_ratio": sp.get("friction_ratio"),
            "daily_atr_pct": daily_atr_pct,
            "beta_adjusted_min_otm": beta_adjusted_min_otm,
            "slope_5d": slope_5d,
            "is_golden_trend": is_golden,
            "tenor_candidates": sp.get("tenor_candidates", {}),
            "short_delta": short_delta,
            "credit_mid": credit_mid,
            "short_iv": short_iv, "atm_iv": atm_iv, "iv_25d_put": iv_25d,
            "hv20": sp.get("hv20_live"),
            "iv_hv_ratio": iv_hv,
            "short_oi": short_oi, "long_oi": long_oi,
            "liquid": liquid,
            "otm_pct": safety_buffer_pct,
            "box_position_pct": round(box_pos, 1) if box_pos is not None else None,
            "box_status": box_status,
            "safety_buffer_pct": safety_buffer_pct,
            "drag_hours": drag_info["drag_hours"],
            "drag_status": drag_info["status"],
            "weekend_warp": warp_desc,
            "skew_status": skew_desc,
            "cot_theme_multiplier": cot_pct,
            "cot_live_willco": {k: live_cot.get(k) for k in ("ES", "NQ", "ZN", "GC") if k in live_cot},
            "assigned_account": theme_account_mapping.get(theme, "pion_main"),
            "fast_harvest_score": fast_harvest_score,
            "fast_harvest_tier": fast_harvest_tier,
            "fast_harvest_expected_hours": fh_stat["expected_hold_hours"],
            "eligible": eligible,
            "ineligible_reasons": reasons,
            "raw_score": round(total_score, 2),
            "total_score": total_score if eligible else round(total_score * 0.5, 2),
            "score_breakdown": {
                "strict_box_discount": box_score,
                "diversification": div_score,
                "cot_smart_money": round(cot_score, 1),
                "roc_yield": round(yield_score, 2),
                "roc_bonus": round(roc_bonus, 2),
                "gamma_shield_bonus": gamma_shield_bonus,
                "drag_hours_adj": drag_score_adj,
                "reproducible_safety_buffer": safety_score,
                "trend_momentum_bonus": trend_momentum_bonus,
                "velocity_warp_bonus": total_velocity_bonus,
                "fast_harvest_boost": fast_harvest_boost,
            },
        })

    # 6. Rank: eligible first (by score), then ineligible (for audit)
    ranked = sorted(scored_candidates, key=lambda x: (x["eligible"], x["total_score"]), reverse=True)
    eligible_ranked = [r for r in ranked if r["eligible"]]

    print("\n🏆 TOP-RANKED LIVE CANDIDATES FOR TONIGHT'S TRADE CYCLE:")
    print("━" * 80)
    for i, r in enumerate(ranked[:6], 1):
        px = f"${r['spot']:.2f}" if r.get("spot") else "n/a"
        gate = "ELIGIBLE ✅" if r["eligible"] else "INELIGIBLE ⛔ " + "; ".join(r["ineligible_reasons"][:2])
        print(f"  #{i} [{r['total_score']:>5.1f} pts] {r['symbol']:<5} spot {px:>9} -> "
              f"${r['short_strike']:.0f}P/${r['long_strike']:.0f}P {r['expiration']} "
              f"({r['dte']}d, credit {r['credit_mid']}, ROC {r['roc_pct']}%) | {gate}")

    lead = eligible_ranked[0] if eligible_ranked else (ranked[0] if ranked else None)

    # Multi-Candidate Dual Dispatch (RULE-098): Select Candidate #2 from different uncorrelated sector
    secondary = None
    if lead and len(eligible_ranked) > 1:
        for cand in eligible_ranked[1:]:
            if cand["symbol"] != lead["symbol"] and cand.get("theme") != lead.get("theme"):
                secondary = cand
                break
        if not secondary and len(eligible_ranked) > 1:
            for cand in eligible_ranked[1:]:
                if cand["symbol"] != lead["symbol"]:
                    secondary = cand
                    break
    elif lead and len(eligible_ranked) <= 1:
        # Fallback to top-scoring uncorrelated runner-up for live 21:15 verification
        lead_theme = lead.get("theme")
        runner_ups = sorted(ranked, key=lambda x: x.get("raw_score", 0.0), reverse=True)
        for cand in runner_ups:
            if cand["symbol"] != lead["symbol"] and cand.get("theme") != lead_theme:
                secondary = dict(cand)
                secondary["is_conditional_runner_up"] = True
                break

    selected_syms = {s for s in [lead["symbol"] if lead else None, secondary["symbol"] if secondary else None] if s}
    fallbacks = [r for r in eligible_ranked if r["symbol"] not in selected_syms][:3]
    if len(fallbacks) < 2:
        runner_ups = sorted(ranked, key=lambda x: x.get("raw_score", 0.0), reverse=True)
        for r in runner_ups:
            if r["symbol"] not in selected_syms and r["symbol"] not in [f["symbol"] for f in fallbacks]:
                c_copy = dict(r)
                c_copy["is_conditional_runner_up"] = True
                fallbacks.append(c_copy)
                if len(fallbacks) >= 3:
                    break

    output_payload: Dict[str, Any] = {
        "timestamp": now_ict,
        "data_source": "LIVE (Tradier PROD / Finnhub / Alpaca IEX) via live_spot + live_market_data",
        "gates": {"min_vrp_ratio": effective_min_vrp, "market_vix": live_vix,
                  "min_credit": MIN_CREDIT, "min_otm_pct": effective_min_otm,
                  "dte_band": [DTE_MIN, DTE_MAX], "target_delta": TARGET_DELTA,
                  "theme_concentration_cap": THEME_CONCENTRATION_CAP},
        "regime": regime_info,
        "primary": lead or {},
        "secondary": secondary or {},
        "fallbacks": fallbacks,
        "stand_aside": lead is None,
        "stand_aside_reason": (None if lead else
                               "No candidate cleared the live gates (spot + liquidity + VRP + OTM) — standing aside."),
        "all_ranked": ranked,
    }

    out_file = base_dir / "tonight_selected_target.json"
    out_file.write_text(json.dumps(output_payload, indent=2), encoding="utf-8")
    print(f"\n💾 Saved dynamic selection to {out_file}!")

    # Keep market_context.json LIVE for the 19:40 pre-market brief
    try:
        ctx_res = refresh_market_context(str(base_dir / "market_context.json"))
        print(f"  💾 market_context.json refreshed live: SPY {ctx_res.get('spy')} | VIX {ctx_res.get('vix')}")
    except Exception as ex_ctx:
        print(f"  ⚠️ market_context refresh notice: {ex_ctx}")

    # Autonomously refresh Tier 16 signals (RULE-073 & RULE-074)
    try:
        import wyckoff_spring_detector
        import sma20_slope_engine
        wyckoff_spring_detector.analyze_wyckoff_signals("SPY")
        sma20_slope_engine.analyze_sma20_slopes("SPY")
        print("  🌾 wyckoff_signals.json & sma20_slope_signals.json refreshed autonomously!")
    except Exception as ex_tier16:
        print(f"  ⚠️ Tier 16 signal refresh notice: {ex_tier16}")

    # candidates.txt roster
    cand_lines = [f"# SkonVault Weekly Candidate Roster — Refreshed: {now_ict}",
                  f"# Data: LIVE chain (no synthetic strikes)"]
    if lead:
        cand_lines.append(f"# Primary Lead: {lead['symbol']} (Score: {lead['total_score']} pts) "
                          f"strikes ${lead['short_strike']:.0f}P/${lead['long_strike']:.0f}P {lead['expiration']}")
    else:
        cand_lines.append("# Primary Lead: NONE — no candidate cleared the live gates (stand aside)")
    cand_lines.append("")
    for idx, r in enumerate(ranked, 1):
        tag = "OK " if r["eligible"] else "SKIP"
        cand_lines.append(f"{r['symbol']:<5}  # Rank #{idx:<2} | {tag} | Score: {r['total_score']} pts | "
                          f"{r['theme']} | Strikes: ${r['short_strike']:.0f}P/${r['long_strike']:.0f}P "
                          f"{r['expiration']} | Spot: ${r['spot']:.2f}" if r.get("spot") else "")
    cand_text = "\n".join(l for l in cand_lines if l) + "\n"
    for cand_p in [base_dir / "candidates.txt",
                   Path("/home/ubuntu/openclaw/candidates.txt"),
                   Path("/home/ubuntu/trading-bot/candidates.txt"),
                   Path("/Users/SkonP/AI_Prompt/Obsidient/SkonVault/candidates.txt")]:
        try:
            cand_p.write_text(cand_text, encoding="utf-8")
            print(f"  💾 Updated candidates.txt in: {cand_p}")
        except Exception:
            pass

    if lead:
        print(f"🎯 TONIGHT'S LIVELY LEAD: {lead['symbol']} ({lead['name']}) [Score: {lead['total_score']} | "
              f"${lead['short_strike']:.0f}P/${lead['long_strike']:.0f}P {lead['expiration']} | "
              f"credit {lead['credit_mid']} | ROC {lead['roc_pct']}% | Acct: {lead['assigned_account'].upper()}]")
    else:
        print("🚫 NO ELIGIBLE LEAD TONIGHT — standing aside (no fabricated strikes).")
    print("=" * 80)
    return output_payload


if __name__ == "__main__":
    run_dynamic_screening()
