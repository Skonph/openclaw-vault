#!/usr/bin/env python3
"""
read_macro_signal.py — consumer-side reader for the shared market context.

Reads the CANONICAL shared file produced by market_context_writer.py:
    /home/ubuntu/shared/market_context.json   (written ~20:55 ICT, Mon–Fri)

The contract is deliberately tiny and SAFE: if the file is missing, malformed,
or stale, the loader returns None and the caller falls back to its own
market-data fetch. The shared context is an optimization, never a new single
point of failure.

Typical use inside a system's scan:

    from read_macro_signal import load_macro_signal
    sig = load_macro_signal()                 # None if missing/stale
    if sig:
        vix        = sig["quotes"]["VIX"]["last"]
        spy_chg    = sig["quotes"]["SPY"]["change_pct"]
        blackout   = sig["calendar_skip"]
        regime     = sig["regime"]
        spy_trend  = sig["signals"]["SPY"]["trend"]
        # ... feed these into THIS system's own thresholds ...
    else:
        ...  # fall back to existing per-system API fetch

Override the path with MACRO_SIGNAL_PATH if needed.
"""
import os
import json
import datetime as dt

DEFAULT_PATH = os.environ.get(
    "MACRO_SIGNAL_PATH",
    os.path.expanduser("~/shared/market_context.json"),
)

# market_context_writer.py runs ~20:55 ICT; consumers run 21:00–21:20 ICT
# (age ~5–25 min). 180 min default still rejects a stale file from a failed
# writer run while comfortably accepting same-evening signals.
DEFAULT_MAX_AGE_MIN = 180


def _parse_ts(s):
    """Parse an ISO timestamp, tolerating a trailing 'Z' (UTC)."""
    if not isinstance(s, str):
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_macro_signal(path=None, max_age_minutes=DEFAULT_MAX_AGE_MIN, now_utc=None):
    """
    Return the market-context dict, or None if unusable (missing / unparseable /
    missing required keys / older than max_age_minutes).
    """
    path = path or DEFAULT_PATH
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            sig = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    # minimal schema sanity for market_context.json
    if not isinstance(sig, dict) or "quotes" not in sig or "generated_at" not in sig:
        return None

    gen = _parse_ts(sig["generated_at"])
    if gen is None:
        return None
    if gen.tzinfo is None:
        gen = gen.replace(tzinfo=dt.timezone.utc)
    now = now_utc or dt.datetime.now(dt.timezone.utc)
    age_min = (now - gen).total_seconds() / 60.0
    if age_min > max_age_minutes:
        return None
    return sig


def signal_age_minutes(sig, now_utc=None):
    """Convenience: age of a loaded signal in minutes (for logging)."""
    gen = _parse_ts(sig.get("generated_at", ""))
    if gen is None:
        return None
    if gen.tzinfo is None:
        gen = gen.replace(tzinfo=dt.timezone.utc)
    now = now_utc or dt.datetime.now(dt.timezone.utc)
    return round((now - gen).total_seconds() / 60.0, 1)


if __name__ == "__main__":
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else None
    s = load_macro_signal(p)
    if s is None:
        print("⚠️  No usable market_context (missing / stale / malformed) — "
              "consumer should fall back to its own fetch.")
        sys.exit(2)
    q = s.get("quotes", {})
    print(f"✅ market_context OK (age {signal_age_minutes(s)} min)")
    print(f"   regime={s.get('regime')}  VIX={q.get('VIX', {}).get('last')}  "
          f"SPY={q.get('SPY', {}).get('last')} ({q.get('SPY', {}).get('change_pct')}%)  "
          f"calendar_skip={s.get('calendar_skip')}")
