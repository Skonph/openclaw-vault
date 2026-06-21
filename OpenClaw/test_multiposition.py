#!/usr/bin/env python3
"""
Tests for the portfolio-budgeted multi-position policy (2026-06-19) in
vault_updater.py: count cap, portfolio-risk cap, per-direction concentration
cap, ledger reconciliation, OCC underlying parse, and order max-loss.
"""
import sys
import vault_updater as vu

PASS = FAIL = 0


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✅ {msg}")
    else:
        FAIL += 1; print(f"  ❌ {msg}")


def test_occ_underlying():
    check(vu._occ_underlying("TOST260717P00012000") == "TOST", "OCC underlying TOST")
    check(vu._occ_underlying("SPY260626P00700000") == "SPY", "OCC underlying SPY")
    check(vu._occ_underlying("") == "", "OCC empty -> empty")


def test_order_max_loss():
    # debit spread: net debit paid = spread_mid * 100 * qty
    ml = vu._order_max_loss("bull_call", {"spread_mid": 0.45}, qty=2, limit_px=0.49)
    check(abs(ml - 90.0) < 1e-6, f"debit max loss = $90 (got {ml})")
    # iron condor: (width - credit) * 100 * qty
    ml = vu._order_max_loss("iron_condor",
                            {"put_short_strike": 725, "put_long_strike": 723},
                            qty=1, limit_px=-0.46)
    check(abs(ml - 154.0) < 1e-6, f"IC max loss = (2-0.46)*100 = $154 (got {ml})")


def test_reconcile_ledger():
    led = {"TOST": {"max_loss": 154}, "AAPL": {"max_loss": 200}}
    out = vu._reconcile_ledger(led, {"TOST"})           # AAPL closed out-of-band
    check(out == {"TOST": {"max_loss": 154}}, "reconcile drops closed AAPL, keeps TOST")
    check(vu._reconcile_ledger(led, set()) == {}, "all closed -> empty ledger")


def test_portfolio_gate():
    EQ = 2898.0  # current OpenClaw equity; budget @15% = $434.70
    # admit a first $200 trade into a flat book
    ok, why = vu._portfolio_admits(200, "bull", EQ, 0, 0.0, {},
                                   max_positions=2, risk_pct=0.15, max_per_dir=1)
    check(ok, "admit first $200 bull trade into flat book")

    # count cap: already at 2 of 2
    ok, why = vu._portfolio_admits(50, "neutral", EQ, 2, 100.0, {"bull": 1, "bear": 1},
                                   max_positions=2, risk_pct=0.15, max_per_dir=1)
    check(not ok and "position-count" in why, f"count cap blocks 3rd ({why})")

    # risk-budget cap: $200 used + $300 new = $500 > $434.70
    ok, why = vu._portfolio_admits(300, "bear", EQ, 1, 200.0, {"bull": 1},
                                   max_positions=3, risk_pct=0.15, max_per_dir=1)
    check(not ok and "portfolio-risk" in why, f"risk-budget cap blocks over-budget ({why})")

    # direction cap: already 1 bull, max 1 per direction
    ok, why = vu._portfolio_admits(50, "bull", EQ, 1, 50.0, {"bull": 1},
                                   max_positions=3, risk_pct=0.15, max_per_dir=1)
    check(not ok and "direction" in why, f"direction cap blocks 2nd bull ({why})")

    # a different direction within budget is admitted
    ok, why = vu._portfolio_admits(150, "bear", EQ, 1, 150.0, {"bull": 1},
                                   max_positions=3, risk_pct=0.15, max_per_dir=1)
    check(ok, "admit a bear trade alongside a bull when within budget")


def test_cumulative_budget():
    """Two orders in one run must respect the cumulative budget, not each in isolation."""
    EQ = 2898.0
    run_count, run_risk, dirs = 0, 0.0, {}
    ok1, _ = vu._portfolio_admits(200, "bull", EQ, run_count, run_risk, dirs,
                                  max_positions=3, risk_pct=0.15, max_per_dir=1)
    if ok1:
        run_count += 1; run_risk += 200; dirs["bull"] = 1
    ok2, why2 = vu._portfolio_admits(300, "bear", EQ, run_count, run_risk, dirs,
                                     max_positions=3, risk_pct=0.15, max_per_dir=1)
    check(ok1 and not ok2 and "portfolio-risk" in why2,
          "2nd order blocked once cumulative risk would exceed budget")


if __name__ == "__main__":
    print("🧪 OpenClaw multi-position portfolio policy tests\n")
    test_occ_underlying()
    test_order_max_loss()
    test_reconcile_ledger()
    test_portfolio_gate()
    test_cumulative_budget()
    print(f"\n{'─'*52}\n  {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
