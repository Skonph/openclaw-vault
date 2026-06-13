#!/usr/bin/env python3
"""
OpenClaw Scanner v2.0
Runs nightly via cron. Scans candidates for qualifying bull call and bear put spreads.
Outputs JSON snapshot for vault_updater.py to consume.

Fixes applied (vs v1):
- alerts/holds scope bug fixed (was inside has_active_position block → NameError)
- PRICE_MAX 30 → 40, DTE_MAX 70 → 40, IV_LAST_MAX added (45), CONVICTION_MIN 70 → 75
- Single KNOWN_EVENTS dict (was duplicated)
- expiry stored in spread dict (was missing → blank DTE field in briefings)
- IV Last absolute check added to analyze_spread()
- bid-ask spread check per leg added
- analyze_bear_put_spread() added
- SECTOR_ETFS dict added for per-ticker ETF direction check
- SPY added to macro tickers
- market_ok uses VIX < VIX_MAX AND spy_chg > -1.5
"""

import json
import os
import requests
import sys
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv('/home/ubuntu/trading-bot/.env')

# ─── Directories ────────────────────────────────────────────────────────────────
LOGS_DIR = Path('/home/ubuntu/trading-bot/logs/snapshots')
CANDIDATES_FILE = Path('/home/ubuntu/trading-bot/candidates.txt')
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# ─── API ─────────────────────────────────────────────────────────────────────────
TRADIER_TOKEN = os.environ.get('TRADIER_TOKEN', '')
TRADIER_BASE = 'https://api.tradier.com/v1'
TRADIER_HEADERS = {
    'Authorization': f'Bearer {TRADIER_TOKEN}',
    'Accept': 'application/json'
}

# ─── Rules (v4.0) ────────────────────────────────────────────────────────────────
PRICE_MIN = 10.0
PRICE_MAX = 40.0          # was 30.0 — expanded May 22
IV_RANK_MAX = 40.0
IV_LAST_MAX = 45.0        # L019 — absolute IV cap (was missing)
PREMIUM_MIN = 0.30
PREMIUM_MAX = 0.60
SPREAD_WIDTH_MAX = 3.0
DTE_MIN = 25
DTE_MAX = 40              # was 70 — corrected to match ruleset
OI_MIN = 500
BID_ASK_MAX = 0.10        # per leg
VIX_MAX = 20.0
SPY_DROP_MAX = -1.5       # % — market condition filter
CONVICTION_MIN = 75       # was 70 — raised May 22

# ─── Known holds (scanner suppresses these until recheck date) ────────────────────
KNOWN_HOLDS = {
    'PR': '2026-06-17',   # dividend Jun 16 — recheck Jun 17
}

# ─── Known events (auto-deductions applied to conviction score) ───────────────────
# Single definition — do not duplicate
KNOWN_EVENTS = {
    # 'TICKER': [{'date': 'YYYY-MM-DD', 'type': 'dividend', 'deduction': 5}]
}

# ─── Sector ETF mapping ───────────────────────────────────────────────────────────
SECTOR_ETFS = {
    'XLY': ['CCL', 'NCLH', 'MAT'],          # Consumer Discretionary
    'XLI': ['AAL', 'PUMP'],                  # Industrials
    'XLE': ['PR', 'VALE', 'SBSW'],           # Energy / Materials overlap — use XLE for energy
    'XLB': ['VALE', 'SBSW', 'ECVT'],         # Materials
    'XLC': [],                                # Communication Services
    'XLF': ['FNB', 'VLY'],                   # Financials
}

# Reverse map: ticker → ETF
TICKER_TO_ETF = {}
for etf, tickers in SECTOR_ETFS.items():
    for t in tickers:
        TICKER_TO_ETF[t] = etf

# ─── Macro tickers to track ───────────────────────────────────────────────────────
MACRO_TICKERS = ['VIX', 'SPY', 'XLE', 'XLY', 'XLI', 'XLB', 'XLC', 'XLF']

# ─── Active positions (set manually when a trade is open) ────────────────────────
# Format: [{'symbol': 'AAL', 'long_strike': 12, 'short_strike': 13, 'expiry': '2026-06-18'}]
ACTIVE_POSITIONS = []


# ─── Helpers ─────────────────────────────────────────────────────────────────────

def has_active_position():
    return len(ACTIVE_POSITIONS) > 0


def load_candidates():
    """Read candidates.txt, strip comments and blanks."""
    if not CANDIDATES_FILE.exists():
        print("⚠️  candidates.txt not found — using default watchlist")
        return ['CCL', 'NCLH', 'AAL', 'VALE']
    tickers = []
    with open(CANDIDATES_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                tickers.append(line.upper())
    return tickers


def is_known_hold(symbol):
    """Returns (True, reason) if ticker is suppressed, else (False, '')."""
    if symbol in KNOWN_HOLDS:
        recheck = KNOWN_HOLDS[symbol]
        today = datetime.now().strftime('%Y-%m-%d')
        if today < recheck:
            return True, f"KNOWN_HOLD until {recheck}"
    return False, ''


def get_quote(symbol):
    """Fetch quote for a single symbol. Returns dict with price, change_pct."""
    try:
        r = requests.get(
            f"{TRADIER_BASE}/markets/quotes",
            headers=TRADIER_HEADERS,
            params={'symbols': symbol, 'greeks': 'false'},
            timeout=10
        )
        if r.status_code != 200:
            return None
        data = r.json().get('quotes', {}).get('quote', {})
        if isinstance(data, list):
            data = data[0]
        if not data:
            return None
        price = data.get('last') or data.get('close')
        prev = data.get('prevclose') or data.get('close')
        change_pct = round(((price - prev) / prev) * 100, 2) if prev else 0
        return {'price': price, 'change_pct': change_pct}
    except Exception as e:
        print(f"  ⚠️  Quote error {symbol}: {e}")
        return None


def get_expirations(symbol):
    """Fetch available option expiration dates for symbol."""
    try:
        r = requests.get(
            f"{TRADIER_BASE}/markets/options/expirations",
            headers=TRADIER_HEADERS,
            params={'symbol': symbol, 'includeAllRoots': 'true', 'strikes': 'false'},
            timeout=10
        )
        if r.status_code != 200:
            return []
        dates = r.json().get('expirations', {}).get('date', [])
        if isinstance(dates, str):
            dates = [dates]
        return dates or []
    except Exception as e:
        print(f"  ⚠️  Expirations error {symbol}: {e}")
        return []


def get_options_chain(symbol, expiry):
    """Fetch full options chain for symbol+expiry."""
    try:
        r = requests.get(
            f"{TRADIER_BASE}/markets/options/chains",
            headers=TRADIER_HEADERS,
            params={'symbol': symbol, 'expiration': expiry, 'greeks': 'true'},
            timeout=10
        )
        if r.status_code != 200:
            return []
        options = r.json().get('options', {}).get('option', [])
        if isinstance(options, dict):
            options = [options]
        return options or []
    except Exception as e:
        print(f"  ⚠️  Chain error {symbol} {expiry}: {e}")
        return []


def get_etf_above_ema20(etf_symbol):
    """
    Check if ETF price is above its EMA(20).
    Uses 30-day daily history to compute EMA(20).
    Returns True if above EMA20, False if below, None if data unavailable.
    """
    try:
        r = requests.get(
            f"{TRADIER_BASE}/markets/history",
            headers=TRADIER_HEADERS,
            params={
                'symbol': etf_symbol,
                'interval': 'daily',
                'start': (datetime.now() - timedelta(days=45)).strftime('%Y-%m-%d'),
                'end': datetime.now().strftime('%Y-%m-%d'),
            },
            timeout=10
        )
        if r.status_code != 200:
            return None
        history = r.json().get('history', {}).get('day', [])
        if not history or len(history) < 20:
            return None
        closes = [float(d['close']) for d in history[-20:]]
        # Simple EMA(20)
        multiplier = 2 / (20 + 1)
        ema = closes[0]
        for c in closes[1:]:
            ema = (c - ema) * multiplier + ema
        current_price = closes[-1]
        return current_price > ema
    except Exception as e:
        print(f"  ⚠️  ETF EMA check error {etf_symbol}: {e}")
        return None


def calc_dte(expiry_str):
    """Days to expiry from today."""
    try:
        expiry_dt = datetime.strptime(expiry_str, '%Y-%m-%d')
        return (expiry_dt - datetime.now()).days
    except Exception:
        return 0


def find_option(chain, option_type, strike):
    """Find a specific option in chain by type and strike."""
    for o in chain:
        if o.get('option_type', '').lower() == option_type.lower():
            if abs(float(o.get('strike', 0)) - float(strike)) < 0.01:
                return o
    return None


# ─── Bull Call Spread Analysis ────────────────────────────────────────────────────

def analyze_spread(symbol, price, expiry, chain):
    """
    Scan chain for qualifying bull call spreads.
    Returns list of qualifying spread dicts.
    """
    dte = calc_dte(expiry)
    if not (DTE_MIN <= dte <= DTE_MAX):
        return []

    calls = [o for o in chain if o.get('option_type', '').lower() == 'call']
    if not calls:
        return []

    qualifying = []

    for i, long_opt in enumerate(calls):
        long_strike = float(long_opt.get('strike', 0))
        if long_strike < price * 0.95 or long_strike > price * 1.15:
            continue  # only near-ATM strikes

        long_bid = float(long_opt.get('bid', 0) or 0)
        long_ask = float(long_opt.get('ask', 0) or 0)
        long_oi = int(long_opt.get('open_interest', 0) or 0)
        long_iv = round(float(long_opt.get('greeks', {}).get('smv_vol', 0) or 0) * 100, 1)

        # Auto-reject checks on long leg
        if long_bid <= 0:
            continue
        if long_oi < OI_MIN:
            continue
        if long_iv > IV_LAST_MAX:
            continue
        if (long_ask - long_bid) > BID_ASK_MAX:
            continue

        # Find short leg (next 1-3 strikes up)
        for short_opt in calls[i+1:i+4]:
            short_strike = float(short_opt.get('strike', 0))
            width = short_strike - long_strike
            if width <= 0 or width > SPREAD_WIDTH_MAX:
                continue

            short_bid = float(short_opt.get('bid', 0) or 0)
            short_ask = float(short_opt.get('ask', 0) or 0)
            short_oi = int(short_opt.get('open_interest', 0) or 0)
            short_iv = round(float(short_opt.get('greeks', {}).get('smv_vol', 0) or 0) * 100, 1)

            if short_bid <= 0:
                continue
            if short_oi < OI_MIN:
                continue
            if short_iv > IV_LAST_MAX:
                continue
            if (short_ask - short_bid) > BID_ASK_MAX:
                continue

            # Net debit (mid prices)
            long_mid = round((long_bid + long_ask) / 2, 2)
            short_mid = round((short_bid + short_ask) / 2, 2)
            spread_mid = round(long_mid - short_mid, 2)

            if not (PREMIUM_MIN <= spread_mid <= PREMIUM_MAX):
                continue

            max_profit = round(width - spread_mid, 2)
            if max_profit <= 0:
                continue
            rr = round(max_profit / spread_mid, 1)

            qualifying.append({
                'symbol': symbol,
                'spread_type': 'bull_call',
                'price': round(price, 2),
                'expiry': expiry,
                'dte': dte,
                'long_strike': long_strike,
                'short_strike': short_strike,
                'long_bid': long_bid,
                'long_ask': long_ask,
                'short_bid': short_bid,
                'short_ask': short_ask,
                'long_oi': long_oi,
                'short_oi': short_oi,
                'long_iv': long_iv,
                'short_iv': short_iv,
                'spread_mid': spread_mid,
                'max_profit': max_profit,
                'rr': rr,
            })

    return qualifying


# ─── Bear Put Spread Analysis ─────────────────────────────────────────────────────

def analyze_bear_put_spread(symbol, price, expiry, chain):
    """
    Scan chain for qualifying bear put spreads.
    Buy higher strike put + sell lower strike put.
    Returns list of qualifying spread dicts.
    """
    dte = calc_dte(expiry)
    if not (DTE_MIN <= dte <= DTE_MAX):
        return []

    puts = [o for o in chain if o.get('option_type', '').lower() == 'put']
    if not puts:
        return []

    # Sort puts descending by strike (high strike first = long leg)
    puts_sorted = sorted(puts, key=lambda o: float(o.get('strike', 0)), reverse=True)

    qualifying = []

    for i, long_opt in enumerate(puts_sorted):
        long_strike = float(long_opt.get('strike', 0))
        if long_strike < price * 0.85 or long_strike > price * 1.05:
            continue  # only near-ATM strikes

        long_bid = float(long_opt.get('bid', 0) or 0)
        long_ask = float(long_opt.get('ask', 0) or 0)
        long_oi = int(long_opt.get('open_interest', 0) or 0)
        long_iv = round(float(long_opt.get('greeks', {}).get('smv_vol', 0) or 0) * 100, 1)

        if long_bid <= 0:
            continue
        if long_oi < OI_MIN:
            continue
        if long_iv > IV_LAST_MAX:
            continue
        if (long_ask - long_bid) > BID_ASK_MAX:
            continue

        # Find short leg (next 1-3 strikes down)
        for short_opt in puts_sorted[i+1:i+4]:
            short_strike = float(short_opt.get('strike', 0))
            width = long_strike - short_strike
            if width <= 0 or width > SPREAD_WIDTH_MAX:
                continue

            short_bid = float(short_opt.get('bid', 0) or 0)
            short_ask = float(short_opt.get('ask', 0) or 0)
            short_oi = int(short_opt.get('open_interest', 0) or 0)
            short_iv = round(float(short_opt.get('greeks', {}).get('smv_vol', 0) or 0) * 100, 1)

            if short_bid <= 0:
                continue
            if short_oi < OI_MIN:
                continue
            if short_iv > IV_LAST_MAX:
                continue
            if (short_ask - short_bid) > BID_ASK_MAX:
                continue

            long_mid = round((long_bid + long_ask) / 2, 2)
            short_mid = round((short_bid + short_ask) / 2, 2)
            spread_mid = round(long_mid - short_mid, 2)

            if not (PREMIUM_MIN <= spread_mid <= PREMIUM_MAX):
                continue

            max_profit = round(width - spread_mid, 2)
            if max_profit <= 0:
                continue
            rr = round(max_profit / spread_mid, 1)

            qualifying.append({
                'symbol': symbol,
                'spread_type': 'bear_put',
                'price': round(price, 2),
                'expiry': expiry,
                'dte': dte,
                'long_strike': long_strike,
                'short_strike': short_strike,
                'long_bid': long_bid,
                'long_ask': long_ask,
                'short_bid': short_bid,
                'short_ask': short_ask,
                'long_oi': long_oi,
                'short_oi': short_oi,
                'long_iv': long_iv,
                'short_iv': short_iv,
                'spread_mid': spread_mid,
                'max_profit': max_profit,
                'rr': rr,
            })

    return qualifying


# ─── Main Scan ────────────────────────────────────────────────────────────────────

def run_daily_scan():
    print(f"\n{'='*50}")
    print(f"OPENCLAW SCANNER v2.0 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}\n")

    # ── CRITICAL: initialize alerts/holds BEFORE the has_active_position check ──
    # (v1 had these inside the if block → NameError crash when ACTIVE_POSITIONS=[])
    alerts = []
    holds = []

    # ── Step 1: Skip scan if position already open ──
    if has_active_position():
        print("⚠️  Active position open — monitor stop levels, skip new entries")
        for pos in ACTIVE_POSITIONS:
            holds.append(f"{pos['symbol']}: active position open")

    # ── Step 2: Fetch macro data ──
    print("📊 Fetching macro indicators...")
    macro = {}
    vix_price = None
    spy_chg = 0.0

    for sym in MACRO_TICKERS:
        q = get_quote(sym)
        if q:
            macro[sym] = q
            print(f"  {sym}: ${q['price']} ({q['change_pct']}%)")
            if sym == 'VIX':
                vix_price = q['price']
            if sym == 'SPY':
                spy_chg = q['change_pct']
        else:
            print(f"  {sym}: N/A")

    # ── Step 3: Market condition check ──
    market_ok = True
    if vix_price is not None and vix_price >= VIX_MAX:
        print(f"\n⚠️  VIX {vix_price} ≥ {VIX_MAX} — elevated risk")
        market_ok = False
    if spy_chg <= SPY_DROP_MAX:
        print(f"\n⚠️  SPY {spy_chg}% — broad market selloff, no new bull entries")
        market_ok = False

    if not market_ok:
        print("🚫 Market condition: ELEVATED — skipping new entries")
    else:
        print(f"\n✅ Market condition: OK (VIX {vix_price}, SPY {spy_chg}%)")

    # ── Step 4: Fetch ETF EMA positions ──
    print("\n📈 Checking sector ETF direction...")
    etf_above_ema = {}
    for etf in SECTOR_ETFS.keys():
        result = get_etf_above_ema20(etf)
        etf_above_ema[etf] = result
        status = '↑ above EMA20' if result else ('↓ below EMA20' if result is False else '? N/A')
        print(f"  {etf}: {status}")

    # ── Step 5: Skip scan if market not OK and no active positions ──
    if not market_ok and not has_active_position():
        print("\n🚫 Market not OK — no scan today")
        snapshot = {
            'scan_time': datetime.now().isoformat(),
            'market_ok': False,
            'macro': macro,
            'alerts': [],
            'holds': ['Market condition elevated — no scan'],
        }
        _write_snapshot(snapshot)
        return snapshot

    # ── Step 6: Load candidates ──
    candidates = load_candidates()
    print(f"\n📋 Candidates: {', '.join(candidates)}")

    # ── Step 7: Scan each ticker ──
    print(f"\n{'─'*50}")
    for symbol in candidates:

        # Check KNOWN_HOLD
        is_hold, hold_reason = is_known_hold(symbol)
        if is_hold:
            print(f"\n⏳ {symbol}: {hold_reason}")
            holds.append(f"{symbol}: {hold_reason}")
            continue

        # Fetch quote
        q = get_quote(symbol)
        if not q or not q.get('price'):
            print(f"\n❌ {symbol}: price unavailable — auto-fail")
            holds.append(f"{symbol}: price unavailable")
            continue

        price = q['price']
        print(f"\n🔍 {symbol}: ${price} ({q['change_pct']}%)")

        # Price range check
        if not (PRICE_MIN <= price <= PRICE_MAX):
            print(f"  ❌ Price ${price} outside ${PRICE_MIN}–${PRICE_MAX}")
            holds.append(f"{symbol}: price ${price} outside range")
            continue

        # Daily move check (>±3% = reject)
        if abs(q['change_pct']) > 3.0:
            print(f"  ❌ Daily move {q['change_pct']}% exceeds ±3%")
            holds.append(f"{symbol}: daily move {q['change_pct']}% too large")
            continue

        # Determine sector ETF and direction
        etf = TICKER_TO_ETF.get(symbol)
        bull_ok = True
        bear_ok = True

        if etf:
            above_ema = etf_above_ema.get(etf)
            if above_ema is True:
                bear_ok = False   # uptrend — bulls only
                print(f"  📈 {etf} above EMA20 — bull call only")
            elif above_ema is False:
                bull_ok = False   # downtrend — bears only
                print(f"  📉 {etf} below EMA20 — bear put only")
            else:
                print(f"  ❓ {etf} direction unknown — scan both")
        else:
            print(f"  ℹ️  No ETF mapping for {symbol} — scan both")

        # Fetch expirations
        expirations = get_expirations(symbol)
        if not expirations:
            print(f"  ❌ No expirations found")
            holds.append(f"{symbol}: no options expirations")
            continue

        # Filter to DTE window
        valid_expiries = [
            e for e in expirations
            if DTE_MIN <= calc_dte(e) <= DTE_MAX
        ]
        if not valid_expiries:
            print(f"  ❌ No expirations in {DTE_MIN}–{DTE_MAX} DTE window")
            holds.append(f"{symbol}: no qualifying expiry (DTE range)")
            continue

        found_any = False

        for expiry in valid_expiries:
            dte = calc_dte(expiry)
            print(f"  📅 {expiry} ({dte} DTE)...")

            chain = get_options_chain(symbol, expiry)
            if not chain:
                print(f"    ❌ Empty chain")
                continue

            # Bull call spreads
            if market_ok and bull_ok:
                bull_spreads = analyze_spread(symbol, price, expiry, chain)
                for spread in bull_spreads:
                    print(f"    ✅ BULL CALL: ${spread['long_strike']}/${spread['short_strike']} "
                          f"mid=${spread['spread_mid']} R:R={spread['rr']}:1 "
                          f"IV={spread['long_iv']}%/{spread['short_iv']}%")
                    alerts.append(spread)
                    found_any = True

            # Bear put spreads (only on downtrending sector)
            if bear_ok:
                bear_spreads = analyze_bear_put_spread(symbol, price, expiry, chain)
                for spread in bear_spreads:
                    print(f"    ✅ BEAR PUT: ${spread['long_strike']}/${spread['short_strike']} "
                          f"mid=${spread['spread_mid']} R:R={spread['rr']}:1 "
                          f"IV={spread['long_iv']}%/{spread['short_iv']}%")
                    alerts.append(spread)
                    found_any = True

        if not found_any:
            print(f"  — No qualifying spread")
            holds.append(f"{symbol}: no qualifying spread")

    # ── Step 8: Deduplicate alerts (keep best R:R per ticker+type) ──
    seen = {}
    deduped_alerts = []
    for a in alerts:
        key = f"{a['symbol']}_{a['spread_type']}"
        if key not in seen or a['rr'] > seen[key]['rr']:
            seen[key] = a
    deduped_alerts = list(seen.values())

    # ── Step 9: Write snapshot JSON ──
    snapshot = {
        'scan_time': datetime.now().isoformat(),
        'market_ok': market_ok,
        'macro': macro,
        'alerts': deduped_alerts,
        'holds': holds,
    }
    _write_snapshot(snapshot)

    # ── Step 10: Print summary ──
    print(f"\n{'='*50}")
    print(f"SCAN COMPLETE")
    print(f"Alerts: {len(deduped_alerts)} | Holds: {len(holds)}")
    if deduped_alerts:
        print("\n🚨 QUALIFYING SPREADS:")
        for a in deduped_alerts:
            t = "BULL CALL" if a['spread_type'] == 'bull_call' else "BEAR PUT"
            print(f"  {a['symbol']} ${a['long_strike']}/{a['short_strike']} "
                  f"{a.get('expiry','')} — {t} — mid=${a['spread_mid']} R:R={a['rr']}")
    print(f"{'='*50}\n")

    return snapshot


def _write_snapshot(snapshot):
    """Write snapshot JSON to logs directory."""
    filename = LOGS_DIR / f"scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, 'w') as f:
        json.dump(snapshot, f, indent=2)
    print(f"\n💾 Snapshot: {filename.name}")


if __name__ == '__main__':
    run_daily_scan()
