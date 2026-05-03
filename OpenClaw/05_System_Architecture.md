# System Architecture — OpenClaw

## Server Details
- Host: ubuntu@43.160.222.7
- Bot directory: ~/trading-bot
- Env file: /home/ubuntu/trading-bot/.env
- SSH: ssh ubuntu@43.160.222.7

## Environment Variables (.env)
- ALPACA_API_KEY
- ALPACA_SECRET_KEY
- ALPACA_BASE_URL

## Standard Order Template (Python)
\`\`\`python
import os, requests
from dotenv import load_dotenv
load_dotenv('/home/ubuntu/trading-bot/.env')

headers = {
    'APCA-API-KEY-ID': os.environ.get('ALPACA_API_KEY'),
    'APCA-API-SECRET-KEY': os.environ.get('ALPACA_SECRET_KEY'),
    'Content-Type': 'application/json'
}

order = {
    "type": "limit",
    "time_in_force": "day",
    "order_class": "mleg",
    "qty": "1",
    "limit_price": "[SPREAD_MID]",
    "legs": [
        {
            "symbol": "[TICKER][EXPIRY]C[STRIKE]",
            "side": "buy",
            "ratio_qty": "1",
            "position_intent": "buy_to_open"
        },
        {
            "symbol": "[TICKER][EXPIRY]C[STRIKE]",
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
\`\`\`

## Standard Close Template (Python)
Same as above but:
- side: "sell" for long leg
- side: "buy" for short leg
- position_intent: "sell_to_close" / "buy_to_close"

## SSH Execution Template (Bash)
\`\`\`bash
ssh ubuntu@43.160.222.7 << 'ENDSSH'
cd ~/trading-bot
python3 - << 'EOF'
[python code here]
EOF
ENDSSH
\`\`\`

## Cancel Specific Order
\`\`\`python
r = requests.delete(
    f"{os.environ.get('ALPACA_BASE_URL')}/orders/{ORDER_ID}",
    headers=headers
)
\`\`\`

## Cancel All Open Orders
\`\`\`python
r = requests.delete(
    f"{os.environ.get('ALPACA_BASE_URL')}/orders",
    headers=headers
)
\`\`\`

## Check Open Orders
\`\`\`python
r = requests.get(
    f"{os.environ.get('ALPACA_BASE_URL')}/orders?status=open",
    headers=headers
)
\`\`\`

## Cowork Tasks
### Task 1: VALE Daily Snapshot
- Schedule: 9:30 PM Bangkok daily
- Output: VALE_GoogleFinance_YYYYMMDD.png
          VALE_YahooOptions_YYYYMMDD.png
- Save path: /Users/SkonP/AI_Prompt/trade/price_snapshots/

### Task 2: VALE Trade Analyzer
- Schedule: 9:35 PM Bangkok daily
- Input: Today's snapshot files
- Output: VALE_Analysis_YYYYMMDD.txt
          VALE_NovaPrompt_YYYYMMDD.txt (if TRADE)
          VALE_AlpacaOrder_YYYYMMDD.sh (if TRADE)

## Symbol Format for Alpaca
[TICKER][YYMMDD]C[8-DIGIT-STRIKE]
Examples:
- VALE260529C00016000 = VALE $16C May29 2026
- CCL260529C00028000 = CCL $28C May29 2026
- AAL260529C00012000 = AAL $12C May29 2026
Strike format: price × 1000, padded to 8 digits