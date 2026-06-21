#!/usr/bin/env python3
"""
Read-only TOST Iron Condor status checker (cross-system rollup follow-up,
2026-06-13).

Resolves the "TOST IC outcome unresolved" gap from the cross-system
performance rollup by checking three things against Alpaca, GET-only:

  1. The original opening order (d0b87fc1-...) — confirm it actually filled
     and on what terms (reuses check_alpaca_order.py's logic).
  2. GET /v2/positions — are any of the 4 TOST option legs still open?
  3. GET /v2/orders?status=closed&symbols=TOST — any closing/exit orders
     for TOST legs, and their fill prices?

This makes NO trading calls — GET only. Prints a plain-English summary at
the end so it's easy to paste back for journal/report updates.

Usage:
  python3 check_tost_status.py
"""

import os
import sys
import json
import requests
from pathlib import Path
from dotenv import load_dotenv

for env_path in ['/home/ubuntu/openclaw/.env',
                  str(Path(__file__).parent / '.env'),
                  str(Path(__file__).parent / '.env.local')]:
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

ALPACA_KEY    = os.environ.get('ALPACA_API_KEY', '')
ALPACA_SECRET = os.environ.get('ALPACA_SECRET_KEY', '')
ALPACA_BASE   = os.environ.get('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets/v2').rstrip('/')
if not ALPACA_BASE.endswith('/v2'):
    ALPACA_BASE += '/v2'

HEADERS = {
    'APCA-API-KEY-ID':     ALPACA_KEY,
    'APCA-API-SECRET-KEY': ALPACA_SECRET,
}

# From OpenClaw/pending_orders.json — TOST IC, expiry 2026-07-17
TOST_OPEN_ORDER_ID = 'd0b87fc1-97ff-48b6-b494-dd21d7b7cf13'
TOST_LEG_SYMBOLS = [
    'TOST260717P00020000',  # put long  (strike 20)
    'TOST260717P00021000',  # put short (strike 21)
    'TOST260717C00027000',  # call short (strike 27)
    'TOST260717C00029000',  # call long  (strike 29)
]


def get(path, params=None):
    r = requests.get(f'{ALPACA_BASE}{path}', headers=HEADERS, params=params, timeout=15)
    return r


def main():
    print('=== 1. Opening order ===')
    r = get(f'/orders/{TOST_OPEN_ORDER_ID}')
    print(f'HTTP {r.status_code}')
    open_order = {}
    if r.status_code == 200:
        open_order = r.json()
        print(f'  status:           {open_order.get("status")}')
        print(f'  filled_avg_price: {open_order.get("filled_avg_price")}')
        print(f'  filled_at:        {open_order.get("filled_at")}')
        for leg in open_order.get('legs') or []:
            print(f'    leg {leg.get("symbol")}: {leg.get("side")} '
                  f'filled_qty={leg.get("filled_qty")} '
                  f'filled_avg_price={leg.get("filled_avg_price")}')
    else:
        print(f'  {r.text[:300]}')

    print('\n=== 2. Current open positions (TOST legs) ===')
    r = get('/positions')
    print(f'HTTP {r.status_code}')
    open_tost_legs = []
    if r.status_code == 200:
        for pos in r.json():
            if pos.get('symbol', '').startswith('TOST'):
                open_tost_legs.append(pos)
                print(f'  OPEN: {pos.get("symbol")}  qty={pos.get("qty")} '
                      f'avg_entry={pos.get("avg_entry_price")} '
                      f'unrealized_pl={pos.get("unrealized_pl")} '
                      f'current_price={pos.get("current_price")}')
        if not open_tost_legs:
            print('  (none — no TOST legs currently open)')
    else:
        print(f'  {r.text[:300]}')

    print('\n=== 3. Closed/filled orders for TOST legs ===')
    r = get('/orders', params={'status': 'closed', 'symbols': 'TOST', 'limit': 100,
                                'direction': 'desc'})
    print(f'HTTP {r.status_code}')
    closing_orders = []
    if r.status_code == 200:
        orders = r.json()
        for o in orders:
            # Skip the original opening order, look for anything else touching
            # the TOST legs (close orders submitted by auto_close_spread)
            if o.get('id') == TOST_OPEN_ORDER_ID:
                continue
            legs = o.get('legs') or []
            leg_syms = [l.get('symbol') for l in legs] if legs else [o.get('symbol')]
            if any(s in TOST_LEG_SYMBOLS for s in leg_syms):
                closing_orders.append(o)
                print(f'  order {o.get("id")}: status={o.get("status")} '
                      f'filled_avg_price={o.get("filled_avg_price")} '
                      f'filled_at={o.get("filled_at")}')
                for leg in legs:
                    print(f'    leg {leg.get("symbol")}: {leg.get("side")} '
                          f'filled_qty={leg.get("filled_qty")} '
                          f'filled_avg_price={leg.get("filled_avg_price")}')
        if not closing_orders:
            print('  (none found)')
    else:
        print(f'  {r.text[:300]}')

    print('\n=== Summary ===')
    if open_order.get('status') != 'filled':
        print(f'  Opening order status is "{open_order.get("status")}" — IC may not '
              f'have actually been entered. Re-check execution.')
    elif open_tost_legs:
        print(f'  IC is STILL OPEN ({len(open_tost_legs)}/4 legs showing in /positions). '
              f'04_Trade_Journal.md and the rollup should list this as an open '
              f'position, not "unresolved/closed".')
    elif closing_orders:
        entry_credit = open_order.get('filled_avg_price')
        close_price = closing_orders[0].get('filled_avg_price')
        print(f'  IC appears CLOSED. Entry net credit: {entry_credit}, '
              f'close fill: {close_price}. Compute P&L = '
              f'(entry_credit - close_debit) * qty * 100 and add a Trade 5 '
              f'entry to 04_Trade_Journal.md.')
    else:
        print('  IC opening order filled, but no open position AND no closing '
              'order found for TOST legs. This is ambiguous — may indicate the '
              '4-leg position was opened under a different symbol format, or '
              'positions/orders pagination missed it. Try GET /v2/positions '
              '(full dump) and GET /v2/account/activities for TOST manually.')


if __name__ == '__main__':
    main()
