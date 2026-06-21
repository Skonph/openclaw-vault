#!/usr/bin/env python3
"""Tests for OpenClaw's shared market_context consumer (_macro_quote_from_context)."""
import sys
import openclaw_scanner as oc

PASS = FAIL = 0


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✅ {msg}")
    else:
        FAIL += 1; print(f"  ❌ {msg}")


CTX = {  # market_context.json quotes block
    "SPY": {"last": 751.35, "change_pct": 0.14},
    "QQQ": {"last": 733.99, "change_pct": 0.57},
    "IWM": {"last": 294.18, "change_pct": 0.72},
    "VIX": {"last": 16.34, "change_pct": -0.43},
}


def test_vix_spy_from_context():
    qv = oc._macro_quote_from_context("VIX", CTX)
    check(qv == {"price": 16.34, "change_pct": -0.43}, "VIX sourced from context")
    qs = oc._macro_quote_from_context("SPY", CTX)
    check(qs == {"price": 751.35, "change_pct": 0.14}, "SPY sourced from context")


def test_sector_falls_back():
    check(oc._macro_quote_from_context("XLE", CTX) is None,
          "sector ETF (XLE) not in context → None → live fallback")


def test_empty_or_missing():
    check(oc._macro_quote_from_context("VIX", {}) is None, "empty ctx → None")
    check(oc._macro_quote_from_context("VIX", None) is None, "None ctx → None")
    check(oc._macro_quote_from_context("SPY", {"SPY": {"last": None}}) is None,
          "last=None → None (fallback, no bad data)")


def test_regime_consumes_context_values():
    # The values the helper feeds determine_regime must produce a sane regime.
    vix = oc._macro_quote_from_context("VIX", CTX)["price"]
    spy = oc._macro_quote_from_context("SPY", CTX)["change_pct"]
    regime = oc.determine_regime(spy, vix)
    check(regime == "flat_low",
          f"determine_regime(0.14, 16.34) → flat_low (SPY flat, VIX<18); got {regime}")


if __name__ == "__main__":
    print("🧪 OpenClaw shared market_context consumer tests\n")
    test_vix_spy_from_context()
    test_sector_falls_back()
    test_empty_or_missing()
    test_regime_consumes_context_values()
    print(f"\n{'─'*48}\n  {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
