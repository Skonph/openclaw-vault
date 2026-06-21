#!/usr/bin/env python3
"""
market_context_writer.py
------------------------
Standalone script that collects live market context and writes
/home/ubuntu/shared/market_context.json for consumption by other bots/tools.

Dependencies: stdlib + requests + python-dotenv
"""

import os
import sys
import json
import re
import datetime
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
ENV_PATH           = Path("/home/ubuntu/openclaw/.env")
DAILY_SCAN_PATH    = Path("/home/ubuntu/trading-bot/daily_scan.py")
PORTFOLIO_PATH     = Path("/home/ubuntu/shared/portfolio_positions.json")
OUTPUT_PATH        = Path("/home/ubuntu/shared/market_context.json")
SHARED_DIR         = Path("/home/ubuntu/shared")

# ── Known US market holidays (dates to avoid as option expiry) ───────────────
HOLIDAY_EXPIRATIONS = [
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # MLK Day
    "2026-02-16",  # Presidents' Day
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth
    "2026-07-03",  # Independence Day (observed)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving
    "2026-12-25",  # Christmas
]

# ── Tradier API ───────────────────────────────────────────────────────────────
TRADIER_BASE    = "https://api.tradier.com/v1"
QUOTE_ENDPOINT  = f"{TRADIER_BASE}/markets/quotes"
HIST_ENDPOINT   = f"{TRADIER_BASE}/markets/history"

# S&P 500 Select Sector SPDR ETFs
SECTOR_SYMBOLS  = [
    "XLK",  # Technology
    "XLF",  # Financials
    "XLY",  # Consumer Discretionary
    "XLP",  # Consumer Staples
    "XLE",  # Energy
    "XLI",  # Industrials
    "XLV",  # Health Care
    "XLU",  # Utilities
    "XLB",  # Materials
    "XLRE", # Real Estate
    "XLC",  # Communication Services
]

# Query quotes for core indices, VIX term structure, and sectors
QUOTE_SYMBOLS   = ["SPY", "QQQ", "IWM", "VIX", "VIX3M"] + SECTOR_SYMBOLS
SIGNAL_SYMBOLS  = ["SPY", "QQQ", "IWM"]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Load environment
# ─────────────────────────────────────────────────────────────────────────────

def load_env() -> dict:
    """Load .env from openclaw project; return dict of relevant keys."""
    try:
        from dotenv import dotenv_values
        cfg = dotenv_values(ENV_PATH)
    except ImportError:
        # Fallback: simple manual parser
        cfg = {}
        try:
            with open(ENV_PATH) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        cfg[k.strip()] = v.strip().strip('"').strip("'")
        except Exception as e:
            print(f"[WARN] Could not read .env: {e}")

    keys = ["TRADIER_PROD_TOKEN", "TRADIER_API_KEY",
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    result = {k: cfg.get(k, "") for k in keys}

    # Also push into os.environ so requests helpers can use them
    for k, v in result.items():
        if v:
            os.environ.setdefault(k, v)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 2. Fetch live quotes
# ─────────────────────────────────────────────────────────────────────────────

def fetch_quotes(token: str) -> dict:
    """Return dict keyed by symbol with Tradier quote data."""
    try:
        import requests
        resp = requests.get(
            QUOTE_ENDPOINT,
            params={"symbols": ",".join(QUOTE_SYMBOLS), "greeks": "false"},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        quotes_raw = data.get("quotes", {}).get("quote", [])
        if isinstance(quotes_raw, dict):
            quotes_raw = [quotes_raw]

        result = {}
        for q in quotes_raw:
            sym = q.get("symbol", "")
            result[sym] = {
                "last":        _safe_float(q.get("last")),
                "change":      _safe_float(q.get("change")),
                "change_pct":  _safe_float(q.get("change_percentage")),
                "open":        _safe_float(q.get("open")),
                "high":        _safe_float(q.get("high")),
                "low":         _safe_float(q.get("low")),
                "close":       _safe_float(q.get("close")),
                "volume":      q.get("volume"),
                "bid":         _safe_float(q.get("bid")),
                "ask":         _safe_float(q.get("ask")),
                "description": q.get("description", ""),
            }
        return result
    except Exception as e:
        print(f"[ERROR] fetch_quotes: {e}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# 3. Fetch history + compute signals
# ─────────────────────────────────────────────────────────────────────────────

def fetch_history(symbol: str, token: str, days: int = 40) -> list:
    """Return list of daily OHLCV dicts sorted ascending by date."""
    try:
        import requests
        end   = datetime.date.today()
        # Request extra calendar days to account for weekends/holidays
        start = end - datetime.timedelta(days=int(days * 1.5))

        resp = requests.get(
            HIST_ENDPOINT,
            params={
                "symbol":   symbol,
                "interval": "daily",
                "start":    start.isoformat(),
                "end":      end.isoformat(),
            },
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        day_data = data.get("history", {}) or {}
        days_list = day_data.get("day", [])
        if isinstance(days_list, dict):
            days_list = [days_list]

        # Sort ascending, keep last `days` bars
        days_list = sorted(days_list, key=lambda x: x.get("date", ""))[-days:]
        return days_list
    except Exception as e:
        print(f"[ERROR] fetch_history({symbol}): {e}")
        return []


def compute_signals(symbol: str, bars: list) -> dict:
    """Compute technical indicators from daily bars."""
    signals = {
        "sma20":         None,
        "momentum_5d":   None,
        "momentum_10d":  None,
        "atr14":         None,
        "pct_vs_sma20":  None,
        "trend":         "unknown",
        "bars_available": len(bars),
    }

    try:
        closes = [_safe_float(b.get("close")) for b in bars]
        highs  = [_safe_float(b.get("high"))  for b in bars]
        lows   = [_safe_float(b.get("low"))   for b in bars]

        # Need at least 20 bars for SMA20
        if len(closes) < 20 or None in closes[-20:]:
            return signals

        # SMA20 (last 20 closes)
        sma20 = sum(closes[-20:]) / 20
        signals["sma20"] = round(sma20, 4)

        last_close = closes[-1]
        signals["pct_vs_sma20"] = round((last_close - sma20) / sma20 * 100, 4)

        # Momentum 5d / 10d (percentage change)
        if len(closes) >= 6:
            signals["momentum_5d"] = round(
                (closes[-1] - closes[-6]) / closes[-6] * 100, 4
            )
        if len(closes) >= 11:
            signals["momentum_10d"] = round(
                (closes[-1] - closes[-11]) / closes[-11] * 100, 4
            )

        # ATR14
        if len(closes) >= 15 and len(highs) >= 15 and len(lows) >= 15:
            true_ranges = []
            for i in range(1, 15):
                idx = len(closes) - 14 + i - 1
                tr = max(
                    highs[idx] - lows[idx],
                    abs(highs[idx] - closes[idx - 1]),
                    abs(lows[idx]  - closes[idx - 1]),
                )
                true_ranges.append(tr)
            signals["atr14"] = round(sum(true_ranges) / len(true_ranges), 4)

        # Trend determination
        pct = signals["pct_vs_sma20"]
        mom5 = signals["momentum_5d"]
        if pct is not None and mom5 is not None:
            if pct > 1.0 and mom5 > 0:
                signals["trend"] = "uptrend"
            elif pct < -1.0 and mom5 < 0:
                signals["trend"] = "downtrend"
            else:
                signals["trend"] = "chop"

    except Exception as e:
        print(f"[WARN] compute_signals({symbol}): {e}")

    return signals


def fetch_signals_for_symbols(symbols: list, token: str) -> dict:
    """Fetch history and compute signals for a list of symbols."""
    result = {}
    for sym in symbols:
        try:
            bars = fetch_history(sym, token, days=40)
            result[sym] = compute_signals(sym, bars)
        except Exception as e:
            print(f"[ERROR] fetch_signals_for_symbols({sym}): {e}")
            result[sym] = {"trend": "unknown", "bars_available": 0}
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 4. Parse calendar events from daily_scan.py
# ─────────────────────────────────────────────────────────────────────────────

def parse_calendar_events() -> tuple:
    """
    Parse fomc_dates and cpi_dates from daily_scan.py source.
    Returns (calendar_skip: bool, next_event: dict|None).
    """
    fomc_dates = []
    cpi_dates  = []

    try:
        src = DAILY_SCAN_PATH.read_text()

        fomc_match = re.search(
            r'fomc_dates\s*=\s*\[(.*?)\]', src, re.DOTALL
        )
        if fomc_match:
            fomc_dates = re.findall(r'"(\d{4}-\d{2}-\d{2})"', fomc_match.group(1))

        cpi_match = re.search(
            r'cpi_dates\s*=\s*\[(.*?)\]', src, re.DOTALL
        )
        if cpi_match:
            cpi_dates = re.findall(r'"(\d{4}-\d{2}-\d{2})"', cpi_match.group(1))

    except Exception as e:
        print(f"[WARN] parse_calendar_events: {e}")

    today = datetime.date.today()
    calendar_skip = False
    next_event = None
    closest_days = None

    all_events = (
        [("FOMC", d) for d in fomc_dates] +
        [("CPI",  d) for d in cpi_dates]
    )

    for name, date_str in all_events:
        try:
            event_date = datetime.date.fromisoformat(date_str)
            diff = (event_date - today).days
            if 0 <= diff <= 2:
                calendar_skip = True
            if diff >= 0 and (closest_days is None or diff < closest_days):
                closest_days = diff
                next_event = {"name": name, "date": date_str, "days_away": diff}
        except Exception:
            pass

    return calendar_skip, next_event


# ─────────────────────────────────────────────────────────────────────────────
# 5. Load portfolio snapshot
# ─────────────────────────────────────────────────────────────────────────────

def load_portfolio_snapshot() -> list:
    """Read portfolio_positions.json; return positions list or []."""
    try:
        if PORTFOLIO_PATH.exists():
            data = json.loads(PORTFOLIO_PATH.read_text())
            # Support {"positions": [...]} or bare list
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("positions", [])
    except Exception as e:
        print(f"[WARN] load_portfolio_snapshot: {e}")
    return []


# ─────────────────────────────────────────────────────────────────────────────
# 6. Compute regime
# ─────────────────────────────────────────────────────────────────────────────

def compute_regime(vix: float | None, spy_change_pct: float | None) -> str:
    """
    bear      → VIX > 20 AND SPY day-change < -1.5 %
    elevated  → VIX > 20
    low_iv    → VIX < 15
    moderate  → everything else
    """
    if vix is None:
        return "moderate"
    if vix > 20 and spy_change_pct is not None and spy_change_pct < -1.5:
        return "bear"
    if vix > 20:
        return "elevated"
    if vix < 15:
        return "low_iv"
    return "moderate"


# ─────────────────────────────────────────────────────────────────────────────
# 7. Session date (next US trading day)
# ─────────────────────────────────────────────────────────────────────────────

US_HOLIDAYS_2026 = {
    datetime.date(2026, 1, 1),
    datetime.date(2026, 1, 19),
    datetime.date(2026, 2, 16),
    datetime.date(2026, 4, 3),
    datetime.date(2026, 5, 25),
    datetime.date(2026, 6, 19),
    datetime.date(2026, 7, 3),
    datetime.date(2026, 9, 7),
    datetime.date(2026, 11, 26),
    datetime.date(2026, 12, 25),
}


def next_trading_day() -> str:
    """Return next US trading day as YYYY-MM-DD string."""
    # If we're running before/during US market hours, today is the session;
    # otherwise use tomorrow. Use ET approximation.
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    # ET = UTC-4 (EDT) or UTC-5 (EST); use -4 conservatively
    now_et  = now_utc - datetime.timedelta(hours=4)
    candidate = now_et.date()

    # If after 4 pm ET, move to next day
    if now_et.hour >= 16:
        candidate += datetime.timedelta(days=1)

    # Advance until we land on a weekday that is not a holiday
    for _ in range(10):
        if candidate.weekday() < 5 and candidate not in US_HOLIDAYS_2026:
            return candidate.isoformat()
        candidate += datetime.timedelta(days=1)

    return candidate.isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_float(val) -> float | None:
    try:
        if val is None:
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  market_context_writer.py — building market_context.json")
    print("=" * 60)

    # ── 1. Environment ────────────────────────────────────────────────────────
    env = load_env()
    token = env.get("TRADIER_PROD_TOKEN") or env.get("TRADIER_API_KEY") or ""
    if not token:
        print("[WARN] No Tradier token found — quote/history calls will fail.")

    # ── 2. Quotes ─────────────────────────────────────────────────────────────
    print("\n[1/5] Fetching live quotes …")
    quotes = fetch_quotes(token)

    vix_data       = quotes.get("VIX", {})
    vxv_data       = quotes.get("VIX3M", {})
    spy_data       = quotes.get("SPY", {})
    
    vix            = vix_data.get("last")
    vxv            = vxv_data.get("last")
    spy_change_pct = spy_data.get("change_pct")

    vix_vxv_ratio  = None
    if vix is not None and vxv is not None and vxv > 0:
        vix_vxv_ratio = round(vix / vxv, 4)

    print(f"      VIX={vix}  VXV={vxv}  Ratio={vix_vxv_ratio}")
    print(f"      SPY chg={spy_change_pct}%")

    # ── 3. History + Signals ──────────────────────────────────────────────────
    print("[2/5] Fetching history and computing signals …")
    signals = fetch_signals_for_symbols(SIGNAL_SYMBOLS, token)
    for sym, sig in signals.items():
        print(f"      {sym}: trend={sig.get('trend')}  "
              f"pct_vs_sma20={sig.get('pct_vs_sma20')}  "
              f"mom5d={sig.get('momentum_5d')}")

    print("[2b/5] Fetching sector history and computing sector signals …")
    sectors = fetch_signals_for_symbols(SECTOR_SYMBOLS, token)
    for sym, sig in sectors.items():
        print(f"      Sector {sym}: trend={sig.get('trend')}  "
              f"pct_vs_sma20={sig.get('pct_vs_sma20')}  "
              f"mom5d={sig.get('momentum_5d')}")

    # ── 4. Calendar ───────────────────────────────────────────────────────────
    print("[3/5] Parsing calendar events …")
    calendar_skip, next_event = parse_calendar_events()
    print(f"      calendar_skip={calendar_skip}  next_event={next_event}")

    # ── 5. Portfolio ──────────────────────────────────────────────────────────
    print("[4/5] Loading portfolio snapshot …")
    portfolio_snapshot = load_portfolio_snapshot()
    print(f"      {len(portfolio_snapshot)} position(s) loaded.")

    # ── 6. Regime ─────────────────────────────────────────────────────────────
    regime = compute_regime(vix, spy_change_pct)
    print(f"[5/5] Regime: {regime}")

    # ── 7. Assemble output ────────────────────────────────────────────────────
    session_date = next_trading_day()
    generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    context = {
        "generated_at":        generated_at,
        "session_date":        session_date,
        "regime":              regime,
        "vix":                 vix,
        "vxv":                 vxv,
        "vix_vxv_ratio":       vix_vxv_ratio,
        "spy_change_pct":      spy_change_pct,
        "calendar_skip":       calendar_skip,
        "next_event":          next_event,
        "signals":             signals,
        "sectors":             sectors,
        "quotes":              quotes,
        "portfolio_snapshot":  portfolio_snapshot,
        "holiday_expirations": HOLIDAY_EXPIRATIONS,
    }

    # ── 8. Write output ───────────────────────────────────────────────────────
    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(context, indent=2))
    print(f"\n✅ Written → {OUTPUT_PATH}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "─" * 60)
    print(f"  Session date : {session_date}")
    print(f"  Regime       : {regime}")
    print(f"  VIX          : {vix} (VXV: {vxv}, VIX/VXV Ratio: {vix_vxv_ratio})")
    print(f"  SPY change   : {spy_change_pct}%")
    print(f"  Calendar skip: {calendar_skip}"
          + (f"  [{next_event['name']} on {next_event['date']}]" if next_event else ""))
    print(f"  Positions    : {len(portfolio_snapshot)}")
    spy_sig = signals.get("SPY", {})
    print(f"  SPY trend    : {spy_sig.get('trend')}  "
          f"SMA20={spy_sig.get('sma20')}  "
          f"pct_vs_sma20={spy_sig.get('pct_vs_sma20')}%")
    print("─" * 60)


if __name__ == "__main__":
    main()
