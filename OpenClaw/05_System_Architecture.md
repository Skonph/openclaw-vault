# System Architecture — OpenClaw

## Server Details
- Host: ubuntu@43.156.9.185
- Bot directory: ~/openclaw
- Env file: /home/ubuntu/openclaw/.env
- SSH: ssh ubuntu@43.156.9.185

## Environment Variables (.env)
- ALPACA_API_KEY
- ALPACA_SECRET_KEY
- ALPACA_BASE_URL

## Standard Order Template (Python)
\`\`\`python
import os, requests
from dotenv import load_dotenv
load_dotenv('/home/ubuntu/openclaw/.env')

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
ssh ubuntu@43.156.9.185 << 'ENDSSH'
cd ~/openclaw
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

## Symbol Format for Alpaca
[TICKER][YYMMDD]C[8-DIGIT-STRIKE]
Examples:
- VALE260529C00016000 = VALE $16C May29 2026
- CCL260529C00028000 = CCL $28C May29 2026
- AAL260529C00012000 = AAL $12C May29 2026
Strike format: price × 1000, padded to 8 digits

## IBKR Account (U25439978)
Status: Funded $2,200 — PAPER USE ONLY until graduation
URL: interactivebrokers.com

### Tools in Use
| Tool | Purpose | Frequency |
|------|---------|-----------|
| Watchlist | Monitor CCL/NCLH/AAL/VALE/ETFs | Daily |
| Market Screener 2.0 | Find IV<40% candidates | Daily |
| Market Overview | Macro context check | Daily |
| Events Calendar | Earnings date verification | Per trade |
| Why Is It Moving? | Catalyst confirmation | As needed |

### Saved Screens
- "OpenClaw Bull Call Setup" — IV<40%, $10-$30, OI>500
- "OpenClaw Bear Put Setup" — same + downtrend filter

### Watchlist Tickers
CCL, NCLH, AAL, VALE (primary)
XLY, XLI, XLE, XLB (sector ETFs)
VIX (volatility monitor)

## Tradier Account (6YB80974)
Status: Unfunded — API use only
Purpose: Automated IV scanning via API
API endpoint: api.tradier.com/v1
Setup: Pending API key configuration