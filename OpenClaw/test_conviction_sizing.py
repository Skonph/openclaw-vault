#!/usr/bin/env python3
"""
Local unit tests — conviction-weighted position sizing (Improvement #5,
OpenClaw side).

Verifies _calc_qty(spread_mid, conviction_score) in vault_updater.py:
  Tier 1 (conviction 75-84):  multiplier 1.0x -> base_risk in [$200,  $500]
  Tier 2 (conviction 85-94):  multiplier 1.5x -> base_risk in [$300,  $750]
  Tier 3 (conviction 95-100): multiplier 2.0x -> base_risk in [$400, $1000]

In this sandbox, the Alpaca /account request inside _calc_qty fails (no
network/credentials), so it deterministically falls back to base_risk=$200
before the conviction multiplier is applied. That makes the multiplier
behavior fully testable without mocking the HTTP call:
  tier 1 -> risk_amount = $200 * 1.0 = $200
  tier 2 -> risk_amount = $200 * 1.5 = $300
  tier 3 -> risk_amount = $200 * 2.0 = $400

Run:
  python3 test_conviction_sizing.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from vault_updater import _calc_qty

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


SPREAD_MID = 0.40  # cost_per_contract = $40

print('\n=== Tier 1 (conviction 75-84): 1.0x -> $200 base ===')
check('conviction=75 (floor) -> qty 5 ($200/$40)',
      _calc_qty(SPREAD_MID, conviction_score=75), 5)
check('conviction=84 (top of tier 1) -> qty 5',
      _calc_qty(SPREAD_MID, conviction_score=84), 5)
check('default conviction (75) -> qty 5',
      _calc_qty(SPREAD_MID), 5)

print('\n=== Tier 2 (conviction 85-94): 1.5x -> $300 base ===')
check('conviction=85 (bottom of tier 2) -> qty 7 ($300/$40)',
      _calc_qty(SPREAD_MID, conviction_score=85), 7)
check('conviction=94 (top of tier 2) -> qty 7',
      _calc_qty(SPREAD_MID, conviction_score=94), 7)

print('\n=== Tier 3 (conviction 95-100): 2.0x -> $400 base ===')
check('conviction=95 (bottom of tier 3) -> qty 10 ($400/$40)',
      _calc_qty(SPREAD_MID, conviction_score=95), 10)
check('conviction=100 (max) -> qty 10',
      _calc_qty(SPREAD_MID, conviction_score=100), 10)

print('\n=== Min 1 contract floor ===')
# Expensive spread: cost_per_contract=$500, tier-1 risk=$200 -> floor(200/500)=0 -> min 1
check('Expensive spread ($5.00 mid) at tier 1 -> qty floors at 1',
      _calc_qty(5.00, conviction_score=75), 1)
# Same expensive spread at tier 3 ($400/$500=0) -> still floors at 1
check('Expensive spread ($5.00 mid) at tier 3 -> qty still floors at 1',
      _calc_qty(5.00, conviction_score=100), 1)

print('\n=== Tier boundaries are monotonically non-decreasing ===')
q75  = _calc_qty(SPREAD_MID, conviction_score=75)
q85  = _calc_qty(SPREAD_MID, conviction_score=85)
q95  = _calc_qty(SPREAD_MID, conviction_score=95)
check('qty(75) < qty(85) < qty(95) -> True', q75 < q85 < q95, True)


print(f'\n{"="*50}')
print(f'  {PASS} passed, {FAIL} failed')
print(f'{"="*50}\n')

sys.exit(1 if FAIL else 0)
