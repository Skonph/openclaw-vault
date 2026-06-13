#!/usr/bin/env python3
"""
OpenClaw Approval Manager

Manages the pending_orders.json queue in the vault.
Scanner writes here when a trade passes all automated checks.
Cowork artifact reads here to show Approve / Reject buttons.
executor.py reads here when executing an approved trade.

File location: /home/ubuntu/openclaw-vault/OpenClaw/pending_orders.json
Git push: handled by vault_updater.py after scanner runs.

Usage (standalone):
  python3 approval_manager.py list
  python3 approval_manager.py approve <trade_id>
  python3 approval_manager.py reject  <trade_id> [reason]
"""

import json
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

VAULT_DIR    = Path('/home/ubuntu/openclaw-vault')
PENDING_FILE = VAULT_DIR / 'OpenClaw/pending_orders.json'


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _load() -> dict:
    """Load pending_orders.json. Returns {'orders': [...], 'updated': str}."""
    if not PENDING_FILE.exists():
        return {'orders': [], 'updated': _now()}
    try:
        return json.loads(PENDING_FILE.read_text())
    except Exception:
        return {'orders': [], 'updated': _now()}


def _save(data: dict):
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    data['updated'] = _now()
    PENDING_FILE.write_text(json.dumps(data, indent=2))


def _now() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M')


# ─── Write new pending approval ───────────────────────────────────────────────

def write_pending(alert: dict, conviction: dict, events_result: dict) -> str:
    """
    Add a trade to the pending queue.
    Returns the trade_id assigned.

    alert:          spread dict from scanner (symbol, strikes, expiry, spread_mid, etc.)
    conviction:     result from conviction_scorer.score_conviction()
    events_result:  result from events_checker.check_one()
    """
    trade_id = str(uuid.uuid4())[:8].upper()
    spread_type = alert.get('spread_type', 'bull_call')
    _labels     = {'bull_call': 'Bull Call', 'bear_put': 'Bear Put', 'iron_condor': 'Iron Condor'}
    type_label  = _labels.get(spread_type, spread_type.replace('_', ' ').title())

    order = {
        'trade_id':       trade_id,
        'status':         'pending',           # pending | approved | rejected | executed | expired
        'created':        _now(),
        'expires':        _expiry_time(),      # auto-expire after market close

        # Trade details
        'symbol':         alert['symbol'],
        'spread_type':    spread_type,
        'type_label':     type_label,
        'price':          alert.get('price'),
        'expiry':         alert.get('expiry', ''),
        'dte':            alert.get('dte', 0),
        'long_strike':    alert['long_strike'],
        'short_strike':   alert['short_strike'],
        'spread_mid':     alert['spread_mid'],
        'max_profit':     alert['max_profit'],
        'rr':             alert['rr'],

        # Option leg details (for executor.py to build OCC symbols)
        'long_bid':       alert.get('long_bid'),
        'long_ask':       alert.get('long_ask'),
        'short_bid':      alert.get('short_bid'),
        'short_ask':      alert.get('short_ask'),
        'long_oi':        alert.get('long_oi'),
        'short_oi':       alert.get('short_oi'),
        'long_iv':        alert.get('long_iv'),
        'short_iv':       alert.get('short_iv'),

        # Iron Condor — 4-strike fields (only populated for IC orders)
        'put_long_strike':   alert.get('put_long_strike'),
        'put_short_strike':  alert.get('put_short_strike'),
        'call_short_strike': alert.get('call_short_strike'),
        'call_long_strike':  alert.get('call_long_strike'),
        'call_credit':       alert.get('call_credit'),
        'put_credit':        alert.get('put_credit'),
        'execution_note':    alert.get('execution_note'),   # '4-leg order — executor update required'

        # Conviction
        'conviction_score':  conviction.get('score'),
        'conviction_pass':   conviction.get('pass'),
        'conviction_mode':   conviction.get('mode'),
        'conviction_reason': conviction.get('reasoning', ''),

        # Events
        'events_status':  events_result.get('status', 'uncertain'),
        'events_reason':  events_result.get('reason', ''),

        # Approval
        'approved_by':    None,
        'approved_at':    None,
        'rejected_by':    None,
        'rejected_at':    None,
        'reject_reason':  None,
        'executed_at':    None,
        'alpaca_order_id': None,
    }

    data = _load()

    # Remove any previous pending order for same symbol (avoid duplicates from re-runs)
    data['orders'] = [o for o in data['orders']
                      if not (o['symbol'] == alert['symbol'] and o['status'] == 'pending')]

    data['orders'].append(order)
    _save(data)
    print(f'✅ Pending order written: {trade_id} — {alert["symbol"]} '
          f'${alert["long_strike"]}/{alert["short_strike"]} ({type_label}) '
          f'conviction {conviction["score"]}/100')
    return trade_id


def _expiry_time() -> str:
    """Orders expire 6 hours after creation — timezone-safe regardless of server locale."""
    return (datetime.now() + timedelta(hours=6)).strftime('%Y-%m-%d %H:%M')


# ─── Read queue ───────────────────────────────────────────────────────────────

def read_pending() -> list[dict]:
    """Return list of orders with status == 'pending'."""
    return [o for o in _load()['orders'] if o['status'] == 'pending']


def read_all() -> list[dict]:
    """Return all orders (all statuses)."""
    return _load()['orders']


def get_by_id(trade_id: str) -> dict | None:
    """Find a specific order by trade_id."""
    for o in _load()['orders']:
        if o['trade_id'].upper() == trade_id.upper():
            return o
    return None


# ─── Status updates ───────────────────────────────────────────────────────────

def mark_approved(trade_id: str, approved_by: str = 'Skon') -> bool:
    data = _load()
    for order in data['orders']:
        if order['trade_id'].upper() == trade_id.upper():
            if order['status'] != 'pending':
                print(f'⚠️  Order {trade_id} is already {order["status"]}')
                return False
            order['status']      = 'approved'
            order['approved_by'] = approved_by
            order['approved_at'] = _now()
            _save(data)
            print(f'✅ Order {trade_id} approved — ready for execution')
            return True
    print(f'❌ Order {trade_id} not found')
    return False


def mark_rejected(trade_id: str, reason: str = 'Manual reject', rejected_by: str = 'Skon') -> bool:
    data = _load()
    for order in data['orders']:
        if order['trade_id'].upper() == trade_id.upper():
            order['status']      = 'rejected'
            order['rejected_by'] = rejected_by
            order['rejected_at'] = _now()
            order['reject_reason'] = reason
            _save(data)
            print(f'❌ Order {trade_id} rejected: {reason}')
            return True
    print(f'❌ Order {trade_id} not found')
    return False


def mark_executed(trade_id: str, alpaca_order_id: str):
    data = _load()
    for order in data['orders']:
        if order['trade_id'].upper() == trade_id.upper():
            order['status']          = 'executed'
            order['executed_at']     = _now()
            order['alpaca_order_id'] = alpaca_order_id
            _save(data)
            print(f'🚀 Order {trade_id} marked executed (Alpaca: {alpaca_order_id})')
            return
    print(f'❌ Order {trade_id} not found for mark_executed')


def expire_old_orders():
    """Mark any pending orders past their expiry as expired. Call at start of each scan."""
    data  = _load()
    now   = datetime.now().strftime('%Y-%m-%d %H:%M')
    count = 0
    for order in data['orders']:
        if order['status'] == 'pending' and order.get('expires') and order['expires'] < now:
            order['status'] = 'expired'
            count += 1
    if count:
        _save(data)
        print(f'ℹ️  Expired {count} old pending order(s)')


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    args = sys.argv[1:]

    if not args or args[0] == 'list':
        pending = read_pending()
        all_orders = read_all()
        print(f'\n=== Pending Orders — {_now()} ===\n')
        if not pending:
            print('No pending orders.\n')
        for o in pending:
            print(f'  [{o["trade_id"]}] {o["symbol"]} ${o["long_strike"]}/{o["short_strike"]} '
                  f'({o["type_label"]}) | conviction {o["conviction_score"]}/100 | '
                  f'created {o["created"]} | expires {o["expires"]}')
        if len(all_orders) > len(pending):
            print(f'\n  ({len(all_orders) - len(pending)} historical orders in archive)')

    elif args[0] == 'approve' and len(args) >= 2:
        mark_approved(args[1])

    elif args[0] == 'reject' and len(args) >= 2:
        reason = ' '.join(args[2:]) if len(args) > 2 else 'Manual reject'
        mark_rejected(args[1], reason)

    elif args[0] == 'show' and len(args) >= 2:
        order = get_by_id(args[1])
        if order:
            print(json.dumps(order, indent=2))
        else:
            print(f'Order {args[1]} not found')

    else:
        print('Usage: approval_manager.py [list | approve <id> | reject <id> [reason] | show <id>]')
