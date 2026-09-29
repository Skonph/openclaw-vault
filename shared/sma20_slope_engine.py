#!/usr/bin/env python3
"""
sma20_slope_engine.py
---------------------
Quantitative 20 SMA Slope Health & 50 SMA Macro Trend Gate Engine (RULE-074 & RULE-075 / The 3R Rule).

Calculates:
1. 5-day slope angle of 20 SMA & ATR distance (RULE-074):
   - OVEREXTENDED_PARABOLIC (Angle > 55° or Dist > 3.0x ATR): Vetoes Bull Put Spreads.
   - SUSTAINABLE_TREND (25° <= Angle <= 50°): +15 Conviction Bonus for 20 SMA Retracement.
   - BASE_CONSOLIDATION (-15° <= Angle <= 15°): Base Breakout requirement.

2. 50 SMA Macro Trend Alignment (RULE-075 / The 3R Rule):
   - Price > SMA50 & SMA20 > SMA50: GOLDEN_TREND_ALIGNMENT (+10 Conviction Bonus).
   - Price < SMA50: MACRO_CAUTION_GATE (Cap BP utilization at 35%, require Wyckoff Spring).

Outputs: shared/sma20_slope_signals.json
"""

import os
import sys
import json
import math
import datetime
from pathlib import Path

# Paths
SHARED_DIR = Path("/home/ubuntu/shared") if Path("/home/ubuntu/shared").exists() else Path(__file__).resolve().parent
MARKET_CONTEXT_PATH = SHARED_DIR / "market_context.json"
OUTPUT_PATH = SHARED_DIR / "sma20_slope_signals.json"

def calculate_slope_angle(sma20_current: float, sma20_5d_ago: float) -> float:
    """Calculate 5-day SMA20 slope angle in degrees."""
    if sma20_5d_ago <= 0:
        return 0.0
    pct_change = (sma20_current - sma20_5d_ago) / sma20_5d_ago * 100
    radians = math.atan(pct_change / 5.0)
    degrees = math.degrees(radians)
    return round(degrees, 2)

def evaluate_macro_trend_gate(price: float, sma20: float, sma50: float) -> dict:
    """
    Evaluates R1: Primary Trend Gate using the 50 SMA.
    """
    above_sma50 = price > sma50
    sma20_above_sma50 = sma20 > sma50
    golden_alignment = above_sma50 and sma20_above_sma50

    if golden_alignment:
        status = "GOLDEN_TREND_ALIGNMENT"
        bonus = 10
        bp_cap_pct = 65.0
        rationale = "Price & 20 SMA sit firmly above 50 SMA. Macro trend is 100% bullish (+10 pts bonus)."
    elif above_sma50:
        status = "MODERATE_BULLISH_TREND"
        bonus = 5
        bp_cap_pct = 50.0
        rationale = "Price is above 50 SMA but 20 SMA lagging (+5 pts bonus)."
    else:
        status = "MACRO_CAUTION_GATE"
        bonus = -15
        bp_cap_pct = 35.0
        rationale = "Price sits below 50 SMA. Macro trend caution active; BP utilization capped at 35% & Wyckoff Spring required."

    return {
        "status": status,
        "price_above_sma50": above_sma50,
        "sma20_above_sma50": sma20_above_sma50,
        "golden_alignment": golden_alignment,
        "conviction_bonus": bonus,
        "max_bp_utilization_pct": bp_cap_pct,
        "rationale": rationale
    }

def evaluate_trend_health(price: float, sma20: float, slope_angle: float, atr: float = 3.50) -> dict:
    """Evaluates trend health and overextension risk (RULE-074)."""
    dist_to_sma20 = price - sma20
    dist_atr_mult = round(dist_to_sma20 / atr, 2) if atr > 0 else 0.0

    is_overextended = slope_angle > 55.0 or dist_atr_mult > 3.0
    is_sustainable = 25.0 <= slope_angle <= 50.0 and dist_atr_mult <= 2.5
    is_base = -15.0 <= slope_angle <= 15.0

    if is_overextended:
        status = "OVEREXTENDED_PARABOLIC"
        recommendation = "VETO_BULL_PUT_SPREAD"
        conviction_adjustment = -30
        rationale = f"20 SMA slope is parabolic ({slope_angle}°) or price is overextended ({dist_atr_mult}x ATR). High risk of sharp pullback."
    elif is_sustainable:
        status = "SUSTAINABLE_TREND"
        recommendation = "20_SMA_RETRACEMENT_BUY"
        conviction_adjustment = 15
        rationale = f"20 SMA slope is healthy ({slope_angle}°). Pullbacks to 20 SMA offer optimal 1.5-day credit spread entries."
    elif is_base:
        status = "BASE_CONSOLIDATION"
        recommendation = "BASE_BREAKOUT_WAIT"
        conviction_adjustment = 0
        rationale = f"20 SMA is flat ({slope_angle}°). Price is forming a base; awaiting volume-contracted breakout."
    else:
        status = "NEUTRAL"
        recommendation = "STANDARD_MONITOR"
        conviction_adjustment = 0
        rationale = f"20 SMA slope is neutral ({slope_angle}°)."

    return {
        "status": status,
        "slope_angle_deg": slope_angle,
        "dist_to_sma20": round(dist_to_sma20, 2),
        "dist_atr_multiples": dist_atr_mult,
        "is_overextended": is_overextended,
        "recommendation": recommendation,
        "conviction_adjustment": conviction_adjustment,
        "rationale": rationale
    }

def analyze_sma20_slopes(symbol: str = "SPY") -> dict:
    """Main pipeline to analyze 20 SMA slope health & 50 SMA macro trend."""
    price = 542.50
    sma20 = 538.00
    sma50 = 530.00
    sma20_5d_ago = 534.50
    atr = 3.50

    # 1. Primary: Direct live bars calculation for high-fidelity OHLCV & slope trajectory
    try:
        sys.path.insert(0, str(SHARED_DIR))
        from live_market_data import daily_bars, get_spot
        bars = daily_bars(symbol, 60)
        if len(bars) >= 50:
            closes = [float(b["close"]) for b in bars]
            sma20 = round(sum(closes[-20:]) / 20.0, 2)
            sma50 = round(sum(closes[-50:]) / 50.0, 2)
            sma20_5d_ago = round(sum(closes[-25:-5]) / 20.0, 2) if len(closes) >= 25 else sma20
            
            trs = []
            for i in range(1, len(bars)):
                h, l, c_prev = float(bars[i]["high"]), float(bars[i]["low"]), float(bars[i-1]["close"])
                trs.append(max(h - l, abs(h - c_prev), abs(l - c_prev)))
            atr = round(sum(trs[-14:]) / 14.0, 2) if len(trs) >= 14 else atr

            spot = get_spot(symbol)
            price = float(spot.get("price") or closes[-1]) if isinstance(spot, dict) else float(spot or closes[-1])
    except Exception as e_bars:
        pass

    # 2. Secondary fallback: market_context.json quotes schema
    if MARKET_CONTEXT_PATH.exists():
        try:
            ctx = json.loads(MARKET_CONTEXT_PATH.read_text())
            quotes = ctx.get("quotes", {}).get(symbol, {})
            if quotes:
                price = float(quotes.get("last") or price)
                sma20 = float(quotes.get("sma20") or sma20)
                sma50 = float(quotes.get("sma50") or sma50)
                if sma20_5d_ago == 534.50:
                    sma20_5d_ago = round(sma20 * 0.993, 2)
        except Exception as e:
            print(f"[WARN] Error reading market_context.json: {e}")

    slope_angle = calculate_slope_angle(sma20, sma20_5d_ago)
    health = evaluate_trend_health(price, sma20, slope_angle, atr)
    macro_gate = evaluate_macro_trend_gate(price, sma20, sma50)

    total_conviction_bonus = health["conviction_adjustment"] + macro_gate["conviction_bonus"]

    output = {
        "analyzed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "symbol": symbol,
        "price": price,
        "sma20": sma20,
        "sma50": sma50,
        "trend_health": health,
        "macro_trend_gate_r1": macro_gate,
        "total_conviction_bonus": total_conviction_bonus
    }

    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2))
    print(f"[SMASlopeEngine] Saved output to {OUTPUT_PATH}")

    return output

if __name__ == "__main__":
    res = analyze_sma20_slopes()
    th = res["trend_health"]
    mg = res["macro_trend_gate_r1"]
    print("=" * 60)
    print(f"📈 20/50 SMA ENGINE & 3R MACRO GATE SUMMARY — Symbol: {res['symbol']}")
    print(f"   20 SMA Health Status: {th['status']} (Angle: {th['slope_angle_deg']}°, Dist: {th['dist_atr_multiples']}x ATR)")
    print(f"   50 SMA Macro Gate R1: {mg['status']} (+{mg['conviction_bonus']} pts | Max BP: {mg['max_bp_utilization_pct']}%)")
    print(f"   Net Conviction Bonus: +{res['total_conviction_bonus']} pts")
    print(f"   Rationale: {th['rationale']} | {mg['rationale']}")
    print("=" * 60)
