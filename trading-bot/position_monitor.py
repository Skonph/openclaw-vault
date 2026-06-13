#!/usr/bin/env python3
"""
OpenClaw Position Monitor v1.0

Checks open options positions via Alpaca paper API.
Compares current P&L against original debit paid.
Writes alerts to vault if stop or DTE threshold is hit.

Stop rule:     close if position value drops to 20% of debit paid (80% loss)
DTE rule:      alert at DTE ≤ 7 (close or manage)
Profit target: alert at DTE ≤ 21 if position up ≥ 50% of max profit

Runs as cron alongside scanner (add to crontab, same time or 5 min after).

Usage:
  python3 position_monitor.py              # run check, write alerts to vault
  python3 position_monitor.py --stdout     # print only, don't write to vault
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv('/home/ubuntu/trading-bot/.env')

ALPACA_KEY    = os.environ.get('ALPACA_API_KEY', '')
ALPACA_SECRET = os.environ.get('ALPACA_SECRET_KEY', '')
ALPACA_BASE   = os.environ.get('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets/v2')

ALPACA_HEADERS = {
    'APCA-API-KEY-ID':     ALPACA_KEY,
    'APCA-API-SECRET-KEY': ALPACA_SECRET,
    'Accept':              'application/json',
}

VAULT_DIR   = Path('/home/ubuntu/openclaw-vault')
MONITOR_LOG = VAULT_DIR / 'OpenClaw/11_Position_Monitor.md'

STOP_LOSS_PCT   = 0.20   # close if position value < 20% of debit (i.e., -80% loss)
DTE_CLOSE_ALERT = 7      # alert: manage or close position
DTE_PROFIT_EVAL = 21     # evaluate early close if in profit at this DTE
PROFIT_TARGET   = 0.50   # close early if unrealised profit ≥ 50% of max profit


# ─── Alpaca helpers ───────────────────────────────────────────────────────────

def get_positions() -> list[dict]:
    """Fetch all open positions from Alpaca paper account."""
    try:
        r = requests.get(f'{ALPACA_BASE}/positions', headers=ALPACA_HEADERS, timeout=10)
        if r.status_code == 200:
            return r.json()
        print(f'⚠️  Alpaca positions: HTTP {r.status_code}')
        return []
    except Exception as e:
        print(f'⚠️  Alpaca positions error: {e}')
        return []


def get_account() -> dict:
    """Fetch account info (equity, buying power, etc.)."""
    try:
        r = requests.get(f'{ALPACA_BASE}/account', headers=ALPACA_HEADERS, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


# ─── Position parsing ─────────────────────────────────────────────────────────

def parse_option_position(pos: dict) -> dict | None:
    """
    Parse an Alpaca position dict for an options position.
    Returns structured dict or None if not an option.
    """
    asset_class = pos.get('asset_class', '')
    if asset_class != 'us_option':
        return None

    symbol = pos.get('symbol', '')  # OCC symbol e.g. NCLH260620C00017000

    # Parse OCC symbol to extract underlying, expiry, type, strike
    try:
        # Format: {TICKER}{YY}{MM}{DD}{C|P}{8digits}
        # Find where digits start after the ticker
        i = 0
        while i < len(symbol) and not symbol[i].isdigit():
            i += 1
        underlying = symbol[:i]
        date_part  = symbol[i:i+6]           # YYMMDD
        cp         = symbol[i+6]             # C or P
        strike_raw = symbol[i+7:i+15]        # 8 digits

        expiry_dt  = datetime.strptime(date_part, '%y%m%d')
        expiry_str = expiry_dt.strftime('%Y-%m-%d')
        dte        = (expiry_dt - datetime.now()).days
        strike     = int(strike_raw) / 1000.0
        option_type = 'call' if cp == 'C' else 'put'
    except Exception:
        underlying  = symbol
        expiry_str  = 'unknown'
        dte         = -1
        strike      = 0.0
        option_type = 'unknown'

    qty          = int(pos.get('qty', 0))
    side         = pos.get('side', '')           # 'long' or 'short'
    avg_entry    = float(pos.get('avg_entry_price', 0) or 0)
    current_price= float(pos.get('current_price', 0) or 0)
    market_value = float(pos.get('market_value', 0) or 0)
    unrealised   = float(pos.get('unrealized_pl', 0) or 0)
    cost_basis   = float(pos.get('cost_basis', 0) or 0)

    return {
        'occ_symbol':  symbol,
        'underlying':  underlying,
        'expiry':      expiry_str,
        'dte':         dte,
        'strike':      strike,
        'option_type': option_type,
        'qty':         qty,
        'side':        side,
        'avg_entry':   avg_entry,
        'current_price': current_price,
        'market_value':  market_value,
        'unrealised_pl': unrealised,
        'cost_basis':    cost_basis,
    }


def group_spread_legs(positions: list[dict]) -> dict[str, list]:
    """
    Group option positions by underlying + expiry into spread groups.
    Returns dict: { 'NCLH_2026-06-20': [long_leg, short_leg] }
    """
    groups: dict[str, list] = {}
    for pos in positions:
        parsed = parse_option_position(pos)
        if parsed is None:
            continue
        key = f'{parsed["underlying"]}_{parsed["expiry"]}'
        groups.setdefault(key, []).append(parsed)
    return groups


# ─── Alert generation ─────────────────────────────────────────────────────────

def analyse_spread_group(key: str, legs: list[dict]) -> dict | None:
    """
    Analyse a group of option legs for one spread.
    Returns alert dict or None if no action needed.
    """
    if not legs:
        return None

    # Sum market value and cost basis across all legs
    total_market_value = sum(l['market_value'] for l in legs)
    total_cost_basis   = sum(l['cost_basis']   for l in legs)
    total_unrealised   = sum(l['unrealised_pl'] for l in legs)

    # DTE from the first leg (all legs share expiry)
    dte = legs[0]['dte']
    underlying = legs[0]['underlying']
    expiry     = legs[0]['expiry']

    # Debit paid = absolute value of net cost (negative cost_basis = credit received on spread)
    debit_paid = abs(total_cost_basis)
    if debit_paid == 0:
        return None

    # Stop loss: position value fell to ≤ 20% of debit
    current_value = total_market_value
    value_pct     = current_value / debit_paid if debit_paid else 1.0

    alerts = []

    if value_pct <= STOP_LOSS_PCT:
        alerts.append({
            'level':   '🔴 STOP LOSS',
            'message': f'Position value ${current_value:.2f} = {value_pct*100:.0f}% of debit '
                       f'(${debit_paid:.2f}). Rule: close at ≤20%.',
            'action':  'CLOSE POSITION',
        })

    if dte <= DTE_CLOSE_ALERT and dte >= 0:
        alerts.append({
            'level':   '⚠️  DTE ALERT',
            'message': f'{dte} days to expiry. Rule: manage or close at DTE ≤7.',
            'action':  'EVALUATE CLOSE / ROLL',
        })

    # Profit target: if in profit at DTE ≤ 21, consider closing
    if dte <= DTE_PROFIT_EVAL and total_unrealised > 0:
        # Estimate max profit as spread width × 100 × qty (rough)
        profit_pct = total_unrealised / debit_paid
        if profit_pct >= PROFIT_TARGET:
            alerts.append({
                'level':   '💰 PROFIT TARGET',
                'message': f'Up ${total_unrealised:.2f} ({profit_pct*100:.0f}% of debit) '
                           f'with {dte} DTE. Consider early close.',
                'action':  'EVALUATE EARLY CLOSE',
            })

    if not alerts:
        return None  # position healthy, no action needed

    return {
        'key':        key,
        'underlying': underlying,
        'expiry':     expiry,
        'dte':        dte,
        'legs':       len(legs),
        'debit_paid': debit_paid,
        'current_value': current_value,
        'unrealised_pl': total_unrealised,
        'alerts':     alerts,
    }


# ─── Vault output ─────────────────────────────────────────────────────────────

def write_monitor_report(groups: dict, account: dict, action_items: list):
    """Write position monitor status to vault markdown file."""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    equity = float(account.get('equity', 0) or 0)
    buying_power = float(account.get('buying_power', 0) or 0)

    report = f'# Position Monitor\n**Last run:** {now}\n\n---\n\n'
    report += f'## Account\n- Equity: ${equity:,.2f}\n- Buying Power: ${buying_power:,.2f}\n\n'

    if not groups:
        report += '## Positions\nNo open options positions.\n\n'
    else:
        report += f'## Open Positions ({len(groups)} spread(s))\n\n'
        for key, legs in groups.items():
            underlying = legs[0]['underlying']
            expiry     = legs[0]['expiry']
            dte        = legs[0]['dte']
            total_mv   = sum(l['market_value'] for l in legs)
            total_pl   = sum(l['unrealised_pl'] for l in legs)
            report += f'### {underlying} — {expiry} ({dte} DTE)\n'
            report += f'- Market Value: ${total_mv:.2f} | Unrealised P&L: ${total_pl:+.2f}\n'
            for leg in legs:
                report += (f'  - {leg["side"].upper()} {leg["option_type"].upper()} '
                           f'${leg["strike"]} × {leg["qty"]} '
                           f'@ ${leg["avg_entry"]:.2f} | now ${leg["current_price"]:.2f}\n')
            report += '\n'

    if action_items:
        report += '## ⚠️ Action Required\n\n'
        for item in action_items:
            for alert in item['alerts']:
                report += f'### {alert["level"]} — {item["underlying"]}\n'
                report += f'- {alert["message"]}\n'
                report += f'- **Action: {alert["action"]}**\n\n'
    else:
        report += '## Status\nAll positions within normal parameters. No action required.\n\n'

    report += '---\n*Auto-generated by position_monitor.py*\n'

    MONITOR_LOG.parent.mkdir(parents=True, exist_ok=True)
    MONITOR_LOG.write_text(report)
    print(f'✅ Position monitor report written to 11_Position_Monitor.md')
    return report


# ─── Main ─────────────────────────────────────────────────────────────────────

def run(stdout_only: bool = False):
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n{"="*50}')
    print(f'POSITION MONITOR — {now}')
    print(f'{"="*50}\n')

    account   = get_account()
    positions = get_positions()

    if not positions:
        print('No open positions on Alpaca paper account.\n')
        if not stdout_only:
            write_monitor_report({}, account, [])
        return

    groups = group_spread_legs(positions)
    print(f'Found {len(positions)} option position(s) across {len(groups)} spread(s)\n')

    action_items = []
    for key, legs in groups.items():
        result = analyse_spread_group(key, legs)
        symbol = legs[0]['underlying']
        dte    = legs[0]['dte']
        if result:
            action_items.append(result)
            print(f'⚠️  {symbol} {legs[0]["expiry"]} ({dte} DTE) — {len(result["alerts"])} alert(s):')
            for a in result['alerts']:
                print(f'   {a["level"]}: {a["message"]}')
        else:
            total_pl = sum(l['unrealised_pl'] for l in legs)
            print(f'✅ {symbol} {legs[0]["expiry"]} ({dte} DTE) — OK | P&L: ${total_pl:+.2f}')

    if not stdout_only:
        write_monitor_report(groups, account, action_items)

    if action_items:
        print(f'\n🔔 {len(action_items)} position(s) need attention — check 11_Position_Monitor.md')
    else:
        print('\n✅ All positions healthy')

    print(f'\n{"="*50}\n')


if __name__ == '__main__':
    stdout_only = '--stdout' in sys.argv
    run(stdout_only=stdout_only)
