#!/usr/bin/env python3
"""
Tests for the no-trade scan heartbeat (added 2026-06-18).

Verifies:
  1. log_scan_heartbeat() writes a {"type":"scan"} record in LIVE mode.
  2. It is suppressed in TEST_MODE (no mock-run noise in trade_log.jsonl).
  3. daily_summary ignores scan records (not miscounted as entries).

Run:  python3 test_scan_heartbeat.py        (no --test needed; see argv hack below)
"""
import os
import sys
import json
import tempfile
from pathlib import Path
from datetime import date

# Import daily_scan WITH --test so the module-load credential check (which
# sys.exit(1)s on placeholder/missing tokens) doesn't abort the test run.
sys.argv = ["daily_scan.py", "--test"]
import daily_scan as ds          # noqa: E402
import daily_summary as dsum     # noqa: E402

PASS = 0
FAIL = 0


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {msg}")
    else:
        FAIL += 1
        print(f"  ❌ {msg}")


def test_heartbeat_writes_in_live_mode():
    tmpdir = tempfile.mkdtemp()
    orig_file, orig_mode = ds.__file__, ds.TEST_MODE
    ds.__file__ = os.path.join(tmpdir, "daily_scan.py")
    ds.TEST_MODE = False
    try:
        ds.log_scan_heartbeat("pass", {"strategy": "pass", "spy": 759.0, "vix": 11.2})
        log = os.path.join(tmpdir, "trade_log.jsonl")
        check(os.path.exists(log), "live-mode heartbeat writes a line")
        rec = json.loads(Path(log).read_text().strip())
        check(rec.get("type") == "scan", "record type == 'scan'")
        check(rec.get("result") == "no_trade", "record result == 'no_trade'")
        check(rec.get("reason") == "pass", "reason preserved ('pass')")
        check(rec.get("vix") == 11.2, "scan context (vix) preserved")
        check("success" not in rec, "no 'success' key (won't inflate entry counts)")
    finally:
        ds.__file__, ds.TEST_MODE = orig_file, orig_mode


def test_heartbeat_suppressed_in_test_mode():
    tmpdir = tempfile.mkdtemp()
    orig_file, orig_mode = ds.__file__, ds.TEST_MODE
    ds.__file__ = os.path.join(tmpdir, "daily_scan.py")
    ds.TEST_MODE = True
    try:
        ds.log_scan_heartbeat("cash", {"strategy": "cash"})
        log = os.path.join(tmpdir, "trade_log.jsonl")
        check(not os.path.exists(log), "TEST_MODE suppresses heartbeat (no log noise)")
    finally:
        ds.__file__, ds.TEST_MODE = orig_file, orig_mode


def test_summary_ignores_scan_records():
    today = date.today().isoformat()
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"date": today, "strategy": "Bull Put Spread",
                            "success": True, "order_id": "X1"}) + "\n")
        f.write(json.dumps({"type": "scan", "date": today, "result": "no_trade",
                            "reason": "pass"}) + "\n")
        f.write(json.dumps({"type": "exit", "date": today, "success": True,
                            "realized_pnl": 21.0, "order_id": "E1"}) + "\n")
    orig = dsum.TRADE_LOG
    dsum.TRADE_LOG = Path(path)
    try:
        entries, exits = dsum.get_today_records()
        check(len(entries) == 1, f"get_today_records: 1 entry (got {len(entries)})")
        check(len(exits) == 1, f"get_today_records: 1 exit (got {len(exits)})")
        stats = dsum.get_performance_stats()
        check(stats and stats.get("total_entries") == 1,
              f"get_performance_stats: total_entries == 1 (got {stats.get('total_entries') if stats else None})")
    finally:
        dsum.TRADE_LOG = orig
        os.unlink(path)


if __name__ == "__main__":
    print("🧪 Scan-heartbeat tests\n")
    test_heartbeat_writes_in_live_mode()
    test_heartbeat_suppressed_in_test_mode()
    test_summary_ignores_scan_records()
    print(f"\n{'─'*48}")
    print(f"  {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
