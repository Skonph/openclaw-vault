#!/usr/bin/env python3
"""
Run this LOCALLY (not in OpenClaw cron) to confirm the live free-tier APIs
return the shapes openclaw_forward.py expects.

    export ALPHAVANTAGE_API_KEY=your_av_key
    export FINNHUB_API_KEY=your_fh_key
    python3 validate_forward_live.py F

It hits each endpoint once, prints the top-level shape, and flags any
mismatch against what the parser reads. If a shape differs, paste the
flagged output back and the parser gets a one-line fix.
"""
import os, sys, json
import openclaw_forward as fwd

sym = sys.argv[1] if len(sys.argv) > 1 else "F"
av = os.environ.get("ALPHAVANTAGE_API_KEY")
fh = os.environ.get("FINNHUB_API_KEY")

print(f"AV key set: {bool(av)}   FH key set: {bool(fh)}\n")

if fh:
    print("== Finnhub earnings calendar ==")
    d = fwd._http_get_json(f"{fwd.FH_BASE}/calendar/earnings",
                           {"symbol": sym, "from": "2026-06-25",
                            "to": "2026-09-25", "token": fh})
    print("keys:", list(d.keys()) if isinstance(d, dict) else type(d))
    if d and d.get("earningsCalendar"):
        print("sample row:", json.dumps(d["earningsCalendar"][0], indent=2)[:300])
    print()

    print("== Finnhub recommendation trends ==")
    d = fwd._http_get_json(f"{fwd.FH_BASE}/stock/recommendation",
                           {"symbol": sym, "token": fh})
    print("type:", type(d).__name__, "len:", len(d) if isinstance(d, list) else "n/a")
    if isinstance(d, list) and d:
        print("newest:", json.dumps(d[0], indent=2)[:300])
    print()

if av:
    print("== Alpha Vantage EARNINGS (surprise history) ==")
    d = fwd._http_get_json(fwd.AV_BASE, {"function": "EARNINGS",
                                         "symbol": sym, "apikey": av})
    if d and d.get("quarterlyEarnings"):
        print("sample:", json.dumps(d["quarterlyEarnings"][0], indent=2)[:300])
    else:
        print("RESPONSE:", json.dumps(d, indent=2)[:400])
    print()

    print("== Alpha Vantage NEWS_SENTIMENT ==")
    d = fwd._http_get_json(fwd.AV_BASE, {"function": "NEWS_SENTIMENT",
                                         "tickers": sym, "limit": 5,
                                         "apikey": av})
    if d and d.get("feed"):
        print("feed len:", len(d["feed"]))
        ts = d["feed"][0].get("ticker_sentiment", [])
        print("first article ticker_sentiment[0]:", json.dumps(ts[0] if ts else {}, indent=2)[:300])
    else:
        print("RESPONSE:", json.dumps(d, indent=2)[:400])
    print()

print("== Full extraction (real keys) ==")
ex = fwd.ForwardExtractor(av_key=av, fh_key=fh)
print(json.dumps(fwd.run_batch([sym], ex), indent=2))
