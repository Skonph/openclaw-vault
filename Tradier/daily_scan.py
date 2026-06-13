"""
Tradier Paper Trading — Daily Market Scan & Trade Construction
Skon's $2K Options POC Account
--------------------------------------------------------------
DUAL-API ARCHITECTURE:
  Production key  → api.tradier.com     → live real-time quotes + options chains
  Sandbox key     → sandbox.tradier.com → paper order execution + account P&L

Run this each morning (9:45–10:00 AM ET / 8:45 PM ICT) to get
a market brief and construct trade setups pending your approval.

Usage:
    python daily_scan.py              # Full morning scan (needs .env)
    python daily_scan.py --test       # Dry-run with mock data (no API needed)
    python daily_scan.py --positions  # Check open positions only
    python daily_scan.py --construct  # Build new trade candidate
    python daily_scan.py --account    # Show account balances
    python daily_scan.py --execute    # Construct then submit after approval

Setup:
    pip install requests python-dotenv
    cp .env.example .env
    # Fill in all three values in .env:
    #   TRADIER_PROD_TOKEN      — production key  (api.tradier.com)
    #   TRADIER_SANDBOX_TOKEN   — sandbox key     (sandbox.tradier.com)
    #   TRADIER_SANDBOX_ACCOUNT — paper account ID
"""

import os
import sys
import json
import requests
from datetime import datetime, timedelta

# Only import dotenv if available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv optional; set env vars manually if needed

# ─── MODE FLAGS ───────────────────────────────────────────────────────────────
TEST_MODE      = "--test"      in sys.argv
NO_NOTIFY      = "--no-notify" in sys.argv   # suppress Telegram (used by telegram_bot.py)

if TEST_MODE:
    print("\n  ⚡ TEST MODE — using mock data, no API calls made\n")

# ─── DUAL-API CONFIG ──────────────────────────────────────────────────────────
#
#  MARKET DATA  →  Production API  (real-time, live Greeks, live bid/ask)
#  TRADE OPS    →  Sandbox API     (paper orders, positions, account balance)
#
PROD_URL       = "https://api.tradier.com/v1"       # Real-time market data
SANDBOX_URL    = "https://sandbox.tradier.com/v1"   # Paper trade execution

PROD_TOKEN     = os.getenv("TRADIER_PROD_TOKEN",    "YOUR_PRODUCTION_TOKEN_HERE")
SANDBOX_TOKEN  = os.getenv("TRADIER_SANDBOX_TOKEN", "YOUR_SANDBOX_TOKEN_HERE")
ACCOUNT_ID     = os.getenv("TRADIER_SANDBOX_ACCOUNT","YOUR_SANDBOX_ACCOUNT_ID_HERE")

MAX_RISK        = 100   # Hard max loss per trade ($) (5% of $2,000 equity)
MAX_POSITIONS   = 2     # Phase 1: max concurrent open positions
STARTING_CAPITAL = float(os.getenv("STARTING_CAPITAL", "2000"))  # Your POC benchmark

# Pre-built headers for each API
PROD_HEADERS = {
    "Authorization": f"Bearer {PROD_TOKEN}",
    "Accept": "application/json"
}
SANDBOX_HEADERS = {
    "Authorization": f"Bearer {SANDBOX_TOKEN}",
    "Accept": "application/json"
}

def _check_credentials():
    """Warn clearly if .env values are still placeholders."""
    missing = []
    if "YOUR_PRODUCTION" in PROD_TOKEN:
        missing.append("TRADIER_PROD_TOKEN")
    if "YOUR_SANDBOX_TOKEN" in SANDBOX_TOKEN:
        missing.append("TRADIER_SANDBOX_TOKEN")
    if "YOUR_SANDBOX_ACCOUNT" in ACCOUNT_ID:
        missing.append("TRADIER_SANDBOX_ACCOUNT")
    if missing:
        print(f"\n  ⚠️  Missing .env values: {', '.join(missing)}")
        print("  Copy .env.example → .env and fill in all three keys.")
        print("  Run with --test to continue without credentials.\n")
        sys.exit(1)

if not TEST_MODE:
    _check_credentials()

# ─── MOCK DATA (used in --test mode) ─────────────────────────────────────────
MOCK_QUOTES = {
    "SPY": {"symbol": "SPY",  "last": 742.15, "change": 1.83,  "change_percentage": 0.25},
    "QQQ": {"symbol": "QQQ",  "last": 708.40, "change": -0.92, "change_percentage": -0.13},
    "IWM": {"symbol": "IWM",  "last": 216.70, "change": 0.55,  "change_percentage": 0.26},
    "VIX": {"symbol": "VIX",  "last": 16.70,  "change": -0.36, "change_percentage": -2.11},
    "SMH": {"symbol": "SMH",  "last": 248.90, "change": 2.10,  "change_percentage": 0.85},
    "XLE": {"symbol": "XLE",  "last": 87.45,  "change": 0.62,  "change_percentage": 0.71},
    "TLT": {"symbol": "TLT",  "last": 88.30,  "change": -0.18, "change_percentage": -0.20},
}

MOCK_PUTS = [
    {"strike": 735, "bid": 1.10, "ask": 1.15, "option_type": "put",
     "greeks": {"delta": -0.28, "mid_iv": 0.18, "theta": -0.055}},
    {"strike": 732, "bid": 0.82, "ask": 0.87, "option_type": "put",
     "greeks": {"delta": -0.22, "mid_iv": 0.17, "theta": -0.044}},
    {"strike": 730, "bid": 0.40, "ask": 0.45, "option_type": "put",
     "greeks": {"delta": -0.18, "mid_iv": 0.17, "theta": -0.038}},
    {"strike": 728, "bid": 0.28, "ask": 0.32, "option_type": "put",
     "greeks": {"delta": -0.14, "mid_iv": 0.16, "theta": -0.030}},
    {"strike": 725, "bid": 0.18, "ask": 0.22, "option_type": "put",
     "greeks": {"delta": -0.11, "mid_iv": 0.16, "theta": -0.022}},
]

MOCK_CALLS = [
    {"strike": 755, "bid": 1.08, "ask": 1.13, "option_type": "call",
     "greeks": {"delta": 0.27, "mid_iv": 0.18, "theta": -0.053}},
    {"strike": 758, "bid": 0.80, "ask": 0.85, "option_type": "call",
     "greeks": {"delta": 0.22, "mid_iv": 0.17, "theta": -0.043}},
    {"strike": 760, "bid": 0.40, "ask": 0.45, "option_type": "call",
     "greeks": {"delta": 0.18, "mid_iv": 0.17, "theta": -0.037}},
    {"strike": 763, "bid": 0.26, "ask": 0.30, "option_type": "call",
     "greeks": {"delta": 0.14, "mid_iv": 0.16, "theta": -0.029}},
]

MOCK_POSITIONS = []  # No open positions in fresh paper account

MOCK_BALANCES = {
    "equity": 2000.00, "total_cash": 2000.00,
    "option_buying_power": 2000.00, "close_pl": 0.00
}

# ─── WATCHLIST ────────────────────────────────────────────────────────────────
MARKET_SCAN_SYMBOLS = ["SPY", "QQQ", "IWM", "VIX", "SMH", "XLE", "TLT"]

# Phase 1 trade candidates — liquid ETFs only
TRADE_CANDIDATES = {
    "SPY":  {"bias": "neutral-bullish", "strategy": "bull_put_spread"},
    "QQQ":  {"bias": "neutral-bullish", "strategy": "bull_put_spread"},
    "IWM":  {"bias": "neutral",         "strategy": "iron_condor"},
}

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def _handle_401(api_name, token_env_var):
    """Print a clear fix guide on 401 errors."""
    print(f"\n  ❌ 401 UNAUTHORIZED on {api_name} API")
    print(f"  ─────────────────────────────────────────────────")
    print(f"  Token in {token_env_var} was rejected.")
    print(f"  → Get the correct token from https://developer.tradier.com")
    print(f"  → Paste it into your .env as {token_env_var}=...")
    print(f"  Run with --test to continue without credentials.\n")
    sys.exit(1)

def get_market(endpoint, params=None):
    """
    GET from Production API (api.tradier.com) — real-time market data.
    Used for: quotes, options chains, Greeks, expirations.
    """
    if TEST_MODE:
        raise RuntimeError("TEST_MODE: use mock helpers instead of get_market()")
    url = f"{PROD_URL}{endpoint}"
    r = requests.get(url, headers=PROD_HEADERS, params=params)
    if r.status_code == 401:
        _handle_401("Production", "TRADIER_PROD_TOKEN")
    r.raise_for_status()
    return r.json()

def get_account(endpoint, params=None):
    """
    GET from Sandbox API (sandbox.tradier.com) — paper account data.
    Used for: positions, balances, order status.
    """
    if TEST_MODE:
        raise RuntimeError("TEST_MODE: use mock helpers instead of get_account()")
    url = f"{SANDBOX_URL}{endpoint}"
    r = requests.get(url, headers=SANDBOX_HEADERS, params=params)
    if r.status_code == 401:
        _handle_401("Sandbox", "TRADIER_SANDBOX_TOKEN")
    r.raise_for_status()
    return r.json()

def post_order(endpoint, data):
    """
    POST to Sandbox API (sandbox.tradier.com) — paper trade execution.
    Used for: placing and cancelling orders.
    No-op in TEST_MODE.
    """
    if TEST_MODE:
        print("  🧪 TEST_MODE: POST suppressed — order not sent to sandbox")
        return {"order": {"id": "TEST-001", "status": "ok (simulated)"}}
    url = f"{SANDBOX_URL}{endpoint}"
    r = requests.post(url, headers=SANDBOX_HEADERS, data=data)
    if r.status_code == 401:
        _handle_401("Sandbox", "TRADIER_SANDBOX_TOKEN")
    r.raise_for_status()
    return r.json()

def next_friday(weeks_out=2):
    """Return the expiration date (Friday) N weeks from today."""
    today = datetime.today()
    days_until_friday = (4 - today.weekday()) % 7
    if days_until_friday == 0:
        days_until_friday = 7          # already Friday — go to next one
    next_fri = today + timedelta(days=days_until_friday + (7 * (weeks_out - 1)))
    return next_fri.strftime("%Y-%m-%d")

def candidate_expirations(weeks_start=2, weeks_end=5):
    """Return a list of weekly Friday expirations to search, from near to far."""
    return [next_friday(w) for w in range(weeks_start, weeks_end + 1)]

def vix_regime(vix):
    if vix < 15:    return "🟢 LOW (premium sellers beware — cheap options)"
    elif vix < 20:  return "🟡 MODERATE (ideal credit spread zone)"
    elif vix < 30:  return "🟠 ELEVATED (iron condors, sell IV spike)"
    else:           return "🔴 HIGH (go to cash or buy protection only)"

def pct_change(current, previous):
    return ((current - previous) / previous) * 100

def get_sma_20(symbol):
    """
    Fetch historical daily closes for symbol from Production API
    and calculate the 20-day Simple Moving Average (SMA).
    """
    if TEST_MODE:
        return 730.0  # mock SMA below current SPY/QQQ prices for testing
        
    import datetime as dt
    end_date = dt.date.today()
    start_date = end_date - dt.timedelta(days=40)
    
    try:
        data = get_market("/markets/history", {
            "symbol": symbol,
            "interval": "daily",
            "start": start_date.strftime("%Y-%m-%d"),
            "end": end_date.strftime("%Y-%m-%d")
        })
        history = data.get("history", {}) or {}
        days = history.get("day", [])
        if isinstance(days, dict):
            days = [days]
        if not days:
            print(f"  ⚠️ No historical daily bars returned for {symbol}")
            return None
            
        days.sort(key=lambda x: x["date"])
        closes = [float(d["close"]) for d in days if "close" in d]
        if len(closes) < 20:
            print(f"  ⚠️ Not enough daily bars to calculate SMA-20 for {symbol} (got {len(closes)})")
            return None
            
        sma = sum(closes[-20:]) / 20.0
        return round(sma, 2)
    except Exception as e:
        print(f"  ⚠️ Error fetching historical data for {symbol} SMA-20: {e}")
        return None

def check_calendar_skip():
    """
    Returns (skip: bool, reason: str) indicating if we should skip trade entry
    due to an upcoming FOMC meeting or CPI release (within 2 days).
    """
    import datetime as dt
    # 2026 FOMC and CPI dates
    fomc_dates = [
        "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
        "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"
    ]
    cpi_dates = [
        "2026-01-13", "2026-02-11", "2026-03-11", "2026-04-10", "2026-05-12",
        "2026-06-10", "2026-07-14", "2026-08-12", "2026-09-11", "2026-10-14",
        "2026-11-10", "2026-12-10"
    ]
    
    today = dt.date.today()
    
    # In test mode, we might want to simulate a skip by checking if we have a test argument
    if TEST_MODE and "--test-skip" in sys.argv:
        return True, "TEST MODE CPI Release on 2026-06-10 (simulated skip)"

    for event_str in fomc_dates:
        event_date = dt.datetime.strptime(event_str, "%Y-%m-%d").date()
        diff = (event_date - today).days
        if 0 <= diff <= 2:
            return True, f"FOMC Meeting on {event_str} (in {diff} days)"
            
    for event_str in cpi_dates:
        event_date = dt.datetime.strptime(event_str, "%Y-%m-%d").date()
        diff = (event_date - today).days
        if 0 <= diff <= 2:
            return True, f"CPI Release on {event_str} (in {diff} days)"
            
    return False, ""

# ─── SECTION 1: MORNING MARKET SCAN ─────────────────────────────────────────

def morning_scan():
    """Fetch key market quotes and print a structured morning brief."""
    print("\n" + "═" * 60)
    # Server runs in Bangkok (+07); convert to ET for display
    from datetime import timezone, timedelta
    now_utc  = datetime.now(timezone.utc)
    now_et   = now_utc.astimezone(timezone(timedelta(hours=-4)))  # EDT (UTC-4)
    print(f"  MORNING MARKET SCAN — {now_et.strftime('%A, %B %d %Y, %I:%M %p ET')}")
    if TEST_MODE:
        print("  (MOCK DATA — replace with live Tradier feed after .env setup)")
    print("═" * 60)

    if TEST_MODE:
        quote_map = MOCK_QUOTES
    else:
        symbols_str = ",".join(MARKET_SCAN_SYMBOLS)
        data = get_market("/markets/quotes", {"symbols": symbols_str, "greeks": "false"})
        quotes = data.get("quotes", {}).get("quote", [])
        if isinstance(quotes, dict):
            quotes = [quotes]
        quote_map = {q["symbol"]: q for q in quotes}

    spy = quote_map.get("SPY", {})
    qqq = quote_map.get("QQQ", {})
    vix = quote_map.get("VIX", {})
    smh = quote_map.get("SMH", {})
    xle = quote_map.get("XLE", {})

    spy_price  = spy.get("last", 0)
    qqq_price  = qqq.get("last", 0)
    vix_level  = vix.get("last", 0)
    smh_price  = smh.get("last", 0)
    xle_price  = xle.get("last", 0)

    print(f"\n  INDEX SNAPSHOT")
    print(f"  {'Symbol':<8} {'Price':>8}  {'Change':>8}  {'% Chg':>7}")
    print(f"  {'─'*40}")
    for sym in ["SPY", "QQQ", "IWM", "TLT"]:
        q = quote_map.get(sym, {})
        price  = q.get("last", 0)
        change = q.get("change", 0)
        pct    = q.get("change_percentage", 0)
        arrow  = "▲" if change >= 0 else "▼"
        print(f"  {sym:<8} ${price:>7.2f}  {arrow}{abs(change):>7.2f}  {pct:>6.2f}%")

    print(f"\n  VOLATILITY")
    print(f"  VIX:  {vix_level:.2f}  →  {vix_regime(vix_level)}")

    print(f"\n  SECTOR LEADERS")
    print(f"  SMH (Semis):    ${smh_price:.2f}  {smh.get('change_percentage', 0):+.2f}%")
    print(f"  XLE (Energy):   ${xle_price:.2f}  {xle.get('change_percentage', 0):+.2f}%")

    # ─── DAY-OF-WEEK FILTER ──────────────────────────────────────────────────
    # Monday: weekend gap risk.  Friday: weekend theta risk + wide spreads.
    # Best entry days: Tuesday, Wednesday, Thursday.
    from datetime import timezone as _tz, timedelta as _td
    dow = datetime.now(_tz.utc).astimezone(_tz(_td(hours=-4))).weekday()  # ET weekday
    DOW_NAMES = ["Monday","Tuesday","Wednesday","Thursday","Friday"]
    if dow in (0, 4):   # Monday=0, Friday=4
        print(f"\n  ⛔ {DOW_NAMES[dow]} — skipping entry (gap/weekend risk). Check back Tuesday.")
        return {
            "spy": spy_price, "qqq": qqq_price, "vix": vix_level,
            "spy_change": spy.get("change_percentage", 0),
            "strategy": "pass", "all_green": False,
        }

    # ─── MARKET REGIME + STRATEGY ROUTING ───────────────────────────────────
    spy_change_pct = spy.get("change_percentage", 0)

    print(f"\n  MARKET REGIME ASSESSMENT")
    print(f"  {'─'*40}")
    print(f"  SPY:  ${spy_price:.2f}  ({spy_change_pct:+.2f}% today)")
    print(f"  VIX:  {vix_level:.2f}  →  {vix_regime(vix_level)}")

    iwm       = quote_map.get("IWM", {})
    iwm_price = iwm.get("last", 0)

    # Determine which strategy fits today's conditions
    if vix_level > 30:
        strategy   = "cash"
        regime_msg = "🔴 EXTREME VOLATILITY — go to cash, do not sell premium"
    elif vix_level < VIX_SECONDARY_FLOOR:
        strategy   = "pass"
        regime_msg = f"⚪ VIX < {VIX_SECONDARY_FLOOR} — IV universally low, skip today"
    elif vix_level < VIX_SPY_FLOOR:
        # SPY IV too thin but QQQ/IWM typically carry 15-25% more IV — try them
        strategy   = "low_vix_secondary"
        regime_msg = (f"🟡 VIX {vix_level:.1f} ({VIX_SECONDARY_FLOOR}–{VIX_SPY_FLOOR} zone) — "
                      f"SPY premium thin, scanning QQQ → IWM for spread opportunities")
    elif abs(spy_change_pct) <= 0.5 and vix_level >= 18:
        strategy   = "iron_condor"
        regime_msg = "🔵 SIDEWAYS + ELEVATED IV — Iron Condor (collect from both sides)"
    elif spy_change_pct > 0.5:
        strategy   = "bull_put_spread"
        regime_msg = "🟢 BULLISH — Bull Put Spread (sell OTM puts below market)"
    elif spy_change_pct < -0.5:
        strategy   = "bear_call_spread"
        regime_msg = "🟡 BEARISH MOMENTUM — Bear Call Spread (sell OTM calls above market)"
    else:
        strategy   = "bull_put_spread"
        regime_msg = "🟢 FLAT/MILD — Bull Put Spread (single-side default)"

    print(f"\n  Strategy: {regime_msg}")

    return {
        "spy":        spy_price,
        "qqq":        qqq_price,
        "iwm":        iwm_price,
        "vix":        vix_level,
        "spy_change": spy_change_pct,
        "strategy":   strategy,
        "all_green":  strategy == "bull_put_spread",
    }

# ─── SECTION 2: OPTIONS CHAIN SCAN ───────────────────────────────────────────

def get_options_chain(symbol="SPY", expiration=None, option_type="put"):
    """
    Fetch options chain and filter candidates by option_type ("put" or "call").
    Used by both construct_bull_put_spread (puts) and construct_bear_call_spread (calls).
    """
    if not expiration:
        expiration = next_friday(weeks_out=2)

    print(f"\n  FETCHING {option_type.upper()} CHAIN: {symbol} expiring {expiration}")

    if TEST_MODE:
        chain = MOCK_PUTS if option_type == "put" else MOCK_CALLS
        print("  (Using mock options chain data)")
    else:
        # Live options chain from Production API — real-time Greeks and bid/ask
        data = get_market("/markets/options/chains", {
            "symbol": symbol,
            "expiration": expiration,
            "greeks": "true"
        })
        # Tradier returns {"options": null} for expirations with no chain data.
        # The `or {}` guard handles null so we never call .get() on None.
        options_raw = data.get("options") or {}
        option_data = options_raw.get("option", []) if options_raw else []
        # Normalize: Tradier returns a dict (not list) when only 1 option exists
        if isinstance(option_data, dict):
            option_data = [option_data]
        if not option_data:
            print("  ⚠️  No options data for this expiration — skipping.")
            return []

        if option_type == "put":
            chain = [
                o for o in option_data
                if o.get("option_type") == "put"
                and o.get("greeks") is not None
                and -0.35 <= (o["greeks"].get("delta", 0) or 0) <= -0.10
            ]
        else:  # call
            chain = [
                o for o in option_data
                if o.get("option_type") == "call"
                and o.get("greeks") is not None
                and 0.10 <= (o["greeks"].get("delta", 0) or 0) <= 0.35
            ]

    chain.sort(key=lambda x: abs(x["greeks"].get("delta", 0)), reverse=True)

    print(f"\n  {'Strike':>8}  {'Bid':>7}  {'Ask':>7}  {'Delta':>7}  {'IV':>7}  {'Theta':>8}")
    print(f"  {'─'*55}")
    for o in chain[:10]:
        g = o.get("greeks", {})
        print(f"  ${o['strike']:>7.0f}  ${o.get('bid',0):>6.2f}  ${o.get('ask',0):>6.2f}"
              f"  {(g.get('delta',0) or 0):>7.3f}  {(g.get('mid_iv',0) or 0)*100:>6.1f}%"
              f"  {(g.get('theta',0) or 0):>8.4f}")

    return chain

# ─── SECTION 3: TRADE CONSTRUCTION ───────────────────────────────────────────

def _score_spread(short_put, long_put, width):
    """
    Evaluate one spread combination. Returns a scored dict or None if invalid.

    Validation rules (Phase 1):
      - Net credit ≥ 15% of spread width   (relative floor — prevents fills too thin to profit)
      - Net credit ≥ $0.30 absolute minimum (hard floor regardless of width)
      - Max risk  ≤ MAX_RISK ($200)
      - Short delta between -0.15 and -0.35 (OTM but not too far)

    Why 15% not 20%: At high SPY prices (>$700), $2-wide spreads yield ~$0.35–0.40 credit
    which is 17–20% of width — rejecting these misses real trades. 15% floor still
    ensures minimum $0.30 on $2-wide and filters out genuinely thin fills.

    Ranking metric: credit / max_risk  (highest reward-per-dollar-risked wins)
    """
    short_bid  = short_put.get("bid", 0) or 0
    long_ask   = long_put.get("ask",  0) or 0
    net_credit = round(short_bid - long_ask, 2)
    max_loss   = round((width - net_credit) * 100, 2)

    min_credit = max(0.30, round(width * 0.15, 2))   # 15% of width, floor $0.30

    if net_credit < min_credit:   return None
    if max_loss   > MAX_RISK:     return None
    if max_loss   <= 0:           return None

    return {
        "net_credit":  net_credit,
        "max_loss":    max_loss,
        "max_profit":  round(net_credit * 100, 2),
        "score":       round(net_credit / max_loss, 4),   # reward-per-dollar
        "width":       width,
    }

MIN_DTE = 10   # Never enter with fewer than 10 DTE (gamma risk zone)
MAX_DTE = 28   # Never enter beyond 28 DTE — widened from 21 to catch further expirations and handle holidays
TARGET_DELTA_MAX = 0.35   # Short delta ≤ 0.35 — widened to catch more strikes
TARGET_DELTA_MIN = 0.10   # Short delta ≥ 0.10 — widened for low-IV days

# VIX routing tiers:
#   VIX ≥ 15       → normal SPY-first scan
#   12 ≤ VIX < 15  → SPY IV too thin; try QQQ then IWM (structurally higher IV)
#   VIX < 12       → universally low, skip all
VIX_SPY_FLOOR      = 15   # minimum VIX to trade SPY spreads
VIX_SECONDARY_FLOOR = 12  # below this, nothing is worth selling

def construct_bull_put_spread(symbol="SPY", expiration=None, spy_price=742, vix=16.0):
    """
    Smart spread selector:
      1. Searches expirations 2–5 weeks out (nearest first), skips any < 10 DTE
      2. At each expiration tries ALL short strike candidates × widths $2, $3, $5
         Short strike delta constrained to 0.15–0.30 per Phase 1 playbook
      3. Validates: credit ≥ 15% of width AND max_risk ≤ $200
      4. Picks the best credit/risk score across all valid combos at the nearest expiry
    """
    # 20-day SMA trend filter
    sma = get_sma_20(symbol)
    if sma is not None and spy_price < sma:
        print(f"  ⛔ TREND FILTER — {symbol} price ${spy_price:.2f} is below 20-day SMA ${sma:.2f}. Skipping put selling.")
        return None

    from datetime import date as _date
    today = _date.today()

    # ── EXPIRATION SEARCH LOOP ──────────────────────────────────────────
    expirations = [expiration] if expiration else candidate_expirations(2, 5)
    best        = None          # best valid candidate across all expiry+width combos
    best_exp    = None
    best_puts   = None

    for exp in expirations:
        # Skip expirations too close to expiry (gamma risk zone)
        dte = (_date.fromisoformat(exp) - today).days
        if dte < MIN_DTE:
            print(f"  ⏩  {exp} ({dte} DTE) — skipping, below {MIN_DTE}-DTE floor")
            continue
        if dte > MAX_DTE:
            print(f"  ⏩  {exp} ({dte} DTE) — skipping, above {MAX_DTE}-DTE max")
            continue

        puts = get_options_chain(symbol, exp)
        if not puts:
            continue

        # Build a dict keyed by strike for fast lookup
        put_by_strike = {p["strike"]: p for p in puts}

        # Phase 1 delta range: 0.15–0.30 (playbook target: 20–30% ITM probability)
        candidates = [p for p in puts
                      if -TARGET_DELTA_MAX <= (p["greeks"].get("delta", 0) or 0) <= -TARGET_DELTA_MIN]
        if not candidates:
            print(f"  ⚠️  {exp}: no strikes in delta {TARGET_DELTA_MIN}–{TARGET_DELTA_MAX} range")
            continue

        # Try every candidate short strike × every width — pick the best score
        found_at_this_exp = False
        for short_put in candidates:
            for width in [1, 2, 3, 4, 5, 7, 10]:
                long_strike = short_put["strike"] - width
                long_put    = put_by_strike.get(long_strike)
                if not long_put:
                    continue

                result = _score_spread(short_put, long_put, width)
                if result is None:
                    continue

                found_at_this_exp = True
                if best is None or result["score"] > best["score"]:
                    best      = {**result,
                                 "short_put": short_put,
                                 "long_put":  long_put}
                    best_exp  = exp
                    best_puts = puts

        # No break — continue searching all expirations to find global best score

    # ── RESULT REPORTING ────────────────────────────────────────────────
    if best is None:
        print(f"\n  ⚠️  NO VALID SPREAD FOUND across expirations {expirations}")
        print(f"  Reasons could be: low IV (thin credits), market holiday,")
        print(f"  or SPY near support (too risky to sell puts). PASS today.")
        return None

    short_put  = best["short_put"]
    long_put   = best["long_put"]
    net_credit = best["net_credit"]
    
    # Calculate quantity
    qty = 1
    score = best["score"]
    single_contract_risk = best["max_loss"]
    if vix > 20 and score > 0.30 and (2 * single_contract_risk <= MAX_RISK):
        qty = 2
        print(f"  ⚡ VIX > 20 & Score > 0.30: Dynamic contract sizing scaled to {qty} contracts.")
    else:
        print(f"  ⚡ Contract size set to {qty} contract(s).")

    max_loss   = round(best["max_loss"] * qty, 2)
    max_profit = round(best["max_profit"] * qty, 2)
    width      = best["width"]
    short_strike = short_put["strike"]
    long_strike  = long_put["strike"]
    short_bid    = short_put.get("bid", 0)
    long_ask     = long_put.get("ask",  0)
    short_delta  = short_put["greeks"].get("delta", 0) or 0
    short_iv     = (short_put["greeks"].get("mid_iv", 0) or 0) * 100
    short_theta  = short_put["greeks"].get("theta", 0) or 0
    breakeven    = round(short_strike - net_credit, 2)
    dte          = (datetime.strptime(best_exp, "%Y-%m-%d") - datetime.today()).days
    profit_target_close = round(net_credit * 0.50, 2)
    stop_loss_close     = round(net_credit * 2.0,  2)
    pct_otm      = round((spy_price - short_strike) / spy_price * 100, 2)

    print(f"\n{'═'*62}")
    print(f"  ✅ PROPOSED TRADE — PENDING YOUR APPROVAL")
    print(f"{'═'*62}")
    print(f"  Strategy:        Bull Put Spread  (${width:.0f}-wide) [Qty: {qty}]")
    print(f"  Underlying:      {symbol}  (current ${spy_price:.2f})")
    print(f"  Expiration:      {best_exp}  ({dte} DTE)")
    print(f"  Short strike:    {pct_otm:.1f}% OTM  |  Delta {short_delta:.3f}  |  IV {short_iv:.1f}%  |  Theta {short_theta:.4f}")
    print(f"  ──────────────────────────────────────────────────────")
    print(f"  SHORT:  SELL {symbol} ${short_strike:.0f} Put  @ ${short_bid:.2f} (bid)")
    print(f"  LONG:   BUY  {symbol} ${long_strike:.0f} Put  @ ${long_ask:.2f} (ask)")
    print(f"  ──────────────────────────────────────────────────────")
    print(f"  Net Credit:      ${net_credit:.2f}/share  (${max_profit:.0f} total)")
    print(f"  Max Profit:      ${max_profit:.0f}  (spread expires worthless)")
    print(f"  Max Loss:        ${max_loss:.0f}  (SPY closes below ${long_strike:.0f})")
    print(f"  Breakeven:       ${breakeven:.2f}  (SPY must stay above this)")
    print(f"  Prob. of profit: ~{100 + short_delta*100:.0f}%")
    print(f"  Reward/Risk:     {net_credit/((width - net_credit)):.2f}:1")
    print(f"  ── Exit Rules ─────────────────────────────────────────")
    print(f"  Profit target:   BTC ≤ ${profit_target_close:.2f}  (50% profit)")
    print(f"  Hard stop:       BTC ≥ ${stop_loss_close:.2f}  (2× entry cost)")
    print(f"  Time stop:       Close at market if open with 2 DTE remaining")
    print(f"  Risk/Account:    {max_loss/STARTING_CAPITAL*100:.1f}% of ${STARTING_CAPITAL:,.0f} benchmark")

    # Format OCC option symbols: e.g. SPY260605P00733000
    exp_formatted = best_exp.replace("-", "")[2:]
    short_sym = f"{symbol}{exp_formatted}P{int(short_strike*1000):08d}"
    long_sym  = f"{symbol}{exp_formatted}P{int(long_strike*1000):08d}"

    print(f"\n  ─── SANDBOX ORDER PAYLOAD ─────────────────────────────")
    print(f"""
  POST /v1/accounts/{ACCOUNT_ID}/orders
  {{
      "class":             "multileg",
      "symbol":            "{symbol}",
      "type":              "credit",
      "price":             "{net_credit:.2f}",
      "duration":          "day",
      "option_symbol[0]":  "{short_sym}",
      "side[0]":           "sell_to_open",
      "quantity[0]":       "{qty}",
      "option_symbol[1]":  "{long_sym}",
      "side[1]":           "buy_to_open",
      "quantity[1]":       "{qty}"
  }}
    """)

    # ── SAVE PENDING TRADE for Telegram /approve ─────────────────────────────
    # telegram_bot.py reads this file when the user sends /approve
    import json as _json
    _pending = {
        "meta": {
            "strategy":     "Bull Put Spread",
            "symbol":       symbol,
            "expiration":   best_exp,
            "dte":          dte,
            "short_strike": short_strike,
            "long_strike":  long_strike,
            "short_symbol": short_sym,
            "long_symbol":  long_sym,
            "short_bid":    round(best["short_put"].get("bid", 0), 2),
            "long_ask":     round(best["long_put"].get("ask", 0), 2),
            "net_credit":   net_credit,
            "max_loss":     max_loss,
            "max_profit":   max_profit,
            "breakeven":    breakeven,
            "account_id":   ACCOUNT_ID,
            "scanned_at":   datetime.now().isoformat(),
            "quantity":     qty,
        },
        "order_payload": {
            "class":              "multileg",
            "symbol":             symbol,
            "type":               "credit",
            "price":              f"{net_credit:.2f}",
            "duration":           "day",
            "option_symbol[0]":   short_sym,
            "side[0]":            "sell_to_open",
            "quantity[0]":        str(qty),
            "option_symbol[1]":   long_sym,
            "side[1]":            "buy_to_open",
            "quantity[1]":        str(qty),
        }
    }
    _pending_path = os.path.join(os.path.dirname(__file__), "pending_trade.json")
    with open(_pending_path, "w") as _f:
        _json.dump(_pending, _f, indent=2)
    print(f"  💾 Trade saved → pending_trade.json  (reply /approve in Telegram to execute)")

    return {
        "strategy":             "Bull Put Spread",
        "symbol":               symbol,
        "expiration":           best_exp,
        "short_strike":         short_strike,
        "long_strike":          long_strike,
        "net_credit":           net_credit,
        "max_loss":             max_loss,
        "max_profit":           max_profit,
        "breakeven":            breakeven,
        "profit_target_close":  profit_target_close,
        "stop_loss_close":      stop_loss_close,
        "short_option_symbol":  short_sym,
        "long_option_symbol":   long_sym,
        # Standardised keys for position_monitor.py
        "short_symbol":         short_sym,
        "long_symbol":          long_sym,
        "quantity":             qty,
    }

# ─── SECTION 3B: BEAR CALL SPREAD CONSTRUCTION ───────────────────────────────

def construct_bear_call_spread(symbol="SPY", expiration=None, spy_price=742, vix=16.0):
    """
    Bear Call Spread selector — mirror of construct_bull_put_spread.

    Triggered when SPY day change ≤ −0.5% (bearish momentum).
    Sells OTM calls ABOVE the market instead of OTM puts below it.

    Phase 1 rules (identical risk parameters):
      - Short call delta: +0.15 to +0.30  (OTM, ~15–30% chance of finishing ITM)
      - 14–21 DTE (same time-decay window)
      - Credit ≥ 15% of spread width, ≥ $0.30 absolute floor
      - Max loss ≤ $200
      - Long call is ABOVE the short call (protection caps upside risk)
    """
    # 20-day SMA trend filter
    sma = get_sma_20(symbol)
    if sma is not None and spy_price > sma:
        print(f"  ⛔ TREND FILTER — {symbol} price ${spy_price:.2f} is above 20-day SMA ${sma:.2f}. Skipping call selling.")
        return None

    from datetime import date as _date
    today = _date.today()

    expirations = [expiration] if expiration else candidate_expirations(2, 5)
    best      = None
    best_exp  = None

    for exp in expirations:
        dte = (_date.fromisoformat(exp) - today).days
        if dte < MIN_DTE:
            print(f"  ⏩  {exp} ({dte} DTE) — skipping, below {MIN_DTE}-DTE floor")
            continue
        if dte > MAX_DTE:
            print(f"  ⏩  {exp} ({dte} DTE) — skipping, above {MAX_DTE}-DTE max")
            continue

        calls = get_options_chain(symbol, exp, option_type="call")
        if not calls:
            continue

        call_by_strike = {c["strike"]: c for c in calls}

        # Short call must be OTM: delta +0.15 to +0.30
        candidates = [c for c in calls
                      if TARGET_DELTA_MIN <= (c["greeks"].get("delta", 0) or 0) <= TARGET_DELTA_MAX]
        if not candidates:
            print(f"  ⚠️  {exp}: no calls in delta {TARGET_DELTA_MIN}–{TARGET_DELTA_MAX} range")
            continue

        # Try every candidate short strike × every width
        found_at_this_exp = False
        for short_call in candidates:
            for width in [1, 2, 3, 4, 5, 7, 10]:
                long_strike = short_call["strike"] + width   # long call is ABOVE short call
                long_call   = call_by_strike.get(long_strike)
                if not long_call:
                    continue

                result = _score_spread(short_call, long_call, width)   # same math as puts
                if result is None:
                    continue

                found_at_this_exp = True
                if best is None or result["score"] > best["score"]:
                    best     = {**result, "short_call": short_call, "long_call": long_call}
                    best_exp = exp

        # No break — continue searching all expirations to find global best score

    # ── RESULT REPORTING ────────────────────────────────────────────────
    if best is None:
        print(f"\n  ⚠️  NO VALID BEAR CALL SPREAD FOUND across expirations {expirations}")
        print(f"  Reasons: calls too cheap, no liquid OTM strikes, or market too elevated.")
        print(f"  PASS today.")
        return None

    short_call   = best["short_call"]
    long_call    = best["long_call"]
    net_credit   = best["net_credit"]
    
    # Calculate quantity
    qty = 1
    score = best["score"]
    single_contract_risk = best["max_loss"]
    if vix > 20 and score > 0.30 and (2 * single_contract_risk <= MAX_RISK):
        qty = 2
        print(f"  ⚡ VIX > 20 & Score > 0.30: Dynamic contract sizing scaled to {qty} contracts.")
    else:
        print(f"  ⚡ Contract size set to {qty} contract(s).")

    max_loss     = round(best["max_loss"] * qty, 2)
    max_profit   = round(best["max_profit"] * qty, 2)
    width        = best["width"]
    short_strike = short_call["strike"]
    long_strike  = long_call["strike"]
    short_bid    = short_call.get("bid", 0)
    long_ask     = long_call.get("ask",  0)
    short_delta  = short_call["greeks"].get("delta", 0) or 0
    short_iv     = (short_call["greeks"].get("mid_iv", 0) or 0) * 100
    short_theta  = short_call["greeks"].get("theta", 0) or 0
    breakeven    = round(short_strike + net_credit, 2)   # breakeven is ABOVE short call
    dte          = (datetime.strptime(best_exp, "%Y-%m-%d") - datetime.today()).days
    profit_target_close = round(net_credit * 0.50, 2)
    stop_loss_close     = round(net_credit * 2.0,  2)
    pct_otm      = round((short_strike - spy_price) / spy_price * 100, 2)

    print(f"\n{'═'*62}")
    print(f"  ✅ PROPOSED TRADE — PENDING YOUR APPROVAL")
    print(f"{'═'*62}")
    print(f"  Strategy:        Bear Call Spread  (${width:.0f}-wide) [Qty: {qty}]")
    print(f"  Underlying:      {symbol}  (current ${spy_price:.2f})")
    print(f"  Expiration:      {best_exp}  ({dte} DTE)")
    print(f"  Short strike:    {pct_otm:.1f}% OTM above market  |  Delta +{short_delta:.3f}  |  IV {short_iv:.1f}%")
    print(f"  ──────────────────────────────────────────────────────")
    print(f"  SHORT:  SELL {symbol} ${short_strike:.0f} Call @ ${short_bid:.2f} (bid)")
    print(f"  LONG:   BUY  {symbol} ${long_strike:.0f} Call @ ${long_ask:.2f} (ask)")
    print(f"  ──────────────────────────────────────────────────────")
    print(f"  Net Credit:      ${net_credit:.2f}/share  (${max_profit:.0f} total)")
    print(f"  Max Profit:      ${max_profit:.0f}  (spread expires worthless below ${short_strike:.0f})")
    print(f"  Max Loss:        ${max_loss:.0f}  (SPY closes above ${long_strike:.0f})")
    print(f"  Breakeven:       ${breakeven:.2f}  (SPY must stay below this)")
    print(f"  Prob. of profit: ~{100 - short_delta*100:.0f}%")
    print(f"  Reward/Risk:     {net_credit/(width - net_credit):.2f}:1")
    print(f"  ── Exit Rules ─────────────────────────────────────────")
    print(f"  Profit target:   BTC ≤ ${profit_target_close:.2f}  (50% profit)")
    print(f"  Hard stop:       BTC ≥ ${stop_loss_close:.2f}  (2× entry cost)")
    print(f"  Time stop:       Close at market if open with 2 DTE remaining")
    print(f"  Risk/Account:    {max_loss/STARTING_CAPITAL*100:.1f}% of ${STARTING_CAPITAL:,.0f} benchmark")

    # OCC call symbol uses 'C' instead of 'P'
    exp_formatted = best_exp.replace("-", "")[2:]
    short_sym = f"{symbol}{exp_formatted}C{int(short_strike*1000):08d}"
    long_sym  = f"{symbol}{exp_formatted}C{int(long_strike*1000):08d}"

    print(f"\n  ─── SANDBOX ORDER PAYLOAD ─────────────────────────────")
    print(f"""
  POST /v1/accounts/{ACCOUNT_ID}/orders
  {{
      "class":             "multileg",
      "symbol":            "{symbol}",
      "type":              "credit",
      "price":             "{net_credit:.2f}",
      "duration":          "day",
      "option_symbol[0]":  "{short_sym}",
      "side[0]":           "sell_to_open",
      "quantity[0]":       "{qty}",
      "option_symbol[1]":  "{long_sym}",
      "side[1]":           "buy_to_open",
      "quantity[1]":       "{qty}"
  }}
    """)

    # ── SAVE PENDING TRADE for Telegram /approve ─────────────────────────────
    import json as _json
    _pending = {
        "meta": {
            "strategy":     "Bear Call Spread",
            "symbol":       symbol,
            "expiration":   best_exp,
            "dte":          dte,
            "short_strike": short_strike,
            "long_strike":  long_strike,
            "short_symbol": short_sym,
            "long_symbol":  long_sym,
            "short_bid":    round(short_call.get("bid", 0), 2),
            "long_ask":     round(long_call.get("ask",  0), 2),
            "net_credit":   net_credit,
            "max_loss":     max_loss,
            "max_profit":   max_profit,
            "breakeven":    breakeven,
            "account_id":   ACCOUNT_ID,
            "scanned_at":   datetime.now().isoformat(),
            "quantity":     qty,
        },
        "order_payload": {
            "class":              "multileg",
            "symbol":             symbol,
            "type":               "credit",
            "price":              f"{net_credit:.2f}",
            "duration":           "day",
            "option_symbol[0]":   short_sym,
            "side[0]":            "sell_to_open",
            "quantity[0]":        str(qty),
            "option_symbol[1]":   long_sym,
            "side[1]":            "buy_to_open",
            "quantity[1]":        str(qty),
        }
    }
    _pending_path = os.path.join(os.path.dirname(__file__), "pending_trade.json")
    with open(_pending_path, "w") as _f:
        _json.dump(_pending, _f, indent=2)
    print(f"  💾 Trade saved → pending_trade.json  (reply /approve in Telegram to execute)")

    return {
        "strategy":             "Bear Call Spread",
        "symbol":               symbol,
        "expiration":           best_exp,
        "short_strike":         short_strike,
        "long_strike":          long_strike,
        "net_credit":           net_credit,
        "max_loss":             max_loss,
        "max_profit":           max_profit,
        "breakeven":            breakeven,
        "profit_target_close":  profit_target_close,
        "stop_loss_close":      stop_loss_close,
        "short_option_symbol":  short_sym,
        "long_option_symbol":   long_sym,
        # Standardised keys for position_monitor.py
        "short_symbol":         short_sym,
        "long_symbol":          long_sym,
        "quantity":             qty,
    }

# ─── SECTION 3C: IRON CONDOR CONSTRUCTION ────────────────────────────────────

def construct_iron_condor(symbol="SPY", expiration=None, spy_price=742, vix=16.0):
    """
    Iron Condor: Bull Put Spread below the market + Bear Call Spread above it.

    Triggered when:
      - |SPY day change| ≤ 0.5%  (no strong directional bias)
      - VIX ≥ 18                 (enough IV to collect meaningful premium from both sides)
    """
    from datetime import date as _date
    today = _date.today()

    expirations = [expiration] if expiration else candidate_expirations(2, 5)
    best_put  = None
    best_call = None
    best_exp  = None

    for exp in expirations:
        dte = (_date.fromisoformat(exp) - today).days
        if dte < MIN_DTE:
            print(f"  ⏩  {exp} ({dte} DTE) — skipping, below {MIN_DTE}-DTE floor")
            continue
        if dte > MAX_DTE:
            print(f"  ⏩  {exp} ({dte} DTE) — skipping, above {MAX_DTE}-DTE max")
            continue

        # Fetch both chains at the same expiration in one loop iteration
        puts  = get_options_chain(symbol, exp, option_type="put")
        calls = get_options_chain(symbol, exp, option_type="call")
        if not puts or not calls:
            print(f"  ⚠️  {exp}: missing chain on one or both sides — skipping")
            continue

        put_by_strike  = {p["strike"]: p for p in puts}
        call_by_strike = {c["strike"]: c for c in calls}

        put_candidates  = [p for p in puts
                           if -TARGET_DELTA_MAX <= (p["greeks"].get("delta", 0) or 0) <= -TARGET_DELTA_MIN]
        call_candidates = [c for c in calls
                           if  TARGET_DELTA_MIN <= (c["greeks"].get("delta", 0) or 0) <=  TARGET_DELTA_MAX]

        if not put_candidates or not call_candidates:
            print(f"  ⚠️  {exp}: no candidates in delta range on one or both sides")
            continue

        # Best put spread at this expiry
        best_put_here = None
        for short_put in put_candidates:
            for width in [1, 2, 3, 4, 5, 7, 10]:
                long_put = put_by_strike.get(short_put["strike"] - width)
                if not long_put:
                    continue
                result = _score_spread(short_put, long_put, width)
                if result and (best_put_here is None or result["score"] > best_put_here["score"]):
                    best_put_here = {**result, "short": short_put, "long": long_put}

        # Best call spread at this expiry
        best_call_here = None
        for short_call in call_candidates:
            for width in [1, 2, 3, 4, 5, 7, 10]:
                long_call = call_by_strike.get(short_call["strike"] + width)
                if not long_call:
                    continue
                result = _score_spread(short_call, long_call, width)
                if result and (best_call_here is None or result["score"] > best_call_here["score"]):
                    best_call_here = {**result, "short": short_call, "long": long_call}

        if best_put_here and best_call_here:
            combined_score = best_put_here["score"] + best_call_here["score"]
            if best_put is None or combined_score > (best_put["score"] + best_call["score"]):
                best_put  = best_put_here
                best_call = best_call_here
                best_exp  = exp

    # ── RESULT ───────────────────────────────────────────────────────────
    if not best_put or not best_call:
        print(f"\n  ⚠️  NO VALID IRON CONDOR — could not find viable spreads on both sides.")
        return None

    put_credit   = best_put["net_credit"]
    call_credit  = best_call["net_credit"]
    total_credit = round(put_credit + call_credit, 2)
    put_width    = best_put["width"]
    call_width   = best_call["width"]

    # Only one wing can hit max loss at expiry
    downside_max = round((put_width  - total_credit) * 100, 2)
    upside_max   = round((call_width - total_credit) * 100, 2)
    single_contract_risk = max(downside_max, upside_max)

    # Calculate quantity
    qty = 1
    combined_score = best_put["score"] + best_call["score"]
    if vix > 20 and combined_score > 0.30 and (2 * (single_contract_risk / 100.0) * 100 <= MAX_RISK):
        qty = 2
        print(f"  ⚡ VIX > 20 & Score > 0.30: Dynamic contract sizing scaled to {qty} contracts.")
    else:
        print(f"  ⚡ Contract size set to {qty} contract(s).")

    max_loss     = round(single_contract_risk * qty, 2)
    total_premium = round(total_credit * 100 * qty, 2)

    dte = (datetime.strptime(best_exp, "%Y-%m-%d") - datetime.today()).days

    ps  = best_put["short"];   pl  = best_put["long"]
    cs  = best_call["short"];  cl  = best_call["long"]
    pss = ps["strike"];        pls = pl["strike"]
    css = cs["strike"];        cls = cl["strike"]

    profit_zone_width = round(css - pss, 2)
    profit_target_close = round(total_credit * 0.50, 2)
    stop_loss_close     = round(total_credit * 2.0,  2)

    print(f"\n{'═'*62}")
    print(f"  ✅ PROPOSED TRADE — PENDING YOUR APPROVAL")
    print(f"{'═'*62}")
    print(f"  Strategy:        Iron Condor  (${put_width:.0f}-wide puts / ${call_width:.0f}-wide calls) [Qty: {qty}]")
    print(f"  Underlying:      {symbol}  (current ${spy_price:.2f})")
    print(f"  Expiration:      {best_exp}  ({dte} DTE)")
    print(f"  Profit zone:     ${pss:.0f} – ${css:.0f}  ({profit_zone_width:.0f}-pt range,  {profit_zone_width/spy_price*100:.1f}% of SPY price)")
    print(f"  ──────────────────────────────────────────────────────")
    print(f"  PUT SPREAD  (bullish side — below market)")
    print(f"  SHORT:  SELL {symbol} ${pss:.0f} Put  @ ${ps.get('bid',0):.2f} (bid)")
    print(f"  LONG:   BUY  {symbol} ${pls:.0f} Put  @ ${pl.get('ask',0):.2f} (ask)")
    print(f"  Put credit:    ${put_credit:.2f}")
    print(f"  ──────────────────────────────────────────────────────")
    print(f"  CALL SPREAD  (bearish side — above market)")
    print(f"  SHORT:  SELL {symbol} ${css:.0f} Call @ ${cs.get('bid',0):.2f} (bid)")
    print(f"  LONG:   BUY  {symbol} ${cls:.0f} Call @ ${cl.get('ask',0):.2f} (ask)")
    print(f"  Call credit:   ${call_credit:.2f}")
    print(f"  ──────────────────────────────────────────────────────")
    print(f"  Total Credit:    ${total_credit:.2f}/share  (${total_premium:.0f} total)")
    print(f"  Max Profit:      ${total_premium:.0f}  (SPY stays between ${pss:.0f}–${css:.0f})")
    print(f"  Max Loss:        ${max_loss:.0f}  (SPY breaks outside either long strike)")
    print(f"  Reward/Risk:     {total_premium/max_loss*100:.1f}%")
    print(f"  Risk/Account:    {max_loss/STARTING_CAPITAL*100:.1f}% of ${STARTING_CAPITAL:,.0f} benchmark")
    print(f"  ── Exit Rules ─────────────────────────────────────────")
    print(f"  Profit target:   BTC both spreads ≤ ${profit_target_close:.2f}  (50% profit)")
    print(f"  Hard stop:       BTC threatened wing ≥ ${stop_loss_close:.2f}  (2× entry)")
    print(f"  Time stop:       Close both wings at market with 2 DTE remaining")

    exp_formatted  = best_exp.replace("-", "")[2:]
    put_short_sym  = f"{symbol}{exp_formatted}P{int(pss*1000):08d}"
    put_long_sym   = f"{symbol}{exp_formatted}P{int(pls*1000):08d}"
    call_short_sym = f"{symbol}{exp_formatted}C{int(css*1000):08d}"
    call_long_sym  = f"{symbol}{exp_formatted}C{int(cls*1000):08d}"

    print(f"\n  ─── SANDBOX ORDER PAYLOAD (4-leg multileg) ────────────")
    print(f"""
  POST /v1/accounts/{ACCOUNT_ID}/orders
  {{
      "class":             "multileg",
      "symbol":            "{symbol}",
      "type":              "credit",
      "price":             "{total_credit:.2f}",
      "duration":          "day",
      "option_symbol[0]":  "{put_short_sym}",   "side[0]": "sell_to_open",  "quantity[0]": "{qty}",
      "option_symbol[1]":  "{put_long_sym}",    "side[1]": "buy_to_open",   "quantity[1]": "{qty}",
      "option_symbol[2]":  "{call_short_sym}",  "side[2]": "sell_to_open",  "quantity[2]": "{qty}",
      "option_symbol[3]":  "{call_long_sym}",   "side[3]": "buy_to_open",   "quantity[3]": "{qty}"
  }}
    """)

    import json as _json
    _pending = {
        "meta": {
            "strategy":          "Iron Condor",
            "symbol":            symbol,
            "expiration":        best_exp,
            "dte":               dte,
            "put_short_strike":  pss,
            "put_long_strike":   pls,
            "call_short_strike": css,
            "call_long_strike":  cls,
            "put_short_symbol":  put_short_sym,
            "put_long_symbol":   put_long_sym,
            "call_short_symbol": call_short_sym,
            "call_long_symbol":  call_long_sym,
            "put_credit":        put_credit,
            "call_credit":       call_credit,
            "net_credit":        total_credit,
            "max_loss":          max_loss,
            "max_profit":        total_premium,
            "profit_zone_low":   pss,
            "profit_zone_high":  css,
            "account_id":        ACCOUNT_ID,
            "scanned_at":        datetime.now().isoformat(),
        },
        "order_payload": {
            "class":             "multileg",
            "symbol":            symbol,
            "type":              "credit",
            "price":             f"{total_credit:.2f}",
            "duration":         "day",
            "option_symbol[0]":  put_short_sym,  "side[0]": "sell_to_open", "quantity[0]": str(qty),
            "option_symbol[1]":  put_long_sym,   "side[1]": "buy_to_open",  "quantity[1]": str(qty),
            "option_symbol[2]":  call_short_sym, "side[2]": "sell_to_open", "quantity[2]": str(qty),
            "option_symbol[3]":  call_long_sym,  "side[3]": "buy_to_open",  "quantity[3]": str(qty),
        }
    }
    _pending_path = os.path.join(os.path.dirname(__file__), "pending_trade.json")
    with open(_pending_path, "w") as _f:
        _json.dump(_pending, _f, indent=2)
    print(f"  💾 Trade saved → pending_trade.json  (reply /approve in Telegram to execute)")

    return {
        "strategy":          "Iron Condor",
        "symbol":            symbol,
        "expiration":        best_exp,
        "net_credit":        total_credit,
        "max_loss":          max_loss,
        "max_profit":        total_premium,
        "put_short_strike":  pss,
        "put_long_strike":   pls,
        "call_short_strike": css,
        "call_long_strike":  cls,
        "profit_zone_low":   pss,
        "profit_zone_high":  css,
        # OCC symbols for position_monitor.py exit orders
        "put_short_symbol":  put_short_sym,
        "put_long_symbol":   put_long_sym,
        "call_short_symbol": call_short_sym,
        "call_long_symbol":  call_long_sym,
        # Profit/stop levels (50% and 2× of total credit)
        "profit_target_close": round(total_credit * 0.50, 2),
        "stop_loss_close":     round(total_credit * 2.0,  2),
        "put_credit":        put_credit,
        "call_credit":       call_credit,
        "quantity":          qty,
    }

# ─── SECTION 4: EXECUTE ORDER (APPROVAL-GATED) ───────────────────────────────

def execute_trade(trade_data):
    """
    Submit approved trade to Tradier paper trading account.
    ONLY call this after receiving explicit approval.
    """
    if not trade_data:
        print("  ❌ No valid trade data. Run construct_bull_put_spread first.")
        return

    confirm = input("\n  ⚠️  TYPE 'APPROVE' TO SUBMIT THIS TRADE: ").strip().upper()
    if confirm != "APPROVE":
        print("  Trade submission cancelled.")
        return

    payload = {
        "class": "multileg",
        "symbol": trade_data["symbol"],
        "type": "credit",
        "price": str(trade_data["net_credit"]),
        "duration": "day",
        "option_symbol[0]": trade_data["short_option_symbol"],
        "side[0]": "sell_to_open",
        "quantity[0]": "1",
        "option_symbol[1]": trade_data["long_option_symbol"],
        "side[1]": "buy_to_open",
        "quantity[1]": "1"
    }

    # Submit paper order to Sandbox API
    response = post_order(f"/accounts/{ACCOUNT_ID}/orders", payload)
    order_id = response.get("order", {}).get("id", "unknown")
    status   = response.get("order", {}).get("status", "unknown")

    print(f"\n  ✅ ORDER SUBMITTED")
    print(f"  Order ID:  {order_id}")
    print(f"  Status:    {status}")
    print(f"  Credit:    ${trade_data['net_credit']:.2f}")
    print(f"\n  → Log this in PLAYBOOK.md Trade Log")

    return response

# ─── SECTION 4B: AUTONOMOUS EXECUTION + TRADE LOG ───────────────────────────

def auto_execute_trade():
    """
    Auto-submit the pending trade (written by construct_*) to Tradier sandbox.
    Called immediately after trade construction — no manual approval needed.
    Returns an exec_result dict consumed by notify_telegram() and log_trade_activity().
    """
    pending_path = os.path.join(os.path.dirname(__file__), "pending_trade.json")

    if not os.path.exists(pending_path):
        return {"success": False, "error": "No pending_trade.json found"}

    with open(pending_path) as f:
        trade_data = json.load(f)

    payload = trade_data.get("order_payload", {})
    meta    = trade_data.get("meta", {})
    account = ACCOUNT_ID or meta.get("account_id", "")

    if TEST_MODE:
        print("  🧪 TEST MODE — order not submitted to sandbox (auto-execute suppressed)")
        return {"success": True, "order_id": "TEST-AUTO-001", "order_status": "simulated"}

    if not account or not SANDBOX_TOKEN or "YOUR_SANDBOX" in SANDBOX_TOKEN:
        return {"success": False, "error": "Missing SANDBOX credentials in .env"}

    url = f"{SANDBOX_URL}/accounts/{account}/orders"
    try:
        r = requests.post(url, headers=SANDBOX_HEADERS, data=payload, timeout=15)
        try:
            resp = r.json()
        except ValueError as je:
            print(f"  ❌ Auto-execute response not JSON. HTTP status: {r.status_code}")
            print(f"  Response content: {r.text[:300]}")
            return {"success": False, "error": f"HTTP {r.status_code}: non-JSON response ({r.text[:100]})"}
    except Exception as e:
        print(f"  ❌ Auto-execute error: {e}")
        return {"success": False, "error": str(e)}

    order_id = resp.get("order", {}).get("id", "unknown")
    status   = resp.get("order", {}).get("status", "unknown")

    if r.status_code in (200, 201) and status in ("ok", "pending", "open", "filled"):
        archive = os.path.join(os.path.dirname(__file__),
                               f"executed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        os.rename(pending_path, archive)
        print(f"  ✅ AUTO-EXECUTED:  Order ID {order_id}  |  Status: {status}")
        return {"success": True, "order_id": str(order_id), "order_status": status}
    else:
        err = resp.get("errors", resp)
        print(f"  ❌ Order rejected (HTTP {r.status_code}): {err}")
        return {"success": False, "error": str(err)[:200], "order_id": "rejected"}


def log_trade_activity(trade, exec_result):
    """
    Append one JSON line to trade_log.jsonl for the daily summary script.
    Each line is a self-contained record of one trade attempt.
    """
    from datetime import date as _date
    log_path = os.path.join(os.path.dirname(__file__), "trade_log.jsonl")

    entry = {
        "date":         _date.today().isoformat(),
        "executed_at":  datetime.now().isoformat(),
        "strategy":     trade.get("strategy", "Unknown"),
        "symbol":       trade.get("symbol",   "SPY"),
        "expiration":   trade.get("expiration", ""),
        "net_credit":   trade.get("net_credit", 0),
        "max_loss":     trade.get("max_loss",   0),
        "max_profit":   trade.get("max_profit", 0),
        "order_id":     exec_result.get("order_id", "unknown"),
        "order_status": exec_result.get("order_status", "unknown"),
        "success":      exec_result.get("success", False),
    }

    # Strategy-specific fields
    if trade.get("strategy") == "Iron Condor":
        entry.update({
            "profit_zone_low":  trade.get("profit_zone_low"),
            "profit_zone_high": trade.get("profit_zone_high"),
            "put_short_strike": trade.get("put_short_strike"),
            "put_long_strike":  trade.get("put_long_strike"),
            "call_short_strike":trade.get("call_short_strike"),
            "call_long_strike": trade.get("call_long_strike"),
        })
    else:
        entry.update({
            "short_strike":         trade.get("short_strike"),
            "long_strike":          trade.get("long_strike"),
            "breakeven":            trade.get("breakeven"),
            "profit_target_close":  trade.get("profit_target_close"),
            "stop_loss_close":      trade.get("stop_loss_close"),
        })

    if exec_result.get("error"):
        entry["error"] = exec_result["error"]

    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"  📝 Trade logged → trade_log.jsonl")

    # Save to active_trades.json so position_monitor.py can manage exits
    if exec_result.get("success"):
        _save_active_trade(trade, exec_result, entry["date"])


def _save_active_trade(trade, exec_result, trade_date):
    """
    Append successfully executed trade to active_trades.json.
    position_monitor.py reads this to apply exit rules intraday.
    """
    active_path = os.path.join(os.path.dirname(__file__), "active_trades.json")
    active = []
    if os.path.exists(active_path):
        try:
            with open(active_path) as f:
                active = json.load(f)
        except Exception:
            active = []

    trade_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    strategy  = trade.get("strategy", "Unknown")

    record = {
        "trade_id":            trade_id,
        "strategy":            strategy,
        "symbol":              trade.get("symbol"),
        "expiration":          trade.get("expiration"),
        "entry_credit":        trade.get("net_credit"),
        "profit_target_debit": trade.get("profit_target_close"),
        "stop_loss_debit":     trade.get("stop_loss_close"),
        "order_id":            exec_result.get("order_id"),
        "entered_at":          datetime.now().isoformat(),
        "quantity":            trade.get("quantity", 1),
    }

    if strategy == "Iron Condor":
        record.update({
            "put_short_symbol":  trade.get("put_short_symbol"),
            "put_long_symbol":   trade.get("put_long_symbol"),
            "call_short_symbol": trade.get("call_short_symbol"),
            "call_long_symbol":  trade.get("call_long_symbol"),
            "put_credit":        trade.get("put_credit"),
            "call_credit":       trade.get("call_credit"),
        })
    else:
        record.update({
            "short_symbol": trade.get("short_symbol"),
            "long_symbol":  trade.get("long_symbol"),
        })

    active.append(record)
    with open(active_path, "w") as f:
        json.dump(active, f, indent=2)
    print(f"  📂 Active trade saved → active_trades.json (monitored for exits)")

# ─── SECTION 5: POSITION MONITOR ─────────────────────────────────────────────

def check_positions():
    """Fetch and display all open positions with unrealized P&L."""
    from datetime import timezone, timedelta
    now_et = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-4)))
    print(f"\n  OPEN POSITIONS — {now_et.strftime('%Y-%m-%d %H:%M ET')}")
    print(f"  {'─'*55}")

    if TEST_MODE:
        positions = MOCK_POSITIONS
        quote_map = {}
    else:
        # Positions from Sandbox API (paper account)
        # Tradier returns {"positions": "null"} (a string) when account is flat
        data           = get_account(f"/accounts/{ACCOUNT_ID}/positions")
        positions_raw  = data.get("positions", None)
        if not positions_raw or positions_raw == "null" or isinstance(positions_raw, str):
            positions = []
        else:
            pos = positions_raw.get("position", [])
            positions = [pos] if isinstance(pos, dict) else pos
        if positions:
            symbols_to_check = [p["symbol"] for p in positions]
            # Current prices from Production API (real-time)
            quote_data  = get_market("/markets/quotes", {"symbols": ",".join(symbols_to_check)})
            quotes_list = quote_data.get("quotes", {}).get("quote", [])
            if isinstance(quotes_list, dict):
                quotes_list = [quotes_list]
            quote_map = {q["symbol"]: q for q in quotes_list}
        else:
            quote_map = {}

    total_pnl = 0
    for p in positions:
        sym        = p["symbol"]
        qty        = p["quantity"]
        cost       = p["cost_basis"]
        current_q  = quote_map.get(sym, {})
        current_px = current_q.get("last", 0)
        mkt_value  = current_px * abs(qty) * 100
        pnl        = mkt_value - cost if qty > 0 else cost - mkt_value
        total_pnl += pnl

        pnl_icon = "🟢" if pnl > 0 else "🔴"
        print(f"  {pnl_icon} {sym:<30}  Qty: {qty:>3}  P&L: ${pnl:>8.2f}")

    print(f"  {'─'*55}")
    print(f"  Total Unrealized P&L: ${total_pnl:>8.2f}")

# ─── SECTION 6: ACCOUNT SUMMARY ──────────────────────────────────────────────

def account_summary():
    """Show account balances and buying power."""
    if TEST_MODE:
        bal = MOCK_BALANCES
    else:
        # Account balances from Sandbox API (paper account)
        data = get_account(f"/accounts/{ACCOUNT_ID}/balances")
        bal  = data.get("balances", {})

    # Tradier sandbox returns equity=0 and balance in total_cash — normalise both
    equity_raw = bal.get("total_equity", 0) or bal.get("equity", 0) or 0
    cash       = bal.get("total_cash",   0) or bal.get("cash",   0) or 0
    equity     = equity_raw if equity_raw > 0 else cash   # use cash when equity is 0
    bp         = bal.get("option_short_value", 0) or bal.get("option_buying_power", 0) or cash
    pnl_day    = bal.get("close_pl", 0) or 0

    # POC tracking: only closed options P&L matters — sandbox cash is $100K virtual
    poc_pnl     = float(pnl_day)
    poc_pnl_pct = (poc_pnl / STARTING_CAPITAL * 100) if STARTING_CAPITAL else 0

    print(f"\n  ACCOUNT SUMMARY")
    print(f"  {'─'*42}")
    print(f"  Virtual Cash (sandbox):  ${cash:>10.2f}")
    print(f"  Options Buying Power:    ${bp:>10.2f}")
    print(f"  ── POC Tracking vs. ${STARTING_CAPITAL:,.0f} benchmark ──────")
    print(f"  Closed Options P&L:      ${poc_pnl:>+10.2f}  ({poc_pnl_pct:>+.2f}%)")
    print(f"  Trades needed to goal:   grow $2K → target +20% = +$400")

# ─── SECTION 7: TELEGRAM NOTIFICATION ───────────────────────────────────────

def notify_telegram(scan_result, trade_result, exec_result=None):
    """
    Send a compact Telegram summary after each scan + autonomous execution.

    exec_result:
      {"success": True,  "order_id": "123", "order_status": "ok"}   → ✅ executed
      {"success": False, "error": "..."}                              → ❌ failed
      None                                                            → no trade

    Skipped when NO_NOTIFY is set (bot sends full output directly) or
    when Telegram credentials are missing.
    """
    if NO_NOTIFY:
        return

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id   = os.getenv("TELEGRAM_CHAT_ID",   "")
    if not bot_token or not chat_id:
        print("  ℹ️  Telegram: TELEGRAM_BOT_TOKEN/CHAT_ID not set — skipping notification")
        return

    vix      = scan_result.get("vix", 0)
    spy      = scan_result.get("spy", 0)
    strategy = scan_result.get("strategy", "pass")
    regime_icons = {
        "bull_put_spread":    "🟢 Neutral-Bullish — Bull Put Spread",
        "bear_call_spread":   "🟡 Bearish — Bear Call Spread",
        "iron_condor":        "🔵 Sideways — Iron Condor",
        "low_vix_secondary":  "🟡 Low VIX — Scanning QQQ/IWM",
        "cash":               "🔴 VIX > 30 — Cash only",
        "pass":               "⚪ VIX < 12 — No trade",
    }
    status_line = regime_icons.get(strategy, "⚪ No trade")

    if trade_result:
        strat_name = trade_result.get("strategy", "Spread")

        # Execution status header
        if exec_result and exec_result.get("success"):
            exec_header = f"✅ *Auto-Executed*  |  Order `{exec_result.get('order_id','?')}`  ({exec_result.get('order_status','?')})"
        elif exec_result:
            exec_header = f"❌ *Execution Failed*\n`{exec_result.get('error','Unknown error')[:120]}`"
        else:
            exec_header = "📋 *Trade Identified*"

        if strat_name == "Iron Condor":
            trade_lines = (
                f"\n\n{exec_header}\n"
                f"Iron Condor — {trade_result['symbol']}\n"
                f"PUT:  SHORT ${trade_result['put_short_strike']:.0f} / LONG ${trade_result['put_long_strike']:.0f}\n"
                f"CALL: SHORT ${trade_result['call_short_strike']:.0f} / LONG ${trade_result['call_long_strike']:.0f}\n"
                f"Profit zone: *${trade_result['profit_zone_low']:.0f} – ${trade_result['profit_zone_high']:.0f}*  exp {trade_result['expiration']}\n"
                f"Total credit: *${trade_result['net_credit']:.2f}*  |  Max loss: *${trade_result['max_loss']:.0f}*"
            )
        else:
            trade_lines = (
                f"\n\n{exec_header}\n"
                f"{strat_name} — {trade_result['symbol']}\n"
                f"SHORT ${trade_result['short_strike']:.0f} / LONG ${trade_result['long_strike']:.0f}  "
                f"exp {trade_result['expiration']}\n"
                f"Credit: *${trade_result['net_credit']:.2f}*  |  Max loss: *${trade_result['max_loss']:.0f}*\n"
                f"Breakeven: ${trade_result['breakeven']:.2f}"
            )
    else:
        trade_lines = "\n\n⏸ No trade today."

    from datetime import timezone, timedelta
    now_et = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-4)))
    msg = (
        f"📊 *Tradier Morning Scan*  —  {now_et.strftime('%b %d, %I:%M %p ET')}\n\n"
        f"SPY: *${spy:.2f}*   VIX: *{vix:.2f}*\n"
        f"Entry check: {status_line}"
        f"{trade_lines}"
    )

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
        if r.status_code == 200:
            print(f"  📱 Telegram notification sent (chat: {chat_id})")
        else:
            print(f"  ⚠️  Telegram notify HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"  ⚠️  Telegram notify failed: {e}")

# ─── MAIN ENTRY POINTS ───────────────────────────────────────────────────────

def full_morning_routine():
    """
    Run complete morning scan + strategy routing + trade construction.

    Decision tree:
      VIX > 30           → Cash only (extreme risk)
      VIX < 15           → Pass (IV too low, premiums too cheap)
      SPY change > -0.5% → Bull Put Spread (neutral-bullish)
      SPY change ≤ -0.5% → Bear Call Spread (bearish momentum)
    """
    scan      = morning_scan()
    account_summary()
    check_positions()

    # CPI / FOMC calendar skip check
    skip, reason = check_calendar_skip()
    if skip:
        print(f"\n  ⛔ CALENDAR SKIP — {reason}. Skipping new trade entries.")
        notify_telegram(scan, None, None)
        return None

    strategy  = scan.get("strategy", "pass")
    spy_price = scan["spy"]
    vix_level = scan.get("vix", 16.0)
    trade     = None

    exec_result = None

    qqq_price = scan["qqq"]
    iwm_price = scan.get("iwm", 0)

    # ── POSITION LIMIT GUARD ─────────────────────────────────────────────────
    active_path = os.path.join(os.path.dirname(__file__), "active_trades.json")
    active_count = 0
    if os.path.exists(active_path):
        try:
            with open(active_path) as _af:
                active_count = len(json.load(_af))
        except Exception:
            active_count = 0

    if active_count >= MAX_POSITIONS:
        print(f"\n  ⛔ Position limit reached ({active_count}/{MAX_POSITIONS} active trades open).")
        print(f"  Waiting for existing positions to close before entering new trade.")
        notify_telegram(scan, None, None)
        return None

    if strategy == "cash":
        print("\n  🔴 VIX > 30 — extreme volatility. Go to cash. No trade today.")
    elif strategy == "pass":
        print("\n  ⚪ IV universally low — no premium worth selling. No trade today.")
    elif strategy == "low_vix_secondary":
        # SPY IV too thin — QQQ and IWM carry structurally higher IV
        # Use same directional bias as SPY but on higher-beta underlyings
        spy_change = scan.get("spy_change", 0)
        if spy_change < -0.5:
            _construct = construct_bear_call_spread
            _label     = "Bear Call Spread"
        else:
            _construct = construct_bull_put_spread
            _label     = "Bull Put Spread"

        print(f"\n  📊 Low VIX — trying QQQ for {_label}...")
        trade = _construct(symbol="QQQ", spy_price=qqq_price, vix=vix_level)
        if not trade and iwm_price > 0:
            print(f"\n  🔄 QQQ no valid spread — trying IWM...")
            trade = _construct(symbol="IWM", spy_price=iwm_price, vix=vix_level)
        if not trade:
            print("\n  ⚪ QQQ and IWM: no valid spread at current IV levels either.")
    elif strategy == "iron_condor":
        print("\n  📊 Entering: Iron Condor — trying SPY first...")
        trade = construct_iron_condor(symbol="SPY", spy_price=spy_price, vix=vix_level)
        if not trade:
            print("\n  🔄 SPY no valid condor — trying QQQ as fallback...")
            trade = construct_iron_condor(symbol="QQQ", spy_price=qqq_price, vix=vix_level)
    elif strategy == "bull_put_spread":
        print("\n  📊 Entering: Bull Put Spread — trying SPY first...")
        trade = construct_bull_put_spread(symbol="SPY", spy_price=spy_price, vix=vix_level)
        if not trade:
            print("\n  🔄 SPY no valid spread — trying QQQ as fallback...")
            trade = construct_bull_put_spread(symbol="QQQ", spy_price=qqq_price, vix=vix_level)
    elif strategy == "bear_call_spread":
        print("\n  📊 Entering: Bear Call Spread — trying SPY first...")
        trade = construct_bear_call_spread(symbol="SPY", spy_price=spy_price, vix=vix_level)
        if not trade:
            print("\n  🔄 SPY no valid spread — trying QQQ as fallback...")
            trade = construct_bear_call_spread(symbol="QQQ", spy_price=qqq_price, vix=vix_level)

    # ── AUTONOMOUS EXECUTION ─────────────────────────────────────────────────
    if trade:
        print("\n  ⚡ AUTO-EXECUTING TRADE (no approval required)...")
        exec_result = auto_execute_trade()
        log_trade_activity(trade, exec_result)

    notify_telegram(scan, trade, exec_result)
    return trade

if __name__ == "__main__":
    if "--positions" in sys.argv:
        check_positions()
    elif "--construct" in sys.argv:
        construct_bull_put_spread()
    elif "--account" in sys.argv:
        account_summary()
    elif "--execute" in sys.argv:
        trade = construct_bull_put_spread()
        if trade:
            execute_trade(trade)
    else:
        full_morning_routine()

    print("\n" + "═" * 60 + "\n")
