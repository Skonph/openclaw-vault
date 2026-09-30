#!/usr/bin/env python3
"""
dynamic_regime_manager.py — Dynamic Regime-Adaptive Tuning (Market-Aware Rule)

Institutional Architecture:
1. Classifies the live market regime dynamically based on real-time VIX:
   - CALM_BULLISH (VIX < 18.0): Max velocity, 65% capital ceiling ($20,891), 60% Sprint (10-14 DTE) / 40% Anchor (21-28 DTE).
   - ELEVATED_CHURN (18.0 <= VIX < 24.0): Balanced safety, 55% capital ceiling ($17,677), 50% Sprint / 50% Anchor.
   - HIGH_VOL_STORM (VIX >= 24.0): Storm bunker, 45% capital ceiling ($14,463), 30% Sprint / 70% Anchor, Index Condor focus.

2. Provides unified regime parameters for:
   - Screener: min OTM buffer, DTE preferences, strategy routing.
   - Sizing: max portfolio margin ceiling vs permanent cash defense floor.
   - Multi-Slot Manager: target Sprint vs Anchor slot count.
"""

import sys
import os
import json
import datetime
from pathlib import Path
from typing import Dict, Any, Optional

sys.path.insert(0, str(Path(__file__).parent))
try:
    from live_spot import get_spot
except ImportError:
    get_spot = None


def get_live_vix(base_dir: Optional[Path] = None) -> float:
    """
    Fetches real-time VIX with fallback to market_context.json.
    """
    if base_dir is None:
        base_dir = Path("/home/ubuntu/shared")
        if not base_dir.exists():
            base_dir = Path(__file__).parent

    # Priority 1: Real-time spot via Tradier / live_spot
    if get_spot:
        try:
            v_res = get_spot("VIX")
            if isinstance(v_res, dict) and v_res.get("price"):
                return float(v_res["price"])
        except Exception:
            pass

    # Priority 2: Ingest from market_context.json
    mctx_file = base_dir / "market_context.json"
    if mctx_file.exists():
        try:
            m_data = json.loads(mctx_file.read_text(encoding="utf-8"))
            vix = m_data.get("vix") or (m_data.get("quotes", {}).get("VIX", {}).get("last"))
            if vix:
                return float(vix)
        except Exception:
            pass

    # Safe default: historical median VIX
    return 15.5


def evaluate_market_regime(override_vix: Optional[float] = None) -> Dict[str, Any]:
    """
    Evaluates current market volatility regime and returns optimal portfolio parameters.
    """
    vix = override_vix if override_vix is not None else get_live_vix()

    if vix < 22.0:
        regime_id = "CALM_BULLISH"
        regime_name = "Calm Bullish / Low Volatility (Velocity Sprint)"
        margin_ceiling_pct = 0.70       # 70% Elastic Margin Ceiling ($22,681 on $32,402 base)
        cash_defense_floor_pct = 0.30   # 30% Permanent Liquid Cash Defense ($9,720)
        sprint_ratio = 0.65             # 65% Sprint allocation
        anchor_ratio = 0.35             # 35% Core Anchor allocation
        target_sprint_slots = 4         # 4 Sprint slots
        target_anchor_slots = 3         # 3 Anchor slots
        min_otm_buffer_pct = 5.0        # >= 5.0% OTM buffer (92% win rate floor)
        preferred_strategy = "BULL_PUT_SPREAD"
        max_slot_risk_alpaca = 6500.0   # $6,500 per slot (supports 2C on $30w mega-caps)
        max_slot_risk_tradier = 600.0   # $600 per slot
        description = (
            "Calm trending market favoring maximum money velocity and short-cycle compounding. "
            "70% elastic margin ceiling enables friction-free 2C $30w spreads while preserving 30% cash defense."
        )
    elif vix < 26.0:
        regime_id = "ELEVATED_CHURN"
        regime_name = "Elevated Volatility / Market Churn"
        margin_ceiling_pct = 0.55       # 55% Margin Ceiling ($17,821)
        cash_defense_floor_pct = 0.45   # 45% Permanent Liquid Cash Defense ($14,581)
        sprint_ratio = 0.50             # 50% Sprint allocation
        anchor_ratio = 0.50             # 50% Core Anchor allocation
        target_sprint_slots = 3         # 3 Sprint slots
        target_anchor_slots = 3         # 3 Anchor slots
        min_otm_buffer_pct = 6.0        # Widened to 6.0% OTM buffer for elevated ATR
        preferred_strategy = "BALANCED_HYBRID"
        max_slot_risk_alpaca = 4500.0
        max_slot_risk_tradier = 500.0
        description = (
            "Heightened market swings. Widened OTM safety buffers and balanced 50/50 "
            "Sprint/Anchor posture to mitigate gap risk and preserve dry powder."
        )
    else:  # vix >= 26.0
        regime_id = "HIGH_VOL_STORM"
        regime_name = "Storm Mode / High Volatility Panic"
        margin_ceiling_pct = 0.45       # 45% Margin Ceiling ($14,463)
        cash_defense_floor_pct = 0.55   # 55% Permanent Liquid Cash Defense ($17,677)
        sprint_ratio = 0.30             # 30% Sprint allocation
        anchor_ratio = 0.70             # 70% Anchor / Index Condor allocation
        target_sprint_slots = 2         # 2 Sprint slots
        target_anchor_slots = 4         # 4 Anchor slots
        min_otm_buffer_pct = 8.0        # Ultra-deep 8.0% OTM buffer
        preferred_strategy = "IRON_CONDOR_INDEX_BALLAST"
        max_slot_risk_alpaca = 1800.0
        max_slot_risk_tradier = 400.0
        description = (
            "Market storm conditions. Cash defense elevated to 55%. Deploys deep OTM "
            "Index Iron Condors (SPY/QQQ/IWM) and high-duration Anchor spreads (28-45 DTE) "
            "to absorb wide swings without early stopouts."
        )

    return {
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT"),
        "vix": round(vix, 2),
        "regime_id": regime_id,
        "regime_name": regime_name,
        "margin_ceiling_pct": margin_ceiling_pct,
        "cash_defense_floor_pct": cash_defense_floor_pct,
        "sprint_ratio": sprint_ratio,
        "anchor_ratio": anchor_ratio,
        "target_sprint_slots": target_sprint_slots,
        "target_anchor_slots": target_anchor_slots,
        "total_active_slots": target_sprint_slots + target_anchor_slots,
        "min_otm_buffer_pct": min_otm_buffer_pct,
        "preferred_strategy": preferred_strategy,
        "max_slot_risk_alpaca": max_slot_risk_alpaca,
        "max_slot_risk_tradier": max_slot_risk_tradier,
        "description": description
    }


def classify_candidate_slot_type(candidate: Dict[str, Any]) -> str:
    """
    Classifies an option candidate into 'SPRINT' (10-16 DTE) or 'ANCHOR' (21-45 DTE).
    """
    dte = int(candidate.get("dte", 21) or 21)
    if dte <= 16:
        return "SPRINT"
    return "ANCHOR"


if __name__ == "__main__":
    regime = evaluate_market_regime()
    print("=" * 80)
    print("🧠 DYNAMIC REGIME-ADAPTIVE TUNING EVALUATION")
    print("=" * 80)
    print(f"• Live VIX Level           : {regime['vix']}")
    print(f"• Detected Market Regime   : {regime['regime_name']} ({regime['regime_id']})")
    print(f"• Capital Margin Ceiling   : {regime['margin_ceiling_pct']*100:.0f}% (${32140 * regime['margin_ceiling_pct']:,.2f})")
    print(f"• Cash Defense Floor       : {regime['cash_defense_floor_pct']*100:.0f}% (${32140 * regime['cash_defense_floor_pct']:,.2f})")
    print(f"• Barbell Allocation Ratio : {regime['sprint_ratio']*100:.0f}% Sprint / {regime['anchor_ratio']*100:.0f}% Anchor")
    print(f"• Target Slot Breakdown    : {regime['target_sprint_slots']} Sprint Slots | {regime['target_anchor_slots']} Anchor Slots (Total: {regime['total_active_slots']})")
    print(f"• Minimum OTM Safety Buffer: {regime['min_otm_buffer_pct']:.1f}%")
    print(f"• Primary Strategy Focus   : {regime['preferred_strategy']}")
    print(f"• Strategy Rationale       : {regime['description']}")
    print("=" * 80)
