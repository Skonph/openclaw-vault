#!/usr/bin/env python3
"""
Nova Executor — Local approval & execution for OpenClaw via Cowork/Nova

Reads Alpaca credentials from openclaw/.env.local (fill in once, never commit).
Reads/writes OpenClaw/pending_orders.json relative to this script.

Usage:
  python3 nova_executor.py list                    # show pending orders
  python3 nova_executor.py show    <trade_id>      # full JSON for one order
  python3 nova_executor.py dry-run <trade_id>      # build payload without submitting
  python3 nova_executor.py execute <trade_id>      # approve + submit to Alpaca
  python3 nova_executor.py reject  <trade_id> [reason]
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

# ── Paths (all relative to this script so they work in Cowork sandbox too) ────
SCRIPT_DIR   = Path(__file__).resolve().parent
VAULT_DIR    = SCRIPT_DIR.parent

# Default relative paths
PENDING_FILE = VAULT_DIR / 'OpenClaw/pending_orders.json'
ENV_FILE     = SCRIPT_DIR / '.env.local'
LOG_FILE     = VAULT_DIR / 'OpenClaw/10_Execution_Log.md'

# Server-side fallback paths if default doesn't exist
SERVER_VAULT_PENDING = Path('/home/ubuntu/openclaw-vault/OpenClaw/pending_orders.json')
SERVER_VAULT_LOG     = Path('/home/ubuntu/openclaw-vault/OpenClaw/10_Execution_Log.md')

if not PENDING_FILE.exists() and SERVER_VAULT_PENDING.exists():
    PENDING_FILE = SERVER_VAULT_PENDING
if not LOG_FILE.parent.exists() and SERVER_VAULT_LOG.parent.exists():
    LOG_FILE = SERVER_VAULT_LOG


# ── Load credentials from .env or .env.local ──────────────────────────────────
def _load_env() -> dict:
    env = {}
    # Try .env first, then override with .env.local if present
    for fname in ('.env', '.env.local'):
        p = SCRIPT_DIR / fname
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    return env

_ENV          = _load_env()
ALPACA_KEY    = _ENV.get('ALPACA_KEY')    or _ENV.get('ALPACA_API_KEY')    or os.environ.get('ALPACA_KEY', '') or os.environ.get('ALPACA_API_KEY', '')
ALPACA_SECRET = _ENV.get('ALPACA_SECRET') or _ENV.get('ALPACA_SECRET_KEY') or os.environ.get('ALPACA_SECRET', '') or os.environ.get('ALPACA_SECRET_KEY', '')
ALPACA_BASE   = (_ENV.get('ALPACA_BASE') or os.environ.get('ALPACA_BASE', 'https://paper-api.alpaca.markets')).rstrip('/').removesuffix('/v2')


# ── Helpers ───────────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M')

def _load_orders() -> dict:
    if not PENDING_FILE.exists():
        return {'orders': [], 'updated': _now()}
    return json.loads(PENDING_FILE.read_text())

def _save_orders(data: dict):
    data['updated'] = _now()
    PENDING_FILE.write_text(json.dumps(data, indent=2))

def _alpaca_headers() -> dict:
    return {
        'APCA-API-KEY-ID':     ALPACA_KEY,
        'APCA-API-SECRET-KEY': ALPACA_SECRET,
        'Content-Type':        'application/json',
    }

def _find_order(trade_id: str) -> tuple[dict | None, dict | None]:
    """Returns (data_dict, order_dict) or (data, None) if not found."""
    data = _load_orders()
    for o in data['orders']:
        if o['trade_id'].upper() == trade_id.upper():
            return data, o
    return data, None


# ── OCC symbol builder ─────────────────────────────────────────────────────────
def build_occ(symbol: str, expiry: str, opt_type: str, strike: float) -> str:
    """
    Builds OCC option symbol.
      symbol   : underlying ticker, e.g. 'NCLH'
      expiry   : 'YYYY-MM-DD'
      opt_type : 'call' or 'put'
      strike   : float, e.g. 17.0
    Returns e.g. 'NCLH260620C00017000'
    """
    exp_digits = expiry.replace('-', '')[2:]          # YYMMDD
    ot         = 'C' if opt_type.lower().startswith('c') else 'P'
    strike_str = f'{int(round(strike * 1000)):08d}'
    return f'{symbol}{exp_digits}{ot}{strike_str}'


# ── Build Alpaca mleg payload ──────────────────────────────────────────────────
def _build_payload(order: dict, qty: int = 1) -> dict:
    spread_type = order.get('spread_type', 'bull_call')

    if spread_type == 'iron_condor':
        # Validate all 4 strike fields are present
        required = ['put_long_strike', 'put_short_strike', 'call_short_strike', 'call_long_strike']
        missing  = [f for f in required if not order.get(f)]
        if missing:
            raise ValueError(f'IC missing fields: {missing}')

        symbol = order['symbol']
        expiry = order['expiry']
        put_long_occ   = build_occ(symbol, expiry, 'put',  float(order['put_long_strike']))
        put_short_occ  = build_occ(symbol, expiry, 'put',  float(order['put_short_strike']))
        call_short_occ = build_occ(symbol, expiry, 'call', float(order['call_short_strike']))
        call_long_occ  = build_occ(symbol, expiry, 'call', float(order['call_long_strike']))

        # IC is a credit — limit_price is min net credit we accept (95% of expected)
        net_credit = float(order['spread_mid'])
        limit_px   = round(max(net_credit * 0.95, 0.01), 2)

        return {
            'order_class':   'mleg',
            'qty':           str(qty),
            'type':          'limit',
            'time_in_force': 'day',
            'limit_price':   str(limit_px),
            'legs': [
                {'symbol': put_long_occ,   'ratio_qty': 1, 'side': 'buy',  'position_effect': 'open'},
                {'symbol': put_short_occ,  'ratio_qty': 1, 'side': 'sell', 'position_effect': 'open'},
                {'symbol': call_short_occ, 'ratio_qty': 1, 'side': 'sell', 'position_effect': 'open'},
                {'symbol': call_long_occ,  'ratio_qty': 1, 'side': 'buy',  'position_effect': 'open'},
            ],
        }

    is_bull   = spread_type == 'bull_call'
    otype     = 'call' if is_bull else 'put'
    long_occ  = build_occ(order['symbol'], order['expiry'], otype, order['long_strike'])
    short_occ = build_occ(order['symbol'], order['expiry'], otype, order['short_strike'])
    limit_px  = round(float(order['spread_mid']) * 1.08, 2)

    return {
        'order_class':   'mleg',
        'qty':           str(qty),
        'type':          'limit',
        'time_in_force': 'day',
        'limit_price':   str(limit_px),
        'legs': [
            {'symbol': long_occ,  'ratio_qty': 1, 'side': 'buy',  'position_effect': 'open'},
            {'symbol': short_occ, 'ratio_qty': 1, 'side': 'sell', 'position_effect': 'open'},
        ],
    }


# ── cmd: list ─────────────────────────────────────────────────────────────────
def cmd_list():
    data    = _load_orders()
    pending = [o for o in data['orders'] if o['status'] == 'pending']
    today   = datetime.now().strftime('%Y-%m-%d')
    recent  = [o for o in data['orders']
               if o['status'] in ('approved', 'rejected', 'executed')
               and o.get('created', '')[:10] == today]

    print(f'\n{"═"*62}')
    print(f'  OpenClaw — Pending Orders          {_now()}')
    print(f'{"═"*62}')

    if not pending:
        print('\n  ✅  No pending orders.\n')
    else:
        for o in pending:
            ev_icon    = {'clear': '✅', 'blocked': '🚫', 'uncertain': '⚠️ '}.get(o['events_status'], '❓')
            is_credit  = o.get('spread_type') == 'iron_condor'
            cost_label = 'Credit' if is_credit else 'Debit '
            # IC: show all 4 strikes; others: show 2
            if is_credit and o.get('put_short_strike'):
                strikes = (f'put ${o["put_long_strike"]}/${o["put_short_strike"]} | '
                           f'call ${o["call_short_strike"]}/${o["call_long_strike"]}')
            else:
                strikes = f'${o["long_strike"]} / ${o["short_strike"]}'
            print(f'\n  ┌─ [{o["trade_id"]}]  {o["symbol"]}  {o["type_label"]}')
            print(f'  │  Strikes : {strikes}  Expiry: {o["expiry"]}  DTE: {o["dte"]}')
            print(f'  │  {cost_label} : ${o["spread_mid"]:.2f}   R:R {o["rr"]:.2f}   '
                  f'Max profit ${o["max_profit"]:.2f}')
            if is_credit and o.get('execution_note'):
                print(f'  │  ⚠️   {o["execution_note"]}')
            print(f'  │  Conv    : {o["conviction_score"]}/100   '
                  f'Events: {ev_icon} {o["events_status"].upper()}')
            print(f'  │  OI L/S  : {o.get("long_oi","?")}/{o.get("short_oi","?")}   '
                  f'IV L/S: {o.get("long_iv","?")}%/{o.get("short_iv","?")}%')
            if o['events_status'] == 'uncertain':
                print(f'  │  ⚠️  EVENTS UNCERTAIN — verify in IBKR before approving')
            print(f'  └─ Created: {o["created"]}   Expires: {o["expires"]}')

    if recent:
        print(f'\n  — Today\'s completed activity —')
        for o in recent:
            icon = {'approved': '✅', 'rejected': '❌', 'executed': '🚀'}.get(o['status'], '•')
            print(f'  {icon}  [{o["trade_id"]}]  {o["symbol"]}  → {o["status"].upper()}')

    print(f'\n  Vault: {PENDING_FILE}\n')


# ── cmd: show ─────────────────────────────────────────────────────────────────
def cmd_show(trade_id: str):
    _, order = _find_order(trade_id)
    if order:
        print(json.dumps(order, indent=2))
    else:
        print(f'❌  Order {trade_id} not found')


# ── cmd: dry-run ──────────────────────────────────────────────────────────────
def cmd_dry_run(trade_id: str):
    _, order = _find_order(trade_id)
    if not order:
        print(f'❌  Order {trade_id} not found')
        return
    payload = _build_payload(order)
    is_ic   = order.get('spread_type') == 'iron_condor'
    print(f'\n  DRY RUN — {trade_id}  ({order["symbol"]} {order["type_label"]})')
    print(f'  Endpoint : POST {ALPACA_BASE}/v2/orders')
    if is_ic:
        limit_note = 'net credit × 0.95 (min credit floor)'
        for leg in payload['legs']:
            action = 'buy ' if leg['side'] == 'buy' else 'sell'
            print(f'  Leg      : {leg["symbol"]}  ({action})')
    else:
        limit_note = 'spread_mid × 1.08'
        print(f'  Legs     : {payload["legs"][0]["symbol"]}  (buy)')
        print(f'           : {payload["legs"][1]["symbol"]}  (sell)')
    print(f'  Limit    : ${payload["limit_price"]}  ({limit_note})')
    print(f'\n  Full payload:\n{json.dumps(payload, indent=4)}\n')


# ── cmd: execute ──────────────────────────────────────────────────────────────
def cmd_execute(trade_id: str, qty: int = 1):
    if not ALPACA_KEY or not ALPACA_SECRET:
        print(f'❌  ALPACA_KEY / ALPACA_SECRET not set.')
        print(f'    Create {ENV_FILE} with those keys.')
        sys.exit(1)

    data, order = _find_order(trade_id)
    if not order:
        print(f'❌  Order {trade_id} not found')
        sys.exit(1)

    if order['status'] not in ('pending', 'approved', 'skipped'):
        print(f'⚠️   Order is already [{order["status"]}] — cannot execute')
        sys.exit(1)

    payload = _build_payload(order, qty=qty)
    is_ic   = order.get('spread_type') == 'iron_condor'

    print(f'\n  Submitting [{trade_id}]  {order["symbol"]}  {order["type_label"]} (Qty: {qty}) …')
    if is_ic:
        for leg in payload['legs']:
            action = 'Buy ' if leg['side'] == 'buy' else 'Sell'
            print(f'  {action} : {leg["symbol"]}')
    else:
        long_occ  = payload['legs'][0]['symbol']
        short_occ = payload['legs'][1]['symbol']
        print(f'  Buy  : {long_occ}')
        print(f'  Sell : {short_occ}')
    print(f'  Limit: ${payload["limit_price"]}')

    r = requests.post(
        f'{ALPACA_BASE}/v2/orders',
        headers=_alpaca_headers(),
        json=payload,
        timeout=15,
    )

    if r.status_code in (200, 201):
        alpaca_id = r.json().get('id', 'unknown')
        order.update({
            'status':          'executed',
            'executed_at':     _now(),
            'alpaca_order_id': alpaca_id,
            'approved_by':     'Nova',
            'approved_at':     _now(),
        })
        _save_orders(data)

        # Append to execution log
        if is_ic:
            strikes_label = (f'put ${order["put_long_strike"]}/${order["put_short_strike"]} | '
                             f'call ${order["call_short_strike"]}/${order["call_long_strike"]}')
            cost_label = f'- **Credit**: ${order["spread_mid"]:.2f}'
        else:
            strikes_label = f'${order["long_strike"]} / ${order["short_strike"]}'
            cost_label = f'- **Debit**: ${order["spread_mid"]:.2f}'

        log_entry = (
            f'\n## {_now()} — [{trade_id}] approved via Nova\n'
            f'- **Symbol**: {order["symbol"]}  {order["type_label"]}\n'
            f'- **Qty**: {qty}\n'
            f'- **Strikes**: {strikes_label}\n'
            f'- **Expiry**: {order["expiry"]}  DTE: {order["dte"]}\n'
            f'{cost_label}  R:R: {order["rr"]:.1f}x\n'
            f'- **Conviction**: {order["conviction_score"]}/100\n'
            f'- **Alpaca order ID**: {alpaca_id}\n'
            f'- **Approved by**: Nova (Cowork)\n'
        )
        if LOG_FILE.parent.exists():
            with open(LOG_FILE, 'a') as f:
                f.write(log_entry)

        print(f'\n  🚀  Executed!  Alpaca order ID: {alpaca_id}')
        print(f'  ✅  pending_orders.json updated')
        print(f'  📝  Appended to {LOG_FILE.name}\n')

    else:
        print(f'\n  ❌  Alpaca API error {r.status_code}:')
        print(f'  {r.text[:600]}\n')
        sys.exit(1)


# ── cmd: reject ───────────────────────────────────────────────────────────────
def cmd_reject(trade_id: str, reason: str = 'Rejected via Nova'):
    data, order = _find_order(trade_id)
    if not order:
        print(f'❌  Order {trade_id} not found')
        return
    order.update({
        'status':        'rejected',
        'rejected_by':   'Nova',
        'rejected_at':   _now(),
        'reject_reason': reason,
    })
    _save_orders(data)
    print(f'  ❌  [{trade_id}] rejected: {reason}')


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    args = sys.argv[1:]

    if not args or args[0] == 'list':
        cmd_list()
    elif args[0] == 'show' and len(args) >= 2:
        cmd_show(args[1])
    elif args[0] == 'dry-run' and len(args) >= 2:
        cmd_dry_run(args[1])
    elif args[0] == 'execute' and len(args) >= 2:
        qty = 1
        if len(args) >= 3:
            try:
                qty = int(args[2])
            except ValueError:
                pass
        cmd_execute(args[1], qty=qty)
    elif args[0] == 'reject' and len(args) >= 2:
        reason = ' '.join(args[2:]) if len(args) > 2 else 'Rejected via Nova'
        cmd_reject(args[1], reason)
    else:
        print('Usage: nova_executor.py [list | show <id> | dry-run <id> | execute <id> [qty] | reject <id> [reason]]')
