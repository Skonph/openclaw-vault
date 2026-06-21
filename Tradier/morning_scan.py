import os
import requests
from datetime import datetime
import pytz
from dotenv import load_dotenv

load_dotenv('/home/ubuntu/trading-bot/.env')

ALPACA_KEY = os.environ.get('ALPACA_API_KEY')
ALPACA_SECRET = os.environ.get('ALPACA_SECRET_KEY')
OPENROUTER_KEY = os.environ.get('OPENROUTER_API_KEY')
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

WATCHLIST = ['RIVN', 'NIO', 'F', 'MARA', 'GRAB', 'BARK', 'VALE', 'AAL']

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message
        }, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def get_stock_price(ticker):
    try:
        headers = {
            'APCA-API-KEY-ID': ALPACA_KEY,
            'APCA-API-SECRET-KEY': ALPACA_SECRET
        }
        # Use bars endpoint - more reliable than quotes
        url = f"https://data.alpaca.markets/v2/stocks/{ticker}/bars/latest"
        params = {'feed': 'iex'}
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            bar = data.get('bar', {})
            close = bar.get('c', None)
            if close and close > 0:
                return round(float(close), 2)
        # Fallback to trades endpoint
        url2 = f"https://data.alpaca.markets/v2/stocks/{ticker}/trades/latest"
        r2 = requests.get(url2, headers=headers, params=params, timeout=10)
        if r2.status_code == 200:
            data2 = r2.json()
            trade = data2.get('trade', {})
            price = trade.get('p', None)
            if price and price > 0:
                return round(float(price), 2)
        return None
    except Exception as e:
        print(f"Alpaca error for {ticker}: {e}")
        return None

def analyze_with_claude(market_data):
    try:
        response = requests.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {OPENROUTER_KEY}',
                'Content-Type': 'application/json'
            },
            json={
                'model': 'anthropic/claude-sonnet-4-5',
                'messages': [
                    {
                        'role': 'system',
                        'content': '''You are NOVA, a disciplined options trading analyst.
Account: $3,000 paper trading
Max risk per trade: $60
Max stock price: $30
Strategy: bull call spread only (IV rank under 50%)
Min conviction to flag: 65/100
Min reward to risk: 1.5:1

For each ticker respond in this format:
TICKER | PRICE | CONVICTION/100 | ACTION
Reason: one line max 15 words

If nothing qualifies write: NO QUALIFYING TRADES TODAY'''
                    },
                    {
                        'role': 'user',
                        'content': market_data
                    }
                ],
                'max_tokens': 500,
                'temperature': 0.1
            },
            timeout=30
        )
        data = response.json()
        if 'choices' in data:
            return data['choices'][0]['message']['content']
        elif 'error' in data:
            return f"API Error: {data['error'].get('message', 'Unknown')}"
        else:
            return f"Unexpected response: {str(data)[:100]}"
    except Exception as e:
        return f"Claude call failed: {str(e)}"

def run_morning_scan():
    bkk = pytz.timezone('Asia/Bangkok')
    now = datetime.now(bkk)

    # Skip weekends
    if now.weekday() >= 5:
        print(f"Weekend skip - {now.strftime('%A')}")
        return

    print(f"Starting scan at {now.strftime('%H:%M')} Bangkok")
    send_telegram(f"NOVA Morning Scan - {now.strftime('%A %b %d')}\nScanning {len(WATCHLIST)} tickers...")

    # Collect prices
    data_summary = f"Market scan {now.strftime('%Y-%m-%d %H:%M')} Bangkok\n\n"
    prices_found = 0

    for ticker in WATCHLIST:
        price = get_stock_price(ticker)
        if price:
            if price > 30:
                data_summary += f"{ticker}: ${price} - EXCLUDED (price over $30 limit)\n"
            else:
                data_summary += f"{ticker}: ${price}\n"
                prices_found += 1
        else:
            data_summary += f"{ticker}: Price unavailable\n"

    data_summary += f"\nPrices retrieved: {prices_found}/{len(WATCHLIST)}"
    data_summary += "\nNote: Options chain data requires manual pull from Alpaca."
    data_summary += "\nUse knowledge-based IV estimates. Flag for manual review if conviction above 65."

    print(data_summary)

    # Get Claude analysis
    print("Calling Claude...")
    analysis = analyze_with_claude(data_summary)
    print(f"Analysis: {analysis}")

    # Send final report
    report = f"NOVA Signal Report - {now.strftime('%b %d')}\n\n{analysis}\n\nManual options chain review needed for any trade above conviction 65."
    send_telegram(report)
    print("Scan complete - report sent to Telegram")

if __name__ == '__main__':
    run_morning_scan()
