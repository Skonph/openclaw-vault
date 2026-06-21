#!/usr/bin/env python3
"""Tests for apply_shared_macro() — Tradier consumer of the canonical
market_context.json (written by market_context_writer.py)."""
import sys

sys.argv = ["daily_scan.py", "--test"]   # avoid module-load credential exit
import daily_scan as ds   # noqa: E402

PASS = FAIL = 0


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✅ {msg}")
    else:
        FAIL += 1; print(f"  ❌ {msg}")


# Mirrors the real market_context.json schema (quotes.<SYM>.last/change/change_pct)
SIG = {
    "regime": "moderate",
    "calendar_skip": False,
    "quotes": {
        "SPY": {"last": 751.35, "change": 1.02, "change_pct": 0.14},
        "QQQ": {"last": 733.99, "change": 4.13, "change_pct": 0.57},
        "IWM": {"last": 294.18, "change": 2.10, "change_pct": 0.72},
        "VIX": {"last": 16.34, "change": -0.07, "change_pct": -0.43},
    },
}


def test_overrides_regime_symbols():
    qm = {"SPY": {"last": 999, "change_percentage": 9, "change": 5.0},
          "VIX": {"last": 11}, "SMH": {"last": 632.39, "change_percentage": 2.67}}
    ds.apply_shared_macro(qm, SIG)
    check(qm["SPY"]["last"] == 751.35, "SPY last overridden from market_context")
    check(qm["SPY"]["change_percentage"] == 0.14, "SPY %chg mapped from change_pct")
    check(qm["SPY"]["change"] == 1.02, "absolute change mapped (present in context)")
    check(qm["VIX"]["last"] == 16.34, "VIX last overridden")
    check(qm["QQQ"]["last"] == 733.99, "QQQ created/overridden from context")
    check(qm["SMH"]["last"] == 632.39, "sector SMH untouched (not in context)")


def test_none_signal_is_noop():
    qm = {"SPY": {"last": 751.22, "change_percentage": 0.12}}
    ds.apply_shared_macro(qm, None)
    check(qm["SPY"]["last"] == 751.22, "None signal → quote_map unchanged (fallback)")


def test_missing_last_skips_symbol():
    qm = {"SPY": {"last": 751.22}}
    ds.apply_shared_macro(qm, {"quotes": {"SPY": {"last": None}, "VIX": {"last": 17.0}}})
    check(qm["SPY"]["last"] == 751.22, "missing SPY.last → SPY untouched")
    check(qm["VIX"]["last"] == 17.0, "VIX still applied when present")


if __name__ == "__main__":
    print("🧪 Tradier shared market_context consumer tests\n")
    test_overrides_regime_symbols()
    test_none_signal_is_noop()
    test_missing_last_skips_symbol()
    print(f"\n{'─'*48}\n  {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
