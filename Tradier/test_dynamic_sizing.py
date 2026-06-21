#!/usr/bin/env python3
"""
Local unit tests — dynamic contract sizing thresholds (Improvement #4 +
Improvement #5 follow-ups).

Improvement #4 (2026-06-13): fixed the dead `score > 0.30` / `combined_score
> 0.30` thresholds -> DYNAMIC_SIZING_SCORE_THRESHOLD = 0.010 (single-leg),
DYNAMIC_SIZING_SCORE_THRESHOLD_IC = 0.020 (IC). These give qty=2.

Improvement #5 (2026-06-13): added a tier-3 "high conviction" rule on top —
qty=3 when score clears an even higher bar (0.018 single-leg / 0.032 IC) AND
3*max_loss <= MAX_RISK_TIER3 (150, vs. the standard MAX_RISK=100 for tier 2).

No network calls, no Tradier credentials required (constants only).

Run:
  python3 test_dynamic_sizing.py
"""

import sys
sys.argv = [sys.argv[0], "--test"]  # avoid _check_credentials() exit(1) at import time

import daily_scan as ds

PASS = 0
FAIL = 0


def check(label, actual, expected):
    global PASS, FAIL
    ok = actual == expected
    icon = '✅' if ok else '❌'
    print(f'  {icon} {label}: got {actual!r}, expected {expected!r}')
    if ok:
        PASS += 1
    else:
        FAIL += 1


def sizing_decision(vix, score, single_contract_risk, threshold2, threshold3=None):
    """Mirrors the qty=1/2/3 conditional in construct_*_spread()."""
    if (threshold3 is not None and vix > 20 and score > threshold3
            and (3 * single_contract_risk <= ds.MAX_RISK_TIER3)):
        return 3
    if vix > 20 and score > threshold2 and (2 * single_contract_risk <= ds.MAX_RISK):
        return 2
    return 1


print('\n=== Constants ===')
check('Single-leg tier-2 threshold (Improvement #4)', ds.DYNAMIC_SIZING_SCORE_THRESHOLD, 0.010)
check('IC tier-2 threshold (Improvement #4)', ds.DYNAMIC_SIZING_SCORE_THRESHOLD_IC, 0.020)
check('Single-leg tier-3 threshold (Improvement #5)', ds.DYNAMIC_SIZING_SCORE_THRESHOLD_TIER3, 0.018)
check('IC tier-3 threshold (Improvement #5)', ds.DYNAMIC_SIZING_SCORE_THRESHOLD_IC_TIER3, 0.032)
check('MAX_RISK ($16k 2%-per-trade scaling, 2026-06-20)', ds.MAX_RISK, 320)
check('MAX_RISK_TIER3 ($16k scaling = 1.5x MAX_RISK)', ds.MAX_RISK_TIER3, 480)


print('\n=== Old threshold (0.30) never fired — regression check ===')
# Typical backtest trades: $1-wide spread, net_credit ~0.35, max_loss ~65
typical_score = round(0.35 / 65.04, 4)
check('Typical score under old 0.30 threshold -> qty 1',
      sizing_decision(vix=25, score=typical_score, single_contract_risk=65.04, threshold2=0.30), 1)


print('\n=== Tier-2 single-leg threshold (0.010) — Improvement #4 ===')
# Typical case: score ~0.0046-0.0058, max_loss ~65 -> still qty 1 (below threshold
# AND fails the 2x max_loss <= MAX_RISK check)
check('Typical $1-wide spread (score~0.0054, max_loss~65) -> qty 1',
      sizing_decision(vix=25, score=typical_score, single_contract_risk=65.04,
                       threshold2=ds.DYNAMIC_SIZING_SCORE_THRESHOLD,
                       threshold3=ds.DYNAMIC_SIZING_SCORE_THRESHOLD_TIER3), 1)

# High-credit day: net_credit=0.55 on $1-wide -> max_loss=45, score=0.55/45=0.0122
high_credit_score = round(0.55 / 45.0, 4)
check('High-credit $1-wide spread (score~0.0122, max_loss=45, VIX>20) -> qty 2',
      sizing_decision(vix=25, score=high_credit_score, single_contract_risk=45.0,
                       threshold2=ds.DYNAMIC_SIZING_SCORE_THRESHOLD,
                       threshold3=ds.DYNAMIC_SIZING_SCORE_THRESHOLD_TIER3), 2)

# Same high-credit score but VIX <= 20 -> still qty 1 (VIX gate)
check('High-credit spread but VIX<=20 -> qty 1 (VIX gate enforced)',
      sizing_decision(vix=18, score=high_credit_score, single_contract_risk=45.0,
                       threshold2=ds.DYNAMIC_SIZING_SCORE_THRESHOLD,
                       threshold3=ds.DYNAMIC_SIZING_SCORE_THRESHOLD_TIER3), 1)

# Score clears tier-2 threshold but max_loss too high for 2x (2*170=340 > MAX_RISK 320) -> risk gate enforced
check('Score clears 0.010 but 2*max_loss > MAX_RISK -> qty 1 (risk gate enforced)',
      sizing_decision(vix=25, score=0.015, single_contract_risk=170.0,
                       threshold2=ds.DYNAMIC_SIZING_SCORE_THRESHOLD,
                       threshold3=ds.DYNAMIC_SIZING_SCORE_THRESHOLD_TIER3), 1)


print('\n=== Tier-3 single-leg "high conviction" threshold (0.018) — Improvement #5 ===')
# Exceptional-credit day: net_credit=0.65 on $1-wide -> max_loss=35, score=0.65/35=0.0186
tier3_score = round(0.65 / 35.0, 4)
check('Exceptional-credit spread (score~0.0186, max_loss=35, VIX>20) -> qty 3',
      sizing_decision(vix=25, score=tier3_score, single_contract_risk=35.0,
                       threshold2=ds.DYNAMIC_SIZING_SCORE_THRESHOLD,
                       threshold3=ds.DYNAMIC_SIZING_SCORE_THRESHOLD_TIER3), 3)

# Same score but VIX <= 20 -> falls all the way to qty 1 (VIX gate blocks both tiers)
check('Exceptional-credit spread but VIX<=20 -> qty 1 (VIX gate enforced)',
      sizing_decision(vix=18, score=tier3_score, single_contract_risk=35.0,
                       threshold2=ds.DYNAMIC_SIZING_SCORE_THRESHOLD,
                       threshold3=ds.DYNAMIC_SIZING_SCORE_THRESHOLD_TIER3), 1)

# Score clears tier-3 bar but max_loss too high for 3x (3*170=510 > MAX_RISK_TIER3 480)
# AND for 2x (2*170=340 > MAX_RISK 320) -> falls back to qty 1 (both risk gates enforced)
check('Score clears 0.018 but 3*max_loss > MAX_RISK_TIER3 (and 2x > MAX_RISK) -> qty 1',
      sizing_decision(vix=25, score=0.02, single_contract_risk=170.0,
                       threshold2=ds.DYNAMIC_SIZING_SCORE_THRESHOLD,
                       threshold3=ds.DYNAMIC_SIZING_SCORE_THRESHOLD_TIER3), 1)

# High-credit (tier-2) score from above does NOT clear tier-3 -> stays qty 2
check('High-credit score (~0.0122) below tier-3 bar -> stays qty 2',
      sizing_decision(vix=25, score=high_credit_score, single_contract_risk=45.0,
                       threshold2=ds.DYNAMIC_SIZING_SCORE_THRESHOLD,
                       threshold3=ds.DYNAMIC_SIZING_SCORE_THRESHOLD_TIER3), 2)


print('\n=== Tier-2 iron condor threshold (0.020) — Improvement #4 ===')
# Typical IC from backtest: put score~0.00576 + call score~0.00458 = 0.01034
ic_typical = round(0.00576 + 0.00458, 4)
check('Typical IC combined score (~0.0103) -> qty 1',
      sizing_decision(vix=25, score=ic_typical, single_contract_risk=68.57,
                       threshold2=ds.DYNAMIC_SIZING_SCORE_THRESHOLD_IC,
                       threshold3=ds.DYNAMIC_SIZING_SCORE_THRESHOLD_IC_TIER3), 1)

# High-credit IC day: both legs at ~0.0122 -> combined ~0.0244, max_loss=45
ic_high = round(0.0122 + 0.0122, 4)
check('High-credit IC combined score (~0.0244, max_loss=45, VIX>20) -> qty 2',
      sizing_decision(vix=25, score=ic_high, single_contract_risk=45.0,
                       threshold2=ds.DYNAMIC_SIZING_SCORE_THRESHOLD_IC,
                       threshold3=ds.DYNAMIC_SIZING_SCORE_THRESHOLD_IC_TIER3), 2)


print('\n=== Tier-3 iron condor "high conviction" threshold (0.032) — Improvement #5 ===')
# Exceptional IC day: both legs at ~0.0186 -> combined ~0.0372, max_loss=35
ic_tier3 = round(tier3_score + tier3_score, 4)
check('Exceptional IC combined score (~0.0372, max_loss=35, VIX>20) -> qty 3',
      sizing_decision(vix=25, score=ic_tier3, single_contract_risk=35.0,
                       threshold2=ds.DYNAMIC_SIZING_SCORE_THRESHOLD_IC,
                       threshold3=ds.DYNAMIC_SIZING_SCORE_THRESHOLD_IC_TIER3), 3)

# High-credit IC (tier-2) combined score (~0.0244) does NOT clear tier-3 (0.032) -> stays qty 2
check('High-credit IC score (~0.0244) below tier-3 bar -> stays qty 2',
      sizing_decision(vix=25, score=ic_high, single_contract_risk=45.0,
                       threshold2=ds.DYNAMIC_SIZING_SCORE_THRESHOLD_IC,
                       threshold3=ds.DYNAMIC_SIZING_SCORE_THRESHOLD_IC_TIER3), 2)


print(f'\n{"="*50}')
print(f'  {PASS} passed, {FAIL} failed')
print(f'{"="*50}\n')

sys.exit(1 if FAIL else 0)
