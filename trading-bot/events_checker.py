#!/usr/bin/env python3
"""
OpenClaw Events Checker
Checks Tradier fundamentals/calendars for upcoming earnings and dividends.
Returns clear / blocked / uncertain per ticker.

Earnings ban: ±14 days from today.
Dividend ban: within 14 days forward (ex-dividend risk on short leg).

Usage (standalone test):
  python3 events_checker.py NCLH CCL AAL
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv('/home/ubuntu/trading-bot/.env')

TRADIER_TOKEN = os.environ.get('TRADIER_API_KEY', '') or os.environ.get('TRADIER_TOKEN', '')
TRADIER_BASE  = 'https://api.tradier.com/v1'
TRADIER_HEADERS = {
    'Authorization': f'Bearer {TRADIER_TOKEN}',
    'Accept': 'application/json',
}

EARNINGS_BAN_DAYS  = 14   # block if earnings within ±N days
DIVIDEND_BAN_DAYS  = 14   # block if ex-div within N days forward


# ─── Core fetch ───────────────────────────────────────────────────────────────

def _fetch_calendar(symbols: list[str]) -> dict:
    """
    Call Tradier GET /v1/markets/fundamentals/calendars.
    Returns raw JSON or {} on failure.
    Note: requires Tradier brokerage / non-sandbox plan.
    """
    try:
        r = requests.get(
            f'{TRADIER_BASE}/markets/fundamentals/calendars',
            headers=TRADIER_HEADERS,
            params={'symbols': ','.join(symbols)},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
        if r.status_code == 403:
            print('⚠️  Tradier fundamentals: 403 — plan may not include this endpoint')
        else:
            print(f'⚠️  Tradier fundamentals: HTTP {r.status_code}')
        return {}
    except Exception as e:
        print(f'⚠️  events_checker fetch error: {e}')
        return {}


def _parse_date(val) -> datetime | None:
    """Parse YYYY-MM-DD string → datetime, or None."""
    if not val:
        return None
    try:
        return datetime.strptime(str(val)[:10], '%Y-%m-%d')
    except Exception:
        return None


# ─── Per-ticker analysis ───────────────────────────────────────────────────────

def _analyse_ticker(symbol: str, calendar_data: dict) -> dict:
    """
    Given raw calendar data for one ticker, return:
    {
      'symbol': str,
      'status': 'clear' | 'blocked' | 'uncertain',
      'events': [...],    # list of event dicts that triggered status
      'reason': str,
    }
    """
    today     = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    win_start = today - timedelta(days=EARNINGS_BAN_DAYS)
    win_end   = today + timedelta(days=EARNINGS_BAN_DAYS)
    div_end   = today + timedelta(days=DIVIDEND_BAN_DAYS)

    events_found = []

    # Tradier returns a list of security entries; navigate to matching symbol
    # Response shape varies: list or single dict
    securities = calendar_data.get('securities', {})
    if not securities:
        return {'symbol': symbol, 'status': 'uncertain', 'events': [],
                'reason': 'No data from Tradier fundamentals endpoint'}

    security_list = securities.get('security', [])
    if isinstance(security_list, dict):
        security_list = [security_list]

    ticker_data = None
    for sec in security_list:
        if str(sec.get('id', '')).upper() == symbol.upper():
            ticker_data = sec
            break

    if ticker_data is None:
        return {'symbol': symbol, 'status': 'uncertain', 'events': [],
                'reason': f'Ticker {symbol} not found in Tradier response'}

    # ── Earnings ──────────────────────────────────────────────────────────────
    # Tradier may return this under several possible keys
    for key in ('reporting_date', 'earnings_date', 'next_reporting_date'):
        raw = ticker_data.get(key) or (ticker_data.get('fundamentals') or {}).get(key)
        dt  = _parse_date(raw)
        if dt and win_start <= dt <= win_end:
            events_found.append({
                'type': 'earnings',
                'date': dt.strftime('%Y-%m-%d'),
                'days_away': (dt - today).days,
            })

    # Check nested 'calendar' block if present (Tradier may return dict or list)
    cal = ticker_data.get('calendar') or {}
    if isinstance(cal, dict):
        cal = [cal]   # normalise single-entry dict to list
    if isinstance(cal, list):
        for entry in cal:
            et  = str(entry.get('event_type', '')).lower()
            dt  = _parse_date(entry.get('date') or entry.get('event_date'))
            if dt is None:
                continue
            if 'earn' in et and win_start <= dt <= win_end:
                events_found.append({'type': 'earnings', 'date': dt.strftime('%Y-%m-%d'),
                                     'days_away': (dt - today).days})
            elif 'div' in et and today <= dt <= div_end:
                events_found.append({'type': 'dividend', 'date': dt.strftime('%Y-%m-%d'),
                                     'days_away': (dt - today).days})

    # ── Dividend ex-date ──────────────────────────────────────────────────────
    for key in ('ex_dividend_date', 'next_ex_date', 'dividend_date'):
        raw = ticker_data.get(key) or (ticker_data.get('fundamentals') or {}).get(key)
        dt  = _parse_date(raw)
        if dt and today <= dt <= div_end:
            events_found.append({
                'type': 'dividend',
                'date': dt.strftime('%Y-%m-%d'),
                'days_away': (dt - today).days,
            })

    # Deduplicate by type + date
    seen = set()
    unique_events = []
    for e in events_found:
        key = (e['type'], e['date'])
        if key not in seen:
            seen.add(key)
            unique_events.append(e)

    if unique_events:
        reasons = ', '.join(f"{e['type']} {e['date']} ({e['days_away']:+d}d)" for e in unique_events)
        return {'symbol': symbol, 'status': 'blocked', 'events': unique_events,
                'reason': f'Event conflict: {reasons}'}

    return {'symbol': symbol, 'status': 'clear', 'events': [],
            'reason': 'No earnings or dividend events in window'}


# ─── Public API ───────────────────────────────────────────────────────────────

def check_events(symbols: list[str]) -> dict[str, dict]:
    """
    Check a list of tickers for event conflicts.

    Returns dict keyed by symbol:
    {
      'NCLH': {'symbol': 'NCLH', 'status': 'clear', 'events': [], 'reason': '...'},
      'CCL':  {'symbol': 'CCL',  'status': 'blocked', 'events': [...], 'reason': '...'},
    }

    Statuses:
      'clear'     — no earnings or dividend in window, safe to trade
      'blocked'   — confirmed event within ±14 days, DO NOT trade
      'uncertain' — Tradier returned no data; human should verify via IBKR
    """
    if not symbols:
        return {}

    # Fetch in one batch request
    raw = _fetch_calendar(symbols)

    results = {}
    for sym in symbols:
        results[sym] = _analyse_ticker(sym, raw)

    return results


def check_one(symbol: str) -> dict:
    """Convenience wrapper for a single ticker."""
    return check_events([symbol]).get(symbol, {
        'symbol': symbol, 'status': 'uncertain', 'events': [],
        'reason': 'check_one returned empty',
    })


# ─── Standalone test ──────────────────────────────────────────────────────────

if __name__ == '__main__':
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ['NCLH', 'CCL', 'AAL', 'VALE', 'PR']
    print(f'\n=== Events Check — {datetime.now().strftime("%Y-%m-%d %H:%M")} ===')
    print(f'Window: ±{EARNINGS_BAN_DAYS}d earnings, +{DIVIDEND_BAN_DAYS}d dividend\n')

    results = check_events(tickers)
    for sym, r in results.items():
        icon = {'clear': '✅', 'blocked': '🚫', 'uncertain': '⚠️ '}.get(r['status'], '?')
        print(f'{icon}  {sym:8s}  [{r["status"].upper():9s}]  {r["reason"]}')
        for e in r.get('events', []):
            print(f'         ↳ {e["type"]} on {e["date"]} ({e["days_away"]:+d} days)')
    print()
