#!/usr/bin/env python3
"""
Safety tests for the position_monitor active_trades.json fix (2026-06-18):
  1. valid repaired file → loads the 1 reconciled trade
  2. malformed file → load_active() FAILS LOUD (SystemExit + alert), never
     silently returns [] while a real position rides unmanaged
  3. the reconciled 695/700 position at current cost-to-close ~$0.75 → monitor
     HOLDS (no false stop_loss from the bogus 0.04 entry_credit)
"""
import os
import sys
import json
import tempfile
from pathlib import Path

sys.argv = ["position_monitor.py", "--test"]
import position_monitor as pm   # noqa: E402

PASS = FAIL = 0


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✅ {msg}")
    else:
        FAIL += 1; print(f"  ❌ {msg}")


TRADE = {
    "trade_id": "reconciled-20260618", "strategy": "Bull Put Spread",
    "symbol": "SPY", "expiration": "2026-06-26", "entry_credit": 0.04,
    "profit_target_debit": 0.01, "stop_loss_debit": 2.50,
    "order_id": "reconciled-manual", "entered_at": "2026-06-18T00:00:00",
    "quantity": 3, "short_symbol": "SPY260626P00700000",
    "long_symbol": "SPY260626P00695000",
}


def test_valid_loads():
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump([TRADE], f, indent=2)
    orig = pm.ACTIVE_TRADES
    pm.ACTIVE_TRADES = Path(path)
    try:
        data = pm.load_active()
        check(len(data) == 1 and data[0]["trade_id"] == "reconciled-20260618",
              "valid repaired file → 1 trade loaded")
    finally:
        pm.ACTIVE_TRADES = orig
        os.unlink(path)


def test_malformed_fails_loud():
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        f.write("[\n  {\n    trade_id: reconciled-20260618,\n  }\n]\n")  # unquoted keys
    orig_path, orig_tg = pm.ACTIVE_TRADES, pm.send_telegram
    alerts = []
    pm.send_telegram = lambda m: alerts.append(m)
    pm.ACTIVE_TRADES = Path(path)
    try:
        try:
            pm.load_active()
            check(False, "malformed file should raise, but returned normally")
        except SystemExit:
            check(True, "malformed file → SystemExit (fail loud, not silent [])")
        check(alerts and "NOT being monitored" in alerts[0],
              "malformed file → Telegram alert sent")
    finally:
        pm.ACTIVE_TRADES, pm.send_telegram = orig_path, orig_tg
        os.unlink(path)


def test_monitor_holds_position():
    # Simulate current cost-to-close ~$0.75 (matches the live -$213 mark).
    orig_cost = pm.spread_cost_to_close
    pm.spread_cost_to_close = lambda quotes, s, l: 0.75
    try:
        exited = pm.evaluate_trade(dict(TRADE), quotes={})
        check(exited is False,
              "monitor HOLDS at $0.75 cost-to-close (no false stop from 0.04 credit)")
    finally:
        pm.spread_cost_to_close = orig_cost


if __name__ == "__main__":
    print("🧪 position_monitor active_trades safety tests\n")
    test_valid_loads()
    test_malformed_fails_loud()
    test_monitor_holds_position()
    print(f"\n{'─'*48}\n  {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
