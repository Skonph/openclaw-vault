#!/usr/bin/env python3
"""Quick Tradier quote debug — run on server to see raw response."""
import os, json, requests
from dotenv import load_dotenv
load_dotenv('/home/ubuntu/trading-bot/.env')

TOKEN = os.environ.get('TRADIER_API_KEY', '') or os.environ.get('TRADIER_TOKEN', '')
BASE  = 'https://api.tradier.com/v1'

print(f'Token: {"SET (" + TOKEN[:6] + "...)" if TOKEN else "MISSING ❌"}\n')

for sym in ['NCLH', 'CCL', 'SPY']:
    r = requests.get(
        f'{BASE}/markets/quotes',
        headers={'Authorization': f'Bearer {TOKEN}', 'Accept': 'application/json'},
        params={'symbols': sym, 'greeks': 'false'},
        timeout=10,
    )
    print(f'=== {sym} — HTTP {r.status_code} ===')
    try:
        data = r.json()
        quote = data.get('quotes', {}).get('quote', {})
        if isinstance(quote, list):
            quote = quote[0]
        print(json.dumps({
            'last':      quote.get('last'),
            'close':     quote.get('close'),
            'prevclose': quote.get('prevclose'),
            'bid':       quote.get('bid'),
            'ask':       quote.get('ask'),
            'type':      quote.get('type'),
            'description': quote.get('description'),
        }, indent=2))
    except Exception as e:
        print(f'Parse error: {e}')
        print(r.text[:300])
    print()
