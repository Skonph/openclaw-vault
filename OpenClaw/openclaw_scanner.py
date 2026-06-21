#!/usr/bin/env python3
"""
OpenClaw Scanner v3.0
Runs nightly via cron at 21:05 Bangkok (US market hours).

Pipeline:
  1. Fetch macro: VIX, SPY, sector ETFs
  2. Market condition gate (VIX < 20, SPY > -1.5%)
  3. Sector ETF EMA20 direction check
  4. Scan candidates for bull call / bear put spreads (IV/OI/DTE/bid-ask rules)
  5. Events check per alert   — Tradier fundamentals/calendars (±14 day ban)
  6. Conviction scoring       — offline rule-based (upgrades to Claude API if key present)
  7. Pending approval write   — approval_manager.write_pending() for conviction ≥75 + events ok
  8. Snapshot JSON write      — for vault_updater.py to consume
  9. Position monitor         — checks Alpaca open positions for stop/DTE alerts

v3 changes vs v2:
  - Imports events_checker, conviction_scorer, approval_manager, position_monitor
  - Steps 5-7 added after spread deduplication
  - etf_above_ema20 field written into each spread dict (for conviction scorer)
  - Events status + conviction score stored in snapshot JSON
  - approval_manager.expire_old_orders() called at startup
  - position_monitor.run() called at end of scan
"""

import json
import os
import sys
import requests
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

# Try multiple possible .env paths for development flexibility
for env_path in ['/home/ubuntu/openclaw/.env', str(Path(__file__).parent / '.env'), str(Path(__file__).parent / '.env.local')]:
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

# ─── Local module imports ─────────────────────────────────────────────────────
# Add openclaw dir to path so modules resolve regardless of cwd
sys.path.insert(0, str(Path(__file__).parent))
from events_checker   import check_events
from conviction_scorer import score_conviction
from approval_manager import write_pending, expire_old_orders
import position_monitor

# ─── Directories ──────────────────────────────────────────────────────────────
if os.path.exists('/home/ubuntu/openclaw'):
    LOGS_DIR        = Path('/home/ubuntu/openclaw/logs/snapshots')
    CANDIDATES_FILE = Path('/home/ubuntu/openclaw/candidates.txt')
    VAULT_DIR       = Path('/home/ubuntu/openclaw-vault')
else:
    LOGS_DIR        = Path(__file__).parent / 'logs' / 'snapshots'
    CANDIDATES_FILE = Path(__file__).parent / 'candidates.txt'
    VAULT_DIR       = Path(__file__).parent.parent

LOGS_DIR.mkdir(parents=True, exist_ok=True)

# ─── API ──────────────────────────────────────────────────────────────────────
TRADIER_TOKEN   = os.environ.get('TRADIER_API_KEY', '') or os.environ.get('TRADIER_TOKEN', '')
TRADIER_BASE    = 'https://api.tradier.com/v1'
TRADIER_HEADERS = {
    'Authorization': f'Bearer {TRADIER_TOKEN}',
    'Accept': 'application/json',
}

# ─── Rules (v4.0) ─────────────────────────────────────────────────────────────
PRICE_MIN       = 10.0
PRICE_MAX       = 100.0        # expanded May 22, raised to 100 Jun 19
IV_RANK_MAX     = 40.0
IV_LAST_MAX     = 45.0        # L019 — absolute IV cap
PREMIUM_MIN     = 0.30
PREMIUM_MAX     = 0.60
SPREAD_WIDTH_MAX= 3.0
DTE_MIN         = 25
DTE_MAX         = 50    # raised from 40 — captures standard monthly expiry (~45 DTE)
OI_MIN          = 300         # lowered to 300 to broaden candidate pool
BID_ASK_MAX     = 0.15        # widened to 0.15 for better fill liquidity
VIX_MAX         = 20.0
VIX_HARD_STOP   = 30.0        # above this = cash only, all strategies blocked
VIX_IC_MIN      = 18.0        # minimum VIX for Iron Condor (need elevated IV to sell)
SPY_DROP_MAX    = -1.5        # % — market condition filter
SPY_FLAT_RANGE  = 0.5         # ±% — flat market threshold for IC regime
IC_CREDIT_MIN   = 0.30        # minimum net credit for Iron Condor
CONVICTION_MIN  = 75
DAILY_MOVE_MAX  = 5.0         # ±% — raised from 3% to allow normal volatile days

# ─── Known holds ──────────────────────────────────────────────────────────────
KNOWN_HOLDS = {
    'PR': '2026-06-17',   # dividend Jun 16 — recheck Jun 17
}

# ─── Known events — hard blocks (earnings/dividend within ±14 days of entry OR expiry) ──
# Format: 'TICKER': 'YYYY-MM-DD'  — use ex-dividend date (not payment), or earnings date
KNOWN_EVENTS = {
    'GME': '2026-06-10',   # Q1 FY2026 earnings Jun 9-10
    'CMG': '2026-07-29',   # Q2 earnings Jul 29 — within 14d of Jul 17 expiry
    'PBR': '2026-06-08',   # ex-dividend date ~Jun 8 (payment Jun 22) — recheck Jun 23
}

# ─── Known clear overrides — verified events outside ±14d window ──────────────
# Tradier returns UNCERTAIN for these but we've manually verified they're safe
KNOWN_CLEAR = {
    'GAP': '2026-08-20',   # Q2 earnings Aug 20 — safe for any expiry before Aug 6
}

# ─── Sector ETF mapping ───────────────────────────────────────────────────────
SECTOR_ETFS = {
    'XLY': ['CCL', 'NCLH', 'MAT', 'CZR', 'GAP', 'CMG', 'GME', 'HMC', 'CPNG', 'XPEV', 'LI'],
    'XLI': ['AAL', 'PUMP', 'AMTM'],
    'XLE': ['PR', 'VALE', 'SBSW', 'PBR', 'BTU', 'SOC', 'SM'],
    'XLB': ['VALE', 'SBSW', 'ECVT', 'UUUU', 'SSRM'],
    'XLC': ['SIRI'],
    'XLF': ['FNB', 'VLY', 'BEKE', 'GLXY'],
    'XLK': ['GEN', 'S', 'BTDR', 'BMNR', 'BZ', 'MBLY', 'TOST'],
    'XLV': ['OSCR'],
}
TICKER_TO_ETF = {t: etf for etf, tickers in SECTOR_ETFS.items() for t in tickers}

MACRO_TICKERS = ['VIX', 'SPY', 'XLE', 'XLY', 'XLI', 'XLB', 'XLC', 'XLF', 'XLK', 'XLV']

ACTIVE_POSITIONS = [
    {'symbol': 'TOST'},  # Iron Condor, exp 2026-07-17, opened 2026-06-08
                         # (unrealized -$44 as of 2026-06-13 — see 04_Trade_Journal.md Trade 5)
]   # set manually when a trade is open; cleared when position closes


# ─── Market regime ────────────────────────────────────────────────────────────

def determine_regime(spy_chg: float, vix_price) -> str:
    """
    Classify market into a regime to select the right spread strategy.

    Returns:
      'cash'           — VIX > 30, all strategies blocked
      'flat_elevated'  — SPY ±0.5% AND VIX ≥ 18  → Iron Condor zone
      'flat_low'       — SPY ±0.5% AND VIX < 18   → Bull Call (conservative default)
      'bull'           — SPY > +0.5%               → Bull Call Spread
      'bear'           — SPY < -0.5%               → Bear Put Spread
      'unknown'        — VIX data unavailable       → scan both debit spreads
    """
    if vix_price is None:
        return 'unknown'
    if vix_price > VIX_HARD_STOP:
        return 'cash'
    if abs(spy_chg) <= SPY_FLAT_RANGE:
        return 'flat_elevated' if vix_price >= VIX_IC_MIN else 'flat_low'
    return 'bull' if spy_chg > SPY_FLAT_RANGE else 'bear'


def _macro_quote_from_context(sym, ctx_quotes):
    """
    Return a get_quote-shaped dict {'price', 'change_pct'} for `sym` from the
    shared market_context.json quotes block, or None if the symbol isn't present
    (caller then falls back to a live get_quote). Pure + testable.

    Lets OpenClaw decide off the SAME VIX/SPY snapshot as Tradier/guardrail and
    skip those redundant per-ticker fetches when the context is fresh.
    """
    cq = (ctx_quotes or {}).get(sym)
    if not cq or cq.get('last') is None:
        return None
    return {'price': cq['last'], 'change_pct': cq.get('change_pct', 0)}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def has_active_position():
    return len(ACTIVE_POSITIONS) > 0


def _load_cooling_off() -> dict:
    """Read cooling-off dict from pending_orders.json."""
    pending_file = VAULT_DIR / 'OpenClaw/pending_orders.json'
    try:
        if pending_file.exists():
            return json.loads(pending_file.read_text()).get('cooling_off', {})
    except Exception:
        pass
    return {}


def load_candidates():
    """Load candidates, filtering out tickers in cooling-off period."""
    if not CANDIDATES_FILE.exists():
        print('⚠️  candidates.txt not found — using default watchlist')
        return ['CCL', 'NCLH', 'AAL', 'VALE']

    today       = datetime.now().strftime('%Y-%m-%d')
    cooling_off = _load_cooling_off()
    tickers     = []
    skipped     = []

    with open(CANDIDATES_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            symbol = line.upper()
            if symbol in cooling_off and cooling_off[symbol] >= today:
                skipped.append(f'{symbol} (until {cooling_off[symbol]})')
                continue
            tickers.append(symbol)

    if skipped:
        print(f'  ⏸ Cooling-off: {", ".join(skipped)}')
    return tickers


def is_known_hold(symbol: str) -> tuple:
    """
    Check KNOWN_HOLDS. Auto-clears hold when recheck date passes.
    Returns (is_hold: bool, reason: str)
    """
    if symbol not in KNOWN_HOLDS:
        return False, ''
    recheck = KNOWN_HOLDS[symbol]
    today   = datetime.now().strftime('%Y-%m-%d')
    if today < recheck:
        return True, f'KNOWN_HOLD until {recheck}'
    # Recheck date passed — auto-clear
    print(f'  ✅ {symbol} KNOWN_HOLD cleared — {recheck} passed, scanning normally')
    del KNOWN_HOLDS[symbol]
    return False, ''


def get_quote(symbol):
    try:
        r = requests.get(
            f'{TRADIER_BASE}/markets/quotes',
            headers=TRADIER_HEADERS,
            params={'symbols': symbol, 'greeks': 'false'},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data = r.json().get('quotes', {}).get('quote', {})
        if isinstance(data, list):
            data = data[0]
        if not data:
            return None
        price = data.get('last') or data.get('prevclose') or data.get('close')
        prev  = data.get('prevclose') or data.get('close')
        chg   = round(((price - prev) / prev) * 100, 2) if prev else 0
        return {'price': price, 'change_pct': chg}
    except Exception as e:
        print(f'  ⚠️  Quote error {symbol}: {e}')
        return None


def get_expirations(symbol):
    try:
        r = requests.get(
            f'{TRADIER_BASE}/markets/options/expirations',
            headers=TRADIER_HEADERS,
            params={'symbol': symbol, 'includeAllRoots': 'true', 'strikes': 'false'},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        dates = r.json().get('expirations', {}).get('date', [])
        if isinstance(dates, str):
            dates = [dates]
        return dates or []
    except Exception as e:
        print(f'  ⚠️  Expirations error {symbol}: {e}')
        return []


def get_options_chain(symbol, expiry):
    try:
        r = requests.get(
            f'{TRADIER_BASE}/markets/options/chains',
            headers=TRADIER_HEADERS,
            params={'symbol': symbol, 'expiration': expiry, 'greeks': 'true'},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        options = r.json().get('options', {}).get('option', [])
        if isinstance(options, dict):
            options = [options]
        return options or []
    except Exception as e:
        print(f'  ⚠️  Chain error {symbol} {expiry}: {e}')
        return []


def get_etf_above_ema20(etf_symbol):
    """Returns True if ETF is above EMA(20), False if below, None if unavailable."""
    try:
        r = requests.get(
            f'{TRADIER_BASE}/markets/history',
            headers=TRADIER_HEADERS,
            params={
                'symbol':   etf_symbol,
                'interval': 'daily',
                'start':    (datetime.now() - timedelta(days=45)).strftime('%Y-%m-%d'),
                'end':      datetime.now().strftime('%Y-%m-%d'),
            },
            timeout=10,
        )
        if r.status_code != 200:
            return None
        history = r.json().get('history', {}).get('day', [])
        if not history or len(history) < 20:
            return None
        closes = [float(d['close']) for d in history[-20:]]
        multiplier = 2 / (20 + 1)
        ema = closes[0]
        for c in closes[1:]:
            ema = (c - ema) * multiplier + ema
        return closes[-1] > ema
    except Exception as e:
        print(f'  ⚠️  ETF EMA error {etf_symbol}: {e}')
        return None


def calc_dte(expiry_str):
    try:
        return (datetime.strptime(expiry_str, '%Y-%m-%d') - datetime.now()).days
    except Exception:
        return 0


def find_option(chain, option_type, strike):
    for o in chain:
        if o.get('option_type', '').lower() == option_type.lower():
            if abs(float(o.get('strike', 0)) - float(strike)) < 0.01:
                return o
    return None


# ─── Verbose rejection diagnostics ───────────────────────────────────────────

def _diagnose_no_spread(symbol: str, price: float, expiry: str, chain: list,
                        spread_type: str = 'bull_call'):
    """
    When no qualifying spread is found, explain the primary rejection reason.
    Counts how many strikes/spreads failed each rule and prints a one-line summary.
    """
    opt_type = 'call' if spread_type in ('bull_call',) else 'put'
    legs = [o for o in chain if o.get('option_type', '').lower() == opt_type]

    in_range = [o for o in legs
                if price * 0.95 <= float(o.get('strike', 0)) <= price * 1.15]

    if not in_range:
        print(f'  ↳ {spread_type}: no strikes near price ${price}')
        return

    counts = {'bid=0': 0, f'OI<{OI_MIN}': 0, f'IV>{IV_LAST_MAX}%': 0,
              f'BA>${BID_ASK_MAX}': 0, 'premium': 0, 'no_short': 0}

    for o in in_range:
        bid  = float(o.get('bid', 0) or 0)
        oi   = int(o.get('open_interest', 0) or 0)
        iv   = round(float((o.get('greeks') or {}).get('smv_vol', 0) or 0) * 100, 1)
        ask  = float(o.get('ask', 0) or 0)
        ba   = round(ask - bid, 3)

        if bid <= 0:             counts['bid=0'] += 1
        elif oi  < OI_MIN:       counts[f'OI<{OI_MIN}'] += 1
        elif iv  > IV_LAST_MAX:  counts[f'IV>{IV_LAST_MAX}%'] += 1
        elif ba  > BID_ASK_MAX:  counts[f'BA>${BID_ASK_MAX}'] += 1

    # Find dominant rejection reason
    top = max(counts, key=counts.get)
    top_val = counts[top]
    total = len(in_range)
    if top_val > 0:
        print(f'  ↳ {spread_type}: {total} strikes in range — '
              f'primary reject: {top} ({top_val}/{total} legs). '
              f'All: {", ".join(f"{k}:{v}" for k,v in counts.items() if v > 0)}')
    else:
        # Long legs pass but spreads don't — premium or no valid short
        print(f'  ↳ {spread_type}: legs pass filters but no spread met '
              f'premium ${PREMIUM_MIN}–${PREMIUM_MAX} or width ≤${SPREAD_WIDTH_MAX}')


# ─── Bull Call Spread ──────────────────────────────────────────────────────────

def analyze_spread(symbol, price, expiry, chain):
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
            continue

        long_bid = float(long_opt.get('bid', 0) or 0)
        long_ask = float(long_opt.get('ask', 0) or 0)
        long_oi  = int(long_opt.get('open_interest', 0) or 0)
        long_iv  = round(float((long_opt.get('greeks') or {}).get('smv_vol', 0) or 0) * 100, 1)

        if long_bid <= 0:            continue
        if long_oi  < OI_MIN:        continue
        if long_iv  > IV_LAST_MAX:   continue
        if (long_ask - long_bid) > BID_ASK_MAX: continue

        for short_opt in calls[i+1:i+4]:
            short_strike = float(short_opt.get('strike', 0))
            width = short_strike - long_strike
            if width <= 0 or width > SPREAD_WIDTH_MAX:
                continue

            short_bid = float(short_opt.get('bid', 0) or 0)
            short_ask = float(short_opt.get('ask', 0) or 0)
            short_oi  = int(short_opt.get('open_interest', 0) or 0)
            short_iv  = round(float((short_opt.get('greeks') or {}).get('smv_vol', 0) or 0) * 100, 1)

            if short_bid <= 0:            continue
            if short_oi  < OI_MIN:        continue
            if short_iv  > IV_LAST_MAX:   continue
            if (short_ask - short_bid) > BID_ASK_MAX: continue

            long_mid   = round((long_bid  + long_ask)  / 2, 2)
            short_mid  = round((short_bid + short_ask) / 2, 2)
            spread_mid = round(long_mid - short_mid, 2)

            if not (PREMIUM_MIN <= spread_mid <= PREMIUM_MAX):
                continue

            max_profit = round(width - spread_mid, 2)
            if max_profit <= 0:
                continue

            qualifying.append({
                'symbol':       symbol,
                'spread_type':  'bull_call',
                'price':        round(price, 2),
                'expiry':       expiry,
                'dte':          dte,
                'long_strike':  long_strike,
                'short_strike': short_strike,
                'long_bid':     long_bid,   'long_ask':   long_ask,
                'short_bid':    short_bid,  'short_ask':  short_ask,
                'long_oi':      long_oi,    'short_oi':   short_oi,
                'long_iv':      long_iv,    'short_iv':   short_iv,
                'spread_mid':   spread_mid,
                'max_profit':   max_profit,
                'rr':           round(max_profit / spread_mid, 1),
            })

    return qualifying


# ─── Bear Put Spread ───────────────────────────────────────────────────────────

def analyze_bear_put_spread(symbol, price, expiry, chain):
    dte = calc_dte(expiry)
    if not (DTE_MIN <= dte <= DTE_MAX):
        return []

    puts = [o for o in chain if o.get('option_type', '').lower() == 'put']
    if not puts:
        return []

    puts_sorted = sorted(puts, key=lambda o: float(o.get('strike', 0)), reverse=True)
    qualifying  = []

    for i, long_opt in enumerate(puts_sorted):
        long_strike = float(long_opt.get('strike', 0))
        if long_strike < price * 0.85 or long_strike > price * 1.05:
            continue

        long_bid = float(long_opt.get('bid', 0) or 0)
        long_ask = float(long_opt.get('ask', 0) or 0)
        long_oi  = int(long_opt.get('open_interest', 0) or 0)
        long_iv  = round(float((long_opt.get('greeks') or {}).get('smv_vol', 0) or 0) * 100, 1)

        if long_bid <= 0:            continue
        if long_oi  < OI_MIN:        continue
        if long_iv  > IV_LAST_MAX:   continue
        if (long_ask - long_bid) > BID_ASK_MAX: continue

        for short_opt in puts_sorted[i+1:i+4]:
            short_strike = float(short_opt.get('strike', 0))
            width = long_strike - short_strike
            if width <= 0 or width > SPREAD_WIDTH_MAX:
                continue

            short_bid = float(short_opt.get('bid', 0) or 0)
            short_ask = float(short_opt.get('ask', 0) or 0)
            short_oi  = int(short_opt.get('open_interest', 0) or 0)
            short_iv  = round(float((short_opt.get('greeks') or {}).get('smv_vol', 0) or 0) * 100, 1)

            if short_bid <= 0:            continue
            if short_oi  < OI_MIN:        continue
            if short_iv  > IV_LAST_MAX:   continue
            if (short_ask - short_bid) > BID_ASK_MAX: continue

            long_mid   = round((long_bid  + long_ask)  / 2, 2)
            short_mid  = round((short_bid + short_ask) / 2, 2)
            spread_mid = round(long_mid - short_mid, 2)

            if not (PREMIUM_MIN <= spread_mid <= PREMIUM_MAX):
                continue

            max_profit = round(width - spread_mid, 2)
            if max_profit <= 0:
                continue

            qualifying.append({
                'symbol':       symbol,
                'spread_type':  'bear_put',
                'price':        round(price, 2),
                'expiry':       expiry,
                'dte':          dte,
                'long_strike':  long_strike,
                'short_strike': short_strike,
                'long_bid':     long_bid,   'long_ask':   long_ask,
                'short_bid':    short_bid,  'short_ask':  short_ask,
                'long_oi':      long_oi,    'short_oi':   short_oi,
                'long_iv':      long_iv,    'short_iv':   short_iv,
                'spread_mid':   spread_mid,
                'max_profit':   max_profit,
                'rr':           round(max_profit / spread_mid, 1),
            })

    return qualifying


# ─── Iron Condor ──────────────────────────────────────────────────────────────

def analyze_iron_condor(symbol, price, expiry, chain):
    """
    Find Iron Condor setups: short OTM call spread + short OTM put spread.
    Credit strategy — collect premium when stock stays range-bound.

    Structure (4 legs):
      Buy  put  at lower strike       (downside protection)
      Sell put  slightly OTM below    (credit)
      Sell call slightly OTM above    (credit)
      Buy  call at higher strike      (upside protection)

    spread_mid = net credit received
    max_profit = net credit (stock stays between short strikes)
    max_loss   = max(call_width, put_width) - net_credit
    rr         = net_credit / max_loss

    Note: executor.py needs 4-leg mleg update before live submission.
    """
    dte = calc_dte(expiry)
    if not (DTE_MIN <= dte <= DTE_MAX):
        return []

    def get_mid(o): return round((float(o.get('bid', 0) or 0) + float(o.get('ask', 0) or 0)) / 2, 2)
    def get_iv(o):  return round(float((o.get('greeks') or {}).get('smv_vol', 0) or 0) * 100, 1)

    calls = sorted([o for o in chain if o.get('option_type', '').lower() == 'call'],
                   key=lambda o: float(o.get('strike', 0)))
    puts  = sorted([o for o in chain if o.get('option_type', '').lower() == 'put'],
                   key=lambda o: float(o.get('strike', 0)), reverse=True)

    if not calls or not puts:
        return []

    # ── OTM short call spread candidates ─────────────────────────────────────
    call_spreads = []
    for i, sc in enumerate(calls):
        sc_strike = float(sc.get('strike', 0))
        if sc_strike <= price * 1.02 or sc_strike > price * 1.15:
            continue
        sc_bid = float(sc.get('bid', 0) or 0)
        sc_ask = float(sc.get('ask', 0) or 0)
        sc_oi  = int(sc.get('open_interest', 0) or 0)
        if sc_bid <= 0 or sc_oi < OI_MIN or (sc_ask - sc_bid) > BID_ASK_MAX:
            continue
        for lc in calls[i+1:i+3]:
            lc_strike = float(lc.get('strike', 0))
            width = lc_strike - sc_strike
            if width <= 0 or width > SPREAD_WIDTH_MAX:
                continue
            lc_bid = float(lc.get('bid', 0) or 0)
            lc_ask = float(lc.get('ask', 0) or 0)
            lc_oi  = int(lc.get('open_interest', 0) or 0)
            if lc_bid <= 0 or lc_oi < OI_MIN or (lc_ask - lc_bid) > BID_ASK_MAX:
                continue
            credit = round(get_mid(sc) - get_mid(lc), 2)
            if credit > 0:
                call_spreads.append({
                    'short_strike': sc_strike, 'long_strike': lc_strike,
                    'short_bid': sc_bid, 'short_ask': sc_ask,
                    'long_bid':  lc_bid, 'long_ask':  lc_ask,
                    'short_oi':  sc_oi,  'long_oi':   lc_oi,
                    'short_iv':  get_iv(sc), 'long_iv': get_iv(lc),
                    'credit': credit, 'width': width,
                })

    # ── OTM short put spread candidates ──────────────────────────────────────
    put_spreads = []
    for i, sp in enumerate(puts):
        sp_strike = float(sp.get('strike', 0))
        if sp_strike >= price * 0.98 or sp_strike < price * 0.85:
            continue
        sp_bid = float(sp.get('bid', 0) or 0)
        sp_ask = float(sp.get('ask', 0) or 0)
        sp_oi  = int(sp.get('open_interest', 0) or 0)
        if sp_bid <= 0 or sp_oi < OI_MIN or (sp_ask - sp_bid) > BID_ASK_MAX:
            continue
        for lp in puts[i+1:i+3]:
            lp_strike = float(lp.get('strike', 0))
            width = sp_strike - lp_strike
            if width <= 0 or width > SPREAD_WIDTH_MAX:
                continue
            lp_bid = float(lp.get('bid', 0) or 0)
            lp_ask = float(lp.get('ask', 0) or 0)
            lp_oi  = int(lp.get('open_interest', 0) or 0)
            if lp_bid <= 0 or lp_oi < OI_MIN or (lp_ask - lp_bid) > BID_ASK_MAX:
                continue
            credit = round(get_mid(sp) - get_mid(lp), 2)
            if credit > 0:
                put_spreads.append({
                    'short_strike': sp_strike, 'long_strike': lp_strike,
                    'short_bid': sp_bid, 'short_ask': sp_ask,
                    'long_bid':  lp_bid, 'long_ask':  lp_ask,
                    'short_oi':  sp_oi,  'long_oi':   lp_oi,
                    'short_iv':  get_iv(sp), 'long_iv': get_iv(lp),
                    'credit': credit, 'width': width,
                })

    if not call_spreads or not put_spreads:
        return []

    # ── Combine: best call spread × best put spread ───────────────────────────
    call_spreads.sort(key=lambda x: -x['credit'])
    put_spreads.sort(key=lambda x: -x['credit'])

    qualifying = []
    for cs in call_spreads[:3]:
        for ps in put_spreads[:3]:
            net_credit = round(cs['credit'] + ps['credit'], 2)
            if net_credit < IC_CREDIT_MIN:
                continue
            max_width = max(cs['width'], ps['width'])
            max_loss  = round(max_width - net_credit, 2)
            if max_loss <= 0:
                continue
            rr = round(net_credit / max_loss, 2)
            qualifying.append({
                'symbol':            symbol,
                'spread_type':       'iron_condor',
                'price':             round(price, 2),
                'expiry':            expiry,
                'dte':               dte,
                # Four strikes (put wing / call wing)
                'put_long_strike':   ps['long_strike'],
                'put_short_strike':  ps['short_strike'],
                'call_short_strike': cs['short_strike'],
                'call_long_strike':  cs['long_strike'],
                # Compat fields (lowest / highest for display)
                'long_strike':       ps['long_strike'],
                'short_strike':      cs['long_strike'],
                # Bid-ask (short legs are the premium legs)
                'long_bid':  ps['short_bid'], 'long_ask':  ps['short_ask'],
                'short_bid': cs['short_bid'], 'short_ask': cs['short_ask'],
                'long_oi':   min(ps['short_oi'], ps['long_oi']),
                'short_oi':  min(cs['short_oi'], cs['long_oi']),
                'long_iv':   round((ps['short_iv'] + ps['long_iv']) / 2, 1),
                'short_iv':  round((cs['short_iv'] + cs['long_iv']) / 2, 1),
                'spread_mid':  net_credit,
                'max_profit':  net_credit,
                'max_loss':    max_loss,
                'rr':          rr,
                'call_credit': cs['credit'],
                'put_credit':  ps['credit'],
                'execution_note': '4-leg order — executor.py 4-leg update required',
            })

    qualifying.sort(key=lambda x: -x['rr'])
    return qualifying[:3]


# ─── Snapshot writer ───────────────────────────────────────────────────────────

def _write_snapshot(snapshot):
    fname = LOGS_DIR / f"scan_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    with open(fname, 'w') as f:
        json.dump(snapshot, f, indent=2)
    print(f'✅ Snapshot: {fname.name}')
    return fname


# ─── Main scan ─────────────────────────────────────────────────────────────────

def run_daily_scan():
    print(f"\n{'='*55}")
    print(f"OPENCLAW SCANNER v3.0 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*55}\n")

    # Expire any stale pending approvals from previous days
    expire_old_orders()

    # ── CRITICAL: initialize alerts/holds at top level ─────────────────────────
    alerts = []
    holds  = []
    ic_ok  = False   # set True when regime == 'flat_elevated'

    # ── Step 1: Note open positions (concurrency is governed by the executor) ───
    # vault_updater.py now enforces a PORTFOLIO BUDGET (max N concurrent + total
    # defined-risk ≤ % equity + ≤1 per direction) using LIVE Alpaca positions, so
    # the scanner no longer hard-blocks at 1 — it scans normally and the executor
    # admits qualifying orders up to the budget. (ACTIVE_POSITIONS below is a
    # cosmetic hint only; the real position state is read live from Alpaca.)
    if has_active_position():
        print('ℹ️  Existing position(s) noted — executor will admit new entries up to the portfolio budget')
        for pos in ACTIVE_POSITIONS:
            holds.append(f"{pos['symbol']}: position open (executor governs concurrency)")

    # ── Step 2: Fetch macro data ───────────────────────────────────────────────
    print('📊 Fetching macro indicators...')
    macro     = {}
    vix_price = None
    spy_chg   = 0.0

    # Shared market context (cross-system consistency; safe fallback). When the
    # nightly market_context.json is fresh, source VIX/SPY from it — skipping
    # those redundant per-ticker fetches and matching Tradier/guardrail exactly.
    # Falls back to a live get_quote per ticker if missing/stale (returns None).
    try:
        from read_macro_signal import load_macro_signal
        _shared_ctx = load_macro_signal()
    except Exception:
        _shared_ctx = None
    _ctx_quotes = (_shared_ctx or {}).get('quotes', {})
    if _shared_ctx:
        print(f'  📡 Shared market_context fresh (regime {_shared_ctx.get("regime")}) '
              f'— sourcing VIX/SPY from it')

    for sym in MACRO_TICKERS:
        q = _macro_quote_from_context(sym, _ctx_quotes)  # SPY/VIX when context fresh
        src = ' 📡' if q else ''
        if q is None:
            q = get_quote(sym)                            # live fallback / sectors
        if q:
            macro[sym] = q
            print(f'  {sym}: ${q["price"]} ({q["change_pct"]}%){src}')
            if sym == 'VIX': vix_price = q['price']
            if sym == 'SPY': spy_chg   = q['change_pct']
        else:
            print(f'  {sym}: N/A')

    # ── Step 3: Market condition gate ──────────────────────────────────────────
    # Abort if Tradier is completely unreachable — don't trade blind
    if vix_price is None and spy_chg == 0:
        print('\n❌ Tradier API unreachable — check TRADIER_API_KEY in .env — aborting scan')
        return {
            'scan_time': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'market_ok': False, 'vix': None, 'spy_chg': None,
            'alerts': [], 'approved_alerts': [], 'holds': [],
            'error': 'Tradier API unreachable',
        }

    # Determine market regime
    regime = determine_regime(spy_chg, vix_price)
    regime_labels = {
        'cash':          '🔴 CASH ONLY    — VIX > 30, all strategies blocked',
        'flat_elevated': '🔵 FLAT+HIGH VIX — Iron Condor zone (SPY flat, VIX ≥ 18)',
        'flat_low':      '🟡 FLAT+LOW VIX  — Bull Call default (SPY flat, VIX < 18)',
        'bull':          '🟢 BULL          — Bull Call Spread (SPY > +0.5%)',
        'bear':          '🟠 BEAR          — Bear Put Spread (SPY < -0.5%)',
        'unknown':       '⚪ UNKNOWN        — VIX unavailable, scan both',
    }
    print(f'\n📍 Regime: {regime_labels.get(regime, regime)} | VIX {vix_price}, SPY {spy_chg}%')

    # Hard stop — VIX > 30
    if regime == 'cash':
        snapshot = {
            'scan_time': datetime.now().isoformat(),
            'market_ok': False, 'regime': 'cash',
            'macro': macro, 'alerts': [], 'approved_alerts': [],
            'holds': [f'VIX {vix_price} > {VIX_HARD_STOP} — cash only'],
            'events_blocked': [], 'pending_approvals': [],
            'error': f'VIX {vix_price} above hard stop {VIX_HARD_STOP}',
        }
        _write_snapshot(snapshot)
        position_monitor.run()
        return snapshot

    # Iron Condor eligible when flat + elevated VIX
    ic_ok = (regime == 'flat_elevated')

    # Debit spread market gate (existing)
    market_ok = True
    if vix_price is not None and vix_price >= VIX_MAX:
        print(f'⚠️  VIX {vix_price} ≥ {VIX_MAX} — debit spreads expensive, IC preferred')
        market_ok = False
    if spy_chg <= SPY_DROP_MAX:
        print(f'⚠️  SPY {spy_chg}% ≤ {SPY_DROP_MAX}% — broad selloff')
        market_ok = False

    if market_ok:
        print(f'✅ Debit spread gate: OK')
    elif ic_ok:
        print(f'✅ IC gate: OK (debit blocked, Iron Condor eligible)')
    else:
        print(f'🚫 Market condition: ELEVATED — bull/bear entries blocked')

    # ── Step 4: Sector ETF EMA20 direction ────────────────────────────────────
    print('\n📈 Checking sector ETF direction (EMA20)...')
    etf_above_ema = {}
    for etf in SECTOR_ETFS:
        result = get_etf_above_ema20(etf)
        etf_above_ema[etf] = result
        tag = '↑ above' if result is True else ('↓ below' if result is False else '? N/A')
        print(f'  {etf}: {tag} EMA20')

    # ── Step 5: Bail early if no strategy is viable ───────────────────────────
    if not market_ok and not ic_ok and not has_active_position():
        print('\n🚫 No strategy eligible tonight — skipping scan')
        snapshot = {
            'scan_time': datetime.now().isoformat(),
            'market_ok': False, 'regime': regime,
            'macro':     macro,  'alerts': [],
            'holds':     ['Market condition elevated — no debit or IC opportunity'],
            'approved_alerts': [], 'events_blocked': [], 'pending_approvals': [],
        }
        _write_snapshot(snapshot)
        position_monitor.run()
        return snapshot

    # ── Step 6: Load candidates ────────────────────────────────────────────────
    candidates = load_candidates()
    print(f'\n📋 Candidates ({len(candidates)}): {", ".join(candidates)}')

    # ── Step 7: Scan each ticker ───────────────────────────────────────────────
    print(f'\n{"─"*55}')
    for symbol in candidates:

        is_hold, hold_reason = is_known_hold(symbol)
        if is_hold:
            print(f'\n⏳ {symbol}: {hold_reason}')
            holds.append(f'{symbol}: {hold_reason}')
            continue

        q = get_quote(symbol)
        if not q or not q.get('price'):
            print(f'\n❌ {symbol}: price unavailable')
            holds.append(f'{symbol}: price unavailable')
            continue

        price = q['price']
        print(f'\n🔍 {symbol}: ${price} ({q["change_pct"]}%)')

        if not (PRICE_MIN <= price <= PRICE_MAX):
            print(f'  ❌ Price ${price} outside ${PRICE_MIN}–${PRICE_MAX}')
            holds.append(f'{symbol}: price ${price} outside range')
            continue

        if abs(q['change_pct']) > DAILY_MOVE_MAX:
            print(f'  ❌ Daily move {q["change_pct"]}% exceeds ±{DAILY_MOVE_MAX}%')
            holds.append(f'{symbol}: daily move too large')
            continue

        # ETF direction
        etf      = TICKER_TO_ETF.get(symbol)
        bull_ok  = True
        bear_ok  = True
        etf_ema  = None

        if etf:
            etf_ema = etf_above_ema.get(etf)
            if etf_ema is True:
                bear_ok = False
                print(f'  📈 {etf} above EMA20 — bull call only')
            elif etf_ema is False:
                bull_ok = False
                print(f'  📉 {etf} below EMA20 — bear put only')
            else:
                print(f'  ❓ {etf} direction unknown — scan both')
        else:
            print(f'  ℹ️  No ETF mapping — scan both')

        expirations = get_expirations(symbol)
        if not expirations:
            print(f'  ❌ No expirations found')
            holds.append(f'{symbol}: no options expirations')
            continue

        valid_expiries = [e for e in expirations if DTE_MIN <= calc_dte(e) <= DTE_MAX]
        if not valid_expiries:
            print(f'  ❌ No expirations in {DTE_MIN}–{DTE_MAX} DTE window')
            holds.append(f'{symbol}: no qualifying expiry')
            continue

        found_any = False
        for expiry in valid_expiries:
            dte   = calc_dte(expiry)
            chain = get_options_chain(symbol, expiry)
            if not chain:
                continue

            print(f'  📅 {expiry} ({dte} DTE)...')

            # Iron Condor — direction-neutral, eligible when regime == flat_elevated
            if ic_ok:
                for spread in analyze_iron_condor(symbol, price, expiry, chain):
                    spread['etf_above_ema20'] = etf_ema
                    print(f'    ✅ IRON CONDOR: '
                          f'put ${spread["put_long_strike"]}/${spread["put_short_strike"]} | '
                          f'call ${spread["call_short_strike"]}/${spread["call_long_strike"]} '
                          f'credit=${spread["spread_mid"]} R:R={spread["rr"]} '
                          f'IV={spread["long_iv"]}%/{spread["short_iv"]}%')
                    alerts.append(spread)
                    found_any = True

            # Directional debit spreads — only when market_ok
            if not ic_ok:
                if market_ok and bull_ok:
                    for spread in analyze_spread(symbol, price, expiry, chain):
                        spread['etf_above_ema20'] = etf_ema
                        print(f'    ✅ BULL CALL: ${spread["long_strike"]}/${spread["short_strike"]} '
                              f'mid=${spread["spread_mid"]} R:R={spread["rr"]}:1 '
                              f'IV={spread["long_iv"]}%/{spread["short_iv"]}%')
                        alerts.append(spread)
                        found_any = True

                if bear_ok:
                    for spread in analyze_bear_put_spread(symbol, price, expiry, chain):
                        spread['etf_above_ema20'] = etf_ema
                        print(f'    ✅ BEAR PUT: ${spread["long_strike"]}/${spread["short_strike"]} '
                              f'mid=${spread["spread_mid"]} R:R={spread["rr"]}:1 '
                              f'IV={spread["long_iv"]}%/{spread["short_iv"]}%')
                        alerts.append(spread)
                        found_any = True

        if not found_any:
            print(f'  — No qualifying spread')
            # Verbose diagnostics: explain why each expiry/direction failed
            for expiry in valid_expiries:
                chain = get_options_chain(symbol, expiry)
                if not chain:
                    continue
                if bull_ok:
                    _diagnose_no_spread(symbol, price, expiry, chain, 'bull_call')
                if bear_ok:
                    _diagnose_no_spread(symbol, price, expiry, chain, 'bear_put')
            holds.append(f'{symbol}: no qualifying spread')

    # ── Step 8: Deduplicate (best R:R per ticker+type) ─────────────────────────
    seen       = {}
    for a in alerts:
        key = f'{a["symbol"]}_{a["spread_type"]}'
        if key not in seen or a['rr'] > seen[key]['rr']:
            seen[key] = a
    deduped_alerts = list(seen.values())

    # ── Step 9: Events check (Tradier fundamentals) ────────────────────────────
    print(f'\n{"─"*55}')
    print('📅 Checking events calendar (Tradier)...')

    pending_approvals = []
    events_blocked    = []

    if deduped_alerts:
        unique_symbols = list(dict.fromkeys(a['symbol'] for a in deduped_alerts))
        events_results = check_events(unique_symbols)

        today       = datetime.now().strftime('%Y-%m-%d')
        cutoff_date = (datetime.now() + timedelta(days=14)).strftime('%Y-%m-%d')

        for alert in deduped_alerts:
            sym     = alert['symbol']
            ev_res  = events_results.get(sym, {'status': 'uncertain', 'reason': 'no data'})
            status  = ev_res['status']

            # ── KNOWN_EVENTS override: block if event within ±14d of entry OR expiry ──
            if sym in KNOWN_EVENTS:
                event_date = KNOWN_EVENTS[sym]
                expiry     = alert.get('expiry', '')
                entry_block  = today <= event_date <= cutoff_date
                expiry_block = False
                if expiry:
                    try:
                        exp_dt      = datetime.strptime(expiry, '%Y-%m-%d')
                        exp_before  = (exp_dt - timedelta(days=14)).strftime('%Y-%m-%d')
                        exp_after   = (exp_dt + timedelta(days=14)).strftime('%Y-%m-%d')
                        expiry_block = exp_before <= event_date <= exp_after
                    except Exception:
                        pass
                if entry_block or expiry_block:
                    where = []
                    if entry_block:  where.append('within 14d of entry')
                    if expiry_block: where.append(f'within 14d of expiry {expiry}')
                    status = 'blocked'
                    ev_res = {'status': 'blocked',
                              'reason': f'KNOWN_EVENTS: {event_date} ({", ".join(where)})'}

            # ── KNOWN_CLEAR override: verified safe — promote uncertain→clear ──
            if sym in KNOWN_CLEAR and status == 'uncertain':
                clear_date = KNOWN_CLEAR[sym]
                if clear_date > cutoff_date:
                    status = 'clear'
                    ev_res = {'status': 'clear',
                              'reason': f'KNOWN_CLEAR: next event {clear_date} is outside ±14d window'}

            alert['events_status'] = status
            alert['events_reason'] = ev_res.get('reason', '')

            icon = {'clear': '✅', 'blocked': '🚫', 'uncertain': '⚠️ '}.get(status, '?')
            print(f'  {icon} {sym}: events {status.upper()} — {ev_res.get("reason", "")}')

            if status == 'blocked':
                events_blocked.append(f'{sym}: {ev_res["reason"]}')
                holds.append(f'{sym}: EVENTS BLOCKED — {ev_res["reason"]}')

    # ── Step 10: Conviction scoring ────────────────────────────────────────────
    print(f'\n{"─"*55}')
    print('🧠 Scoring conviction...')

    approved_alerts = []

    for alert in deduped_alerts:
        if alert.get('events_status') == 'blocked':
            continue   # skip events-blocked; already logged above

        conviction = score_conviction(alert, macro)
        alert['conviction_score']  = conviction['score']
        alert['conviction_pass']   = conviction['pass']
        alert['conviction_mode']   = conviction['mode']
        alert['conviction_reason'] = conviction['reasoning']

        events_uncertain = alert.get('events_status') == 'uncertain'

        if conviction['pass']:
            # ── Step 11: Write pending approval ───────────────────────────────
            ev_check = events_results.get(alert['symbol'], {})
            trade_id = write_pending(alert, conviction, ev_check)
            alert['trade_id'] = trade_id

            if events_uncertain:
                alert['approval_note'] = ('⚠️ Events data uncertain — '
                                          'verify IBKR calendar before approving')
                print(f'  ⚠️  {alert["symbol"]}: conviction {conviction["score"]}/100 PASS '
                      f'— events uncertain, IBKR check required before approval')
            else:
                print(f'  ✅ {alert["symbol"]}: conviction {conviction["score"]}/100 PASS '
                      f'— pending approval [{trade_id}]')
            approved_alerts.append(alert)
        else:
            print(f'  ❌ {alert["symbol"]}: conviction {conviction["score"]}/100 FAIL '
                  f'— {conviction["reasoning"]}')
            holds.append(f'{alert["symbol"]}: conviction {conviction["score"]}/100 below {CONVICTION_MIN}')

    # ── Step 12: Write snapshot JSON ───────────────────────────────────────────
    snapshot = {
        'scan_time':          datetime.now().isoformat(),
        'market_ok':          market_ok,
        'regime':             regime,
        'macro':              macro,
        'alerts':             deduped_alerts,          # all qualifying spreads (incl. events+conviction data)
        'approved_alerts':    approved_alerts,         # subset that passed everything → pending approval
        'holds':              holds,
        'events_blocked':     events_blocked,
        'pending_approvals':  [a.get('trade_id') for a in approved_alerts if a.get('trade_id')],
    }
    _write_snapshot(snapshot)

    # ── Step 13: Summary ───────────────────────────────────────────────────────
    print(f'\n{"="*55}')
    print('SCAN COMPLETE')
    print(f'Spreads found:     {len(deduped_alerts)}')
    print(f'Events blocked:    {len(events_blocked)}')
    print(f'Pending approval:  {len(approved_alerts)}')
    print(f'Holds/rejects:     {len(holds)}')

    if approved_alerts:
        print('\n🔔 AWAITING YOUR APPROVAL:')
        for a in approved_alerts:
            t  = 'BULL CALL' if a['spread_type'] == 'bull_call' else 'BEAR PUT'
            ev = '⚠️ verify IBKR' if a.get('events_status') == 'uncertain' else '✅ events clear'
            print(f'  [{a["trade_id"]}] {a["symbol"]} ${a["long_strike"]}/{a["short_strike"]} '
                  f'{a["expiry"]} — {t} — conviction {a["conviction_score"]}/100 — {ev}')
        print('\n  → Open Approval Dashboard in Cowork to approve/reject')
        print('  → Or: python3 approval_manager.py approve <trade_id>')

    print(f'{"="*55}\n')

    # ── Step 14: Position monitor ──────────────────────────────────────────────
    print('👁  Running position monitor...')
    position_monitor.run()

    return snapshot


if __name__ == '__main__':
    run_daily_scan()
