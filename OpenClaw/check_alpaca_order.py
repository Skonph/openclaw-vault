#!/usr/bin/env python3
"""
Read-only Alpaca order inspector — for IC live-fire validation (Improvement #3).

Fetches one order by ID and prints status, top-level filled_avg_price, and
per-leg detail (symbol, side, ratio_qty, filled_avg_price, position_intent).

This makes NO trading calls — GET only.

Usage:
  python3 check_alpaca_order.py <order_id>

Example (the TOST Iron Condor from pending_orders.json):
  python3 check_alpaca_order.py d0b87fc1-97ff-48b6-b494-dd21d7b7cf13
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


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    order_id = sys.argv[1]
    r = requests.get(f'{ALPACA_BASE}/orders/{order_id}', headers=HEADERS, timeout=15)
    print(f'HTTP {r.status_code}')

    if r.status_code != 200:
        print(r.text[:500])
        sys.exit(1)

    o = r.json()
    print('\n=== Order Summary ===')
    print(f'  id:                {o.get("id")}')
    print(f'  symbol:            {o.get("symbol")}')
    print(f'  order_class:       {o.get("order_class")}')
    print(f'  status:            {o.get("status")}')
    print(f'  type:              {o.get("type")}')
    print(f'  limit_price:       {o.get("limit_price")}')
    print(f'  filled_avg_price:  {o.get("filled_avg_price")}')
    print(f'  qty:               {o.get("qty")}  filled_qty: {o.get("filled_qty")}')
    print(f'  created_at:        {o.get("created_at")}')
    print(f'  filled_at:         {o.get("filled_at")}')

    legs = o.get('legs') or []
    if legs:
        print(f'\n=== Legs ({len(legs)}) ===')
        for i, leg in enumerate(legs, 1):
            print(f'  Leg {i}: {leg.get("symbol")}')
            print(f'    side:             {leg.get("side")}')
            print(f'    ratio_qty:        {leg.get("ratio_qty")}')
            print(f'    position_intent:  {leg.get("position_intent")}')
            print(f'    status:           {leg.get("status")}')
            print(f'    filled_qty:       {leg.get("filled_qty")}')
            print(f'    filled_avg_price: {leg.get("filled_avg_price")}')
    else:
        print('\n(no legs array on this order)')

    print('\n=== Full raw JSON ===')
    print(json.dumps(o, indent=2))


if __name__ == '__main__':
    main()
