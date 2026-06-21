#!/usr/bin/env python3
"""
Local unit tests — Alpaca mleg limit_price sign convention (Improvement #3).

Covers the fix to:
  - vault_updater.py   _build_ic_payload()      (IC open, net credit)
  - nova_executor.py   _build_payload()         (IC open, net credit; and
                                                   debit-spread open, unchanged)
  - position_monitor.py _close_limit_price()    (IC close / debit-spread close)

Pure functions only — no network calls, no Alpaca credentials required.

Run:
  python3 test_mleg_pricing.py
"""

import sys

import vault_updater as vu
import nova_executor as ne
import position_monitor as pm


PASS = 0
FAIL = 0


def check(label: str, actual, expected):
    global PASS, FAIL
    ok = actual == expected
    icon = '✅' if ok else '❌'
    print(f'  {icon} {label}: got {actual!r}, expected {expected!r}')
    if ok:
        PASS += 1
    else:
        FAIL += 1


# ── Shared sample IC order ─────────────────────────────────────────────────
IC_ORDER = {
    'symbol':            'TOST',
    'expiry':            '2026-07-17',
    'spread_type':       'iron_condor',
    'put_long_strike':   20,
    'put_short_strike':  21,
    'call_short_strike': 27,
    'call_long_strike':  29,
    'spread_mid':        0.567,   # net credit at scan time
}

DEBIT_ORDER = {
    'symbol':       'NCLH',
    'expiry':       '2026-07-17',
    'spread_type':  'bull_call',
    'long_strike':  17,
    'short_strike': 18,
    'spread_mid':   0.40,         # net debit at scan time
}


print('\n=== vault_updater._build_ic_payload ===')
payload = vu._build_ic_payload(IC_ORDER, qty=1)
limit_px = float(payload['limit_price'])
expected_floor = round(max(IC_ORDER['spread_mid'] * 0.95, 0.01), 2)
check('IC limit_price is negative (net credit)', limit_px < 0, True)
check('IC limit_price magnitude == 95% of net_credit', round(-limit_px, 2), expected_floor)
check('IC legs all position_effect=open', all(l['position_effect'] == 'open' for l in payload['legs']), True)


print('\n=== nova_executor._build_payload (IC) ===')
payload = ne._build_payload(IC_ORDER, qty=1)
limit_px = float(payload['limit_price'])
check('IC limit_price is negative (net credit)', limit_px < 0, True)
check('IC limit_price magnitude == 95% of net_credit', round(-limit_px, 2), expected_floor)


print('\n=== nova_executor._build_payload (debit spread, unchanged) ===')
payload = ne._build_payload(DEBIT_ORDER, qty=1)
limit_px = float(payload['limit_price'])
expected_debit = round(DEBIT_ORDER['spread_mid'] * 1.08, 2)
check('Debit spread limit_price is positive (net debit)', limit_px > 0, True)
check('Debit spread limit_price == spread_mid * 1.08', limit_px, expected_debit)


print('\n=== position_monitor._close_limit_price ===')

# Closing an Iron Condor: legs we'd buy back (shorts) cost more than legs
# we'd sell (longs) -> net_credit on close is NEGATIVE (we pay to close).
# Mirrors the live TOST IC: longs total 0.81, shorts total 1.27 ->
# net_credit = 0.81 - 1.27 = -0.46  (we'd pay ~$0.46 to close)
ic_close_net_credit = 0.81 - 1.27
limit_px = pm._close_limit_price(ic_close_net_credit)
check('IC close: limit_price is positive (net debit to pay)', limit_px > 0, True)
check('IC close: limit_price == |net_credit| * 1.05', limit_px, round(0.46 * 1.05, 2))

# Closing a profitable debit spread: selling the long leg for more than
# buying back the short leg -> net_credit on close is POSITIVE (we receive).
debit_close_net_credit = 0.65 - 0.15  # = 0.50
limit_px = pm._close_limit_price(debit_close_net_credit)
check('Debit-spread close: limit_price is negative (net credit received)', limit_px < 0, True)
check('Debit-spread close: limit_price == -(net_credit * 0.95)', limit_px, round(-0.50 * 0.95, 2))

# Edge case: net_credit exactly zero on close (breakeven) -> tiny debit limit
limit_px = pm._close_limit_price(0.0)
check('Zero net_credit close: limit_price == 0.01 (min debit)', limit_px, 0.01)


print(f'\n{"="*50}')
print(f'  {PASS} passed, {FAIL} failed')
print(f'{"="*50}\n')

sys.exit(1 if FAIL else 0)
