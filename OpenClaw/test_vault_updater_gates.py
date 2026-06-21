#!/usr/bin/env python3
"""
Local diagnostic for vault_updater._check_gates().

Verifies that an 'approved' order (Hermes-cleared events) still cannot
bypass the cooling-off (Gate 1) or conviction (Gate 3) safety checks —
only Gate 2 (events_status) is bypassable via explicit approval.

Run: python3 test_vault_updater_gates.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from vault_updater import _check_gates

TODAY = '2026-06-13'


def make_order(**overrides):
    base = dict(
        trade_id='TEST0001',
        status='pending',
        symbol='XYZ',
        events_status='clear',
        conviction_pass=True,
        conviction_score=90,
    )
    base.update(overrides)
    return base


def run():
    passed = 0
    total = 0

    def check(name, condition):
        nonlocal passed, total
        total += 1
        status = '✅ PASS' if condition else '❌ FAIL'
        print(f'{status}  {name}')
        if condition:
            passed += 1

    # 1. Pending order, all gates clear -> executes
    reason, notify = _check_gates(make_order(), {}, TODAY)
    check('Pending order, all gates clear -> no skip', reason is None)

    # 2. Pending order, events uncertain -> skipped + notify (Gate 2)
    reason, notify = _check_gates(make_order(events_status='uncertain'), {}, TODAY)
    check('Pending + events uncertain -> skipped with notify',
          reason is not None and 'Events' in reason and notify is True)

    # 3. Approved order, events still uncertain -> Gate 2 bypassed
    reason, notify = _check_gates(
        make_order(status='approved', events_status='uncertain'), {}, TODAY)
    check('Approved order bypasses Gate 2 (events)', reason is None)

    # 4. Approved order, conviction_pass False -> Gate 3 NOT bypassed
    reason, notify = _check_gates(
        make_order(status='approved', conviction_pass=False, conviction_score=40),
        {}, TODAY)
    check('Approved order still blocked by Gate 3 (conviction)',
          reason is not None and 'Conviction' in reason)

    # 5. Approved order, symbol in active cooling-off -> Gate 1 NOT bypassed
    reason, notify = _check_gates(
        make_order(status='approved'), {'XYZ': '2026-06-20'}, TODAY)
    check('Approved order still blocked by Gate 1 (cooling-off)',
          reason is not None and 'Cooling-off' in reason)

    # 6. Pending order, cooling-off expired (date < today) -> passes Gate 1
    reason, notify = _check_gates(
        make_order(), {'XYZ': '2026-06-01'}, TODAY)
    check('Expired cooling-off date does not block', reason is None)

    print(f'\n{passed}/{total} checks passed')
    return passed == total


if __name__ == '__main__':
    ok = run()
    sys.exit(0 if ok else 1)
