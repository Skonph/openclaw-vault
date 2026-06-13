#!/usr/bin/env python3
"""
OpenClaw Executor v1.0

Submits vertical spread orders to Alpaca paper trading API.
Called after human approval — NEVER runs automatically without approval.

Supports:
  - Bull Call Spread: buy lower strike call + sell higher strike call
  - Bear Put Spread:  buy higher strike put  + sell lower strike put

OCC Symbol format: {SYMBOL}{YY}{MM}{DD}{C|P}{8-digit strike × 1000}
Example: NCLH260620C00017000  = NCLH call, Jun 20 2026, $17 strike

Usage:
  python3 executor.py execute <trade_id>      # execute approved order from queue
  python3 executor.py dry-run <trade_id>      # show what would be sent — no actual order
  python3 executor.py close   <alpaca_id>     # close an open position by Alpaca order ID

Can also be called from Cowork bash tool using the Alpaca API key directly.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv('/home/ubuntu/trading-bot/.env')

ALPACA_KEY     = os.environ.get('ALPACA_API_KEY', '')
ALPACA_SECRET  = os.environ.get('ALPACA_SECRET_KEY', '')
ALPACA_BASE    = os.environ.get('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets/v2')

ALPACA_HEADERS = {
    'APCA-API-KEY-ID':     ALPACA_KEY,
    'APCA-API-SECRET-KEY': ALPACA_SECRET,
    'Content-Type':        'application/json',
}

# Max slippage: pay up to 5% more than mid price to get filled
SLIPPAGE_BUFFER = 0.05


# ─── OCC Symbol builder ───────────────────────────────────────────────────────

def build_occ_symbol(underlying: str, expiry: str, option_type: str, strike: float) -> str:
    """
    Build OCC option symbol.
    underlying:  'NCLH'
    expiry:      '2026-06-20'
    option_type: 'call' or 'put' (or 'C'/'P')
    strike:      17.0

    Returns: 'NCLH260620C00017000'
    """
    dt       = datetime.strptime(expiry, '%Y-%m-%d')
    yy       = dt.strftime('%y')
    mm       = dt.strftime('%m')
    dd       = dt.strftime('%d')
    cp       = 'C' if option_type.lower().startswith('c') else 'P'
    strike_i = int(round(strike * 1000))
    strike_s = f'{strike_i:08d}'
    return f'{underlying.upper()}{yy}{mm}{dd}{cp}{strike_s}'


# ─── Order logic ─────────────────────────────────────────────────────────────

def build_order_payload(order: dict, dry_run: bool = False) -> dict:
    """
    Build the Alpaca multi-leg order payload from an approval_manager order dict.
    Returns the payload dict (or raises ValueError if data is missing).
    """
    spread_type = order.get('spread_type', 'bull_call')
    symbol      = order['symbol']
    expiry      = order['expiry']
    long_strike = float(order['long_strike'])
    short_strike= float(order['short_strike'])
    spread_mid  = float(order['spread_mid'])

    if spread_type == 'bull_call':
        long_type  = 'call'
        short_type = 'call'
    else:  # bear_put
        long_type  = 'put'
        short_type = 'put'

    long_occ  = build_occ_symbol(symbol, expiry, long_type,  long_strike)
    short_occ = build_occ_symbol(symbol, expiry, short_type, short_strike)

    # Limit price = mid + small buffer (rounded to nearest cent)
    limit_price = round(spread_mid + SLIPPAGE_BUFFER * spread_mid, 2)
    limit_price = min(limit_price, float(order.get('long_ask', 9999)) -
                      float(order.get('short_bid', 0)))  # don't exceed natural price

    payload = {
        'order_class':   'mleg',
        'type':          'limit',
        'time_in_force': 'day',
        'limit_price':   str(round(limit_price, 2)),
        'legs': [
            {
                'symbol':           long_occ,
                'ratio_qty':        1,
                'side':             'buy',
                'position_effect':  'open',
            },
            {
                'symbol':           short_occ,
                'ratio_qty':        1,
                'side':             'sell',
                'position_effect':  'open',
            },
        ],
    }
    return payload


def submit_order(payload: dict) -> dict:
    """POST order to Alpaca. Returns response JSON."""
    r = requests.post(
        f'{ALPACA_BASE}/orders',
        headers=ALPACA_HEADERS,
        json=payload,
        timeout=15,
    )
    return {'status_code': r.status_code, 'body': r.json()}


# ─── Main execute flow ────────────────────────────────────────────────────────

def execute_trade(trade_id: str, dry_run: bool = False) -> bool:
    """
    Load approved order by trade_id, build Alpaca payload, submit.
    Returns True on success.
    """
    # Import here to avoid circular if called externally
    sys.path.insert(0, str(Path(__file__).parent))
    from approval_manager import get_by_id, mark_executed, mark_rejected

    order = get_by_id(trade_id)
    if not order:
        print(f'❌ Trade {trade_id} not found in pending_orders.json')
        return False

    if order['status'] != 'approved' and not dry_run:
        print(f'❌ Trade {trade_id} status is "{order["status"]}" — must be "approved" to execute')
        print('   Run: python3 approval_manager.py approve {trade_id}')
        return False

    try:
        payload = build_order_payload(order, dry_run=dry_run)
    except (KeyError, ValueError) as e:
        print(f'❌ Failed to build order payload: {e}')
        return False

    print(f'\n{"=" * 55}')
    print(f'{"DRY RUN — " if dry_run else ""}EXECUTING: {order["symbol"]} '
          f'${order["long_strike"]}/{order["short_strike"]} ({order["type_label"]})')
    print(f'Expiry: {order["expiry"]} ({order["dte"]} DTE)')
    print(f'Conviction: {order["conviction_score"]}/100 | Events: {order["events_status"]}')
    print(f'Limit Price: ${payload["limit_price"]} (mid ${order["spread_mid"]} + buffer)')
    print(f'Long leg:  {payload["legs"][0]["symbol"]}')
    print(f'Short leg: {payload["legs"][1]["symbol"]}')
    print(f'{"=" * 55}')

    if dry_run:
        print('\nDRY RUN — payload that would be sent:')
        print(json.dumps(payload, indent=2))
        print('\nNo order submitted.')
        return True

    # Final sanity check
    print('\n⚠️  Submitting live order to Alpaca paper account...')
    response = submit_order(payload)

    if response['status_code'] in (200, 201):
        alpaca_id = response['body'].get('id', 'unknown')
        print(f'✅ Order accepted! Alpaca ID: {alpaca_id}')
        mark_executed(trade_id, alpaca_id)

        # Log to vault
        _log_execution(order, payload, alpaca_id)
        return True
    else:
        error_msg = response['body'].get('message', str(response['body']))
        print(f'❌ Order rejected by Alpaca: {error_msg}')
        print(f'   Status code: {response["status_code"]}')
        # Don't auto-reject — let human decide next step
        return False


def _log_execution(order: dict, payload: dict, alpaca_id: str):
    """Append execution record to vault log."""
    log_path = Path('/home/ubuntu/openclaw-vault/OpenClaw/10_Execution_Log.md')
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    entry = f"""
## {now} — {order['symbol']} {order['type_label']} EXECUTED

| Field | Value |
|-------|-------|
| Trade ID | {order['trade_id']} |
| Symbol | {order['symbol']} |
| Strikes | ${order['long_strike']} / ${order['short_strike']} |
| Expiry | {order['expiry']} ({order['dte']} DTE) |
| Debit Paid | ${order['spread_mid']} (limit ${payload['limit_price']}) |
| Max Profit | ${order['max_profit']} | R:R | {order['rr']}:1 |
| Conviction | {order['conviction_score']}/100 |
| Alpaca Order ID | {alpaca_id} |

---
"""
    if log_path.exists():
        existing = log_path.read_text()
        log_path.write_text(f'# Execution Log\n{entry}' + existing.replace('# Execution Log\n', ''))
    else:
        log_path.write_text(f'# Execution Log\n{entry}')
    print(f'✅ Logged to 10_Execution_Log.md')


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    args = sys.argv[1:]

    if len(args) >= 2 and args[0] == 'execute':
        success = execute_trade(args[1], dry_run=False)
        sys.exit(0 if success else 1)

    elif len(args) >= 2 and args[0] == 'dry-run':
        execute_trade(args[1], dry_run=True)

    elif len(args) >= 2 and args[0] == 'close':
        # Cancel an existing Alpaca order by ID
        alpaca_id = args[1]
        r = requests.delete(
            f'{ALPACA_BASE}/orders/{alpaca_id}',
            headers=ALPACA_HEADERS, timeout=10)
        if r.status_code in (200, 204):
            print(f'✅ Alpaca order {alpaca_id} cancelled')
        else:
            print(f'❌ Cancel failed: {r.status_code} {r.text}')
    else:
        print('Usage:')
        print('  executor.py execute <trade_id>     # execute approved order')
        print('  executor.py dry-run <trade_id>     # simulate only')
        print('  executor.py close   <alpaca_id>    # cancel Alpaca order')
