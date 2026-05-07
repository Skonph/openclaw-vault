
cd ~/trading-bot
python3 - << 'EOF'
import os, requests
from dotenv import load_dotenv
load_dotenv('/home/ubuntu/trading-bot/.env')

headers = {
    'APCA-API-KEY-ID': os.environ.get('ALPACA_API_KEY'),
    'APCA-API-SECRET-KEY': os.environ.get('ALPACA_SECRET_KEY'),
    'Content-Type': 'application/json'
}

# AM $22/$23 Bull Call Spread — Jun 18, 2026
order = {
    "type": "limit",
    "time_in_force": "day",
    "order_class": "mleg",
    "qty": "1",
    "limit_price": "0.30",
    "legs": [
        {
            "symbol": "AM260618C00022000",
            "side": "buy",
            "ratio_qty": "1",
            "position_intent": "buy_to_open"
        },
        {
            "symbol": "AM260618C00023000",
            "side": "sell",
            "ratio_qty": "1",
            "position_intent": "sell_to_open"
        }
    ]
}

r = requests.post(
    f"{os.environ.get('ALPACA_BASE_URL')}/orders",
    headers=headers,
    json=order
)
print(f"Status: {r.status_code}")
print(f"Response: {r.json()}")
EOF