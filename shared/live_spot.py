#!/usr/bin/env python3
"""
live_spot.py — Single source of truth for LIVE underlying spot prices (zero-token).

Why this exists: several production scripts (sync_openclaw_anna_memory, intelligent_spread_formatter,
dispatch_daily_premarket_brief, transaction_journal_manager) carried HARDCODED spot prices. On
2026-09-20 the shared "ground truth" doc claimed XLU $45.80 / "100% SAFE" while the real close was
$41.10 and the short 44P was 7% IN THE MONEY. Any script that publishes position safety MUST resolve
prices from this module — never from a literal.

Sources (fired in parallel per symbol, first success wins, in priority order):
  1. Tradier PROD   /v1/markets/quotes          (last / close)
  2. Finnhub        /api/v1/quote               (c)
  3. Alpaca data    /v2/stocks/{sym}/snapshot   (IEX dailyBar close)

Every result carries `source` + `asof`. If ALL sources fail, `price` is None and the caller must
degrade explicitly (print UNAVAILABLE / use the broker's own mark) — NEVER invent a number.

Usage:
    from live_spot import get_spot, get_spots
    px = get_spot("XLU")            # {"symbol","price","source","asof","prev_close"} | price=None
    book = get_spots(["XLU","XLF","SPY","VIX"])
CLI:
    python3 live_spot.py XLU XLF SPY VIX AMD
"""
from __future__ import annotations
import json, os, ssl, datetime, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CACHE_PATH = Path(__file__).resolve().parent / ".spot_cache.json"
CACHE_TTL = 300          # seconds; intraday spot only
TIMEOUT = 12
CTX = ssl._create_unverified_context()
ENV = {}


def _load_env() -> dict:
    """Load API keys from the canonical env file(s)."""
    global ENV
    if ENV:
        return ENV
    for p in (Path("/home/ubuntu/openclaw/.env"),
              Path(__file__).resolve().parent / ".env",
              Path(__file__).resolve().parent.parent / "Tradier" / ".env",
              Path("/home/ubuntu/.env")):
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                ENV[k.strip()] = v.strip().strip('"').strip("'")
    return ENV


def _get_json(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {"Accept": "application/json"})
    with urllib.request.urlopen(req, context=CTX, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


# ------------------------------------------------------------------ sources
def _tradier_spot(sym: str):
    tok = _load_env().get("TRADIER_PROD_TOKEN")
    if not tok:
        return None
    d = _get_json("https://api.tradier.com/v1/markets/quotes?" + urllib.parse.urlencode({"symbols": sym}),
                  {"Authorization": "Bearer " + tok, "Accept": "application/json"})
    q = (d.get("quotes") or {}).get("quote")
    if isinstance(q, list):
        q = q[0] if q else None
    if not isinstance(q, dict):
        return None
    px = q.get("last") or q.get("close")
    if not px:
        return None
    return {"price": float(px), "source": "tradier",
            "prev_close": float(q.get("prevclose") or 0) or None,
            "asof": datetime.datetime.now().isoformat(timespec="seconds")}


def _finnhub_spot(sym: str):
    tok = _load_env().get("FINNHUB_API_KEY")
    if not tok:
        return None
    d = _get_json(f"https://finnhub.io/api/v1/quote?symbol={urllib.parse.quote(sym)}&token={tok}")
    px = d.get("c")
    if not px:
        return None
    return {"price": float(px), "source": "finnhub",
            "prev_close": float(d.get("pc") or 0) or None,
            "asof": datetime.datetime.now().isoformat(timespec="seconds")}


def _alpaca_spot(sym: str):
    env = _load_env()
    key, sec = env.get("ALPACA_API_KEY"), env.get("ALPACA_SECRET_KEY")
    if not (key and sec):
        return None
    d = _get_json(f"https://data.alpaca.markets/v2/stocks/{urllib.parse.quote(sym)}/snapshot?feed=iex",
                  {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec, "Accept": "application/json"})
    px = ((d.get("dailyBar") or {}).get("c")) or ((d.get("latestTrade") or {}).get("p"))
    if not px:
        return None
    return {"price": float(px), "source": "alpaca_iex",
            "prev_close": float((d.get("prevDailyBar") or {}).get("c") or 0) or None,
            "asof": datetime.datetime.now().isoformat(timespec="seconds")}


SOURCES = (_tradier_spot, _finnhub_spot, _alpaca_spot)

# VIX is an index: Tradier exposes it as "VIX" (quotes) — Finnhub/Alpaca do not. Alias map for
# instruments whose provider symbol differs from the trader-facing symbol.
ALIASES = {"VIX": ["VIX", "^VIX"], "$VIX": ["VIX", "^VIX"]}


# ------------------------------------------------------------------ cache
def _read_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text())
    except Exception:
        return {}


def _write_cache(c: dict) -> None:
    try:
        CACHE_PATH.write_text(json.dumps(c, indent=1))
    except Exception:
        pass


def get_spot(symbol: str, max_age: int = CACHE_TTL, use_cache: bool = True) -> dict:
    """Resolve one symbol. Never fabricates: price is None when every source fails."""
    symbol = symbol.upper().lstrip("$")
    now = datetime.datetime.now()
    cache = _read_cache() if use_cache else {}
    hit = cache.get(symbol)
    if hit and hit.get("price") is not None:
        try:
            age = (now - datetime.datetime.fromisoformat(hit["asof"])).total_seconds()
            if age <= max_age:
                return {**hit, "cached": True}
        except Exception:
            pass

    result = {"symbol": symbol, "price": None, "source": None, "prev_close": None,
              "asof": now.isoformat(timespec="seconds"), "cached": False}
    for cand in ALIASES.get(symbol, [symbol]):
        for src in SOURCES:
            try:
                r = src(cand)
            except Exception:
                r = None
            if r and r.get("price"):
                r.update({"symbol": symbol, "cached": False})
                cache[symbol] = r
                _write_cache(cache)
                return r
    cache[symbol] = result
    _write_cache(cache)
    return result


def get_spots(symbols, max_age: int = CACHE_TTL, use_cache: bool = True) -> dict:
    """Parallel resolve of many symbols -> {SYM: result}. Zero-token, thread-pooled."""
    syms = sorted({s.upper().lstrip("$") for s in symbols})
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(syms)))) as ex:
        pairs = zip(syms, ex.map(lambda s: get_spot(s, max_age, use_cache), syms))
    return {s: r for s, r in pairs}


def price_of(symbol: str, default=None):
    """Convenience: numeric price or `default` (never a fabricated literal)."""
    px = get_spot(symbol).get("price")
    return px if px is not None else default


if __name__ == "__main__":
    import sys
    want = sys.argv[1:] or ["SPY", "QQQ", "IWM", "VIX", "XLU", "XLF", "AMD"]
    book = get_spots(want, use_cache=False)
    print(f"LIVE SPOT BOOK — {datetime.datetime.now():%Y-%m-%d %H:%M:%S} ICT")
    for s in want:
        r = book.get(s.upper(), {})
        px = r.get("price")
        print(f"  {s.upper():<6} {'$%.2f' % px if px else 'UNAVAILABLE':>12}  "
              f"src={r.get('source')}  prev={r.get('prev_close')}  asof={r.get('asof')}")
