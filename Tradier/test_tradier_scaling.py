#!/usr/bin/env python3
"""
Verifies the $16k / 2%-per-trade scaling of the Tradier risk parameters
(2026-06-20, graduation: primary real account scaled to $16k).
"""
import sys
sys.argv = ["daily_scan.py", "--test"]   # avoid module-load credential exit
import daily_scan as ds   # noqa: E402

REAL_ACCOUNT = 16000.0
PASS = FAIL = 0


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✅ {msg}")
    else:
        FAIL += 1; print(f"  ❌ {msg}")


def test_per_trade_risk_is_2pct():
    check(ds.MAX_RISK == 320, f"MAX_RISK == $320 (got {ds.MAX_RISK})")
    check(abs(ds.MAX_RISK / REAL_ACCOUNT - 0.02) < 1e-9,
          f"per-trade risk == 2% of $16k (got {ds.MAX_RISK/REAL_ACCOUNT:.3%})")


def test_tier3_ceiling_consistent():
    check(ds.MAX_RISK_TIER3 == 480, f"MAX_RISK_TIER3 == $480 (got {ds.MAX_RISK_TIER3})")
    check(ds.MAX_RISK_TIER3 > ds.MAX_RISK,
          f"tier-3 ceiling > MAX_RISK (480 > 320) — qty-3 path is reachable again")


def test_portfolio_cap_is_15pct():
    max_total = ds.MAX_POSITIONS * ds.MAX_RISK_TIER3   # worst case: every slot a qty-3 trade
    check(ds.MAX_POSITIONS == 5, f"MAX_POSITIONS == 5 (got {ds.MAX_POSITIONS})")
    check(max_total == 2400, f"max total open risk == $2,400 (got {max_total})")
    check(abs(max_total / REAL_ACCOUNT - 0.15) < 1e-9,
          f"portfolio risk cap == 15% of $16k (got {max_total/REAL_ACCOUNT:.1%})")


if __name__ == "__main__":
    print("🧪 Tradier $16k / 2% scaling tests\n")
    test_per_trade_risk_is_2pct()
    test_tier3_ceiling_consistent()
    test_portfolio_cap_is_15pct()
    print(f"\n{'─'*48}\n  {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
