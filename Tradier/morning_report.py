#!/usr/bin/env python3
"""
OpenClaw Morning Report v1.0

Sends a full daily digest via Telegram at 7:30 AM ICT (after US market close).
Covers: account equity, open positions + P&L, yesterday's trades, upcoming expirations,
        last scan context (VIX/SPY/regime).

Cron (server): 30 0 * * 2-6
  = 00:30 UTC = 07:30 ICT, runs Tue-Sat (covers Mon-Fri US market sessions)

Usage:
  python3 morning_report.py
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv('/home/ubuntu/trading-bot/.env')

VAULT_DIR    = Path('/home/ubuntu/openclaw-vault')
SCANS_DIR    = Path('/home/ubuntu/trading-bot/logs/snapshots')

ALPACA_KEY    = os.environ.get('ALPACA_API_KEY', '')
ALPACA_SECRET = os.environ.get('ALPACA_SECRET_KEY', '')
ALPACA_BASE   = os.environ.get('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets/v2').rstrip('/')
if not ALPACA_BASE.endswith('/v2'):
    ALPACA_BASE += '/v2'

TELEGRAM_TOKEN   = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _type_label(spread_type: str) -> str:
    return {'bull_call': 'Bull Call', 'bear_put': 'Bear Put',
            'iron_condor': 'Iron Condor'}.get(
              spread_type, spread_type.replace('_', ' ').title())


def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print('⚠️  Telegram credentials missing — check .env')
        return
    try:
        r = requests.post(
            f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'Markdown'},
            timeout=10,
        )
        if r.status_code == 200:
            print('✅ Morning report sent via Telegram')
        else:
            print(f'⚠️  Telegram HTTP {r.status_code}: {r.text[:100]}')
    except Exception as e:
        print(f'⚠️  Telegram error: {e}')


def alpaca_get(path: str):
    """GET from Alpaca API. Returns parsed JSON or None on error."""
    try:
        r = requests.get(
            f'{ALPACA_BASE}{path}',
            headers={
                'APCA-API-KEY-ID':     ALPACA_KEY,
                'APCA-API-SECRET-KEY': ALPACA_SECRET,
            },
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
        print(f'⚠️  Alpaca GET {path}: HTTP {r.status_code}')
    except Exception as e:
        print(f'⚠️  Alpaca GET {path}: {e}')
    return None


def _load_pending_orders() -> dict:
    f = VAULT_DIR / 'OpenClaw/pending_orders.json'
    if not f.exists():
        return {'orders': [], 'last_scan_time': 'N/A'}
    try:
        return json.loads(f.read_text())
    except Exception:
        return {'orders': [], 'last_scan_time': 'N/A'}


def _load_last_scan() -> dict:
    scans = sorted(SCANS_DIR.glob('scan_*.json'))
    if not scans:
        return {}
    try:
        with open(scans[-1]) as f:
            return json.load(f)
    except Exception:
        return {}


# ─── Report builder ───────────────────────────────────────────────────────────

def build_report() -> str:
    now       = datetime.now().strftime('%Y-%m-%d %H:%M')
    today_str = datetime.now().strftime('%Y-%m-%d')
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    lines = [
        '🌅 *OpenClaw — Morning Report*',
        f'_{now} Bangkok (07:30 ICT)_',
        '',
    ]

    # ── Account summary ───────────────────────────────────────────────────────
    account = alpaca_get('/account')
    if account:
        equity    = float(account.get('equity', 0))
        cash      = float(account.get('cash', 0))
        bp        = float(account.get('buying_power', 0))
        pnl_today = float(account.get('equity', 0)) - float(account.get('last_equity', equity))
        pnl_icon  = '🟢' if pnl_today >= 0 else '🔴'
        lines += [
            '💰 *Account*',
            f'Equity: *${equity:,.2f}* | Cash: ${cash:,.2f}',
            f'Buying Power: ${bp:,.2f}',
            f'{pnl_icon} Session P&L: ${pnl_today:+,.2f}',
            '',
        ]
    else:
        lines += ['💰 *Account* — unavailable', '']

    # ── Open positions ────────────────────────────────────────────────────────
    positions = alpaca_get('/positions') or []
    if positions:
        total_upnl = sum(float(p.get('unrealized_pl', 0)) for p in positions)
        pnl_icon   = '🟢' if total_upnl >= 0 else '🔴'
        lines.append(f'📈 *Open Positions ({len(positions)}) — '
                     f'{pnl_icon} Total P&L: ${total_upnl:+,.2f}*')
        for p in positions:
            sym      = p.get('symbol', '?')
            qty      = p.get('qty', '?')
            upnl     = float(p.get('unrealized_pl', 0))
            upnl_pct = float(p.get('unrealized_plpc', 0)) * 100
            avg_px   = float(p.get('avg_entry_price', 0))
            cur_px   = float(p.get('current_price', 0))
            icon     = '🟢' if upnl >= 0 else '🔴'
            lines.append(
                f'{icon} `{sym}` × {qty} | Avg: ${avg_px:.2f} → ${cur_px:.2f} | '
                f'P&L: *${upnl:+.2f}* ({upnl_pct:+.1f}%)'
            )
        lines.append('')
    else:
        lines += ['📈 *Open Positions* — none', '']

    # ── Yesterday's auto-executions ───────────────────────────────────────────
    pdata = _load_pending_orders()
    last_scan_time = pdata.get('last_scan_time', 'N/A')
    all_orders     = pdata.get('orders', [])

    recent_executed = [
        o for o in all_orders
        if o.get('status') == 'executed'
        and o.get('executed_at', '')[:10] in (today_str, yesterday)
    ]
    recent_skipped = [
        o for o in all_orders
        if o.get('status') == 'skipped'
        and o.get('skipped_at', '')[:10] in (today_str, yesterday)
    ]

    if recent_executed:
        lines.append(f'✅ *Auto-Executed ({len(recent_executed)})*')
        for o in recent_executed:
            tl   = _type_label(o.get('spread_type', 'bull_call'))
            qty  = o.get('qty', 1)
            cost = round(float(o.get('spread_mid', 0)) * qty * 100, 0)
            lines += [
                f'`[{o["trade_id"]}]` {o["symbol"]} '
                f'${o["long_strike"]}/${o["short_strike"]} {tl} × {qty}',
                f'  Debit: ${o["spread_mid"]} | Risk: ${cost:.0f} | '
                f'Alpaca: `{o.get("alpaca_order_id","?")}`',
            ]
        lines.append('')

    if recent_skipped:
        lines.append(f'⏸ *Skipped Yesterday ({len(recent_skipped)})*')
        for o in recent_skipped:
            tl = _type_label(o.get('spread_type', 'bull_call'))
            lines.append(
                f'`[{o["trade_id"]}]` {o["symbol"]} ({tl}) — '
                f'{o.get("skip_reason","?")}'
            )
        lines.append('')

    if not recent_executed and not recent_skipped:
        lines += ['😴 *No trades yesterday* — capital preserved', '']

    # ── Upcoming expirations (from executed orders) ───────────────────────────
    active_trades = [
        o for o in all_orders
        if o.get('status') == 'executed' and o.get('expiry', '') >= today_str
    ]
    if active_trades:
        active_trades.sort(key=lambda o: o.get('expiry', ''))
        lines.append('📅 *Active Trades & Upcoming Expirations*')
        for o in active_trades:
            exp    = o.get('expiry', '?')
            try:
                dte_rem = (datetime.strptime(exp, '%Y-%m-%d') - datetime.now()).days
            except Exception:
                dte_rem = '?'
            tl  = _type_label(o.get('spread_type', 'bull_call'))
            qty = o.get('qty', 1)
            warn = ' ⚠️ _Expiring soon!_' if isinstance(dte_rem, int) and dte_rem <= 7 else ''
            lines.append(
                f'`{o["symbol"]}` ${o["long_strike"]}/${o["short_strike"]} {tl} × {qty}'
                f' | {exp} ({dte_rem}d){warn}'
            )
        lines.append('')

    # ── Macro from last scan ──────────────────────────────────────────────────
    scan  = _load_last_scan()
    macro = scan.get('macro', {})
    if macro:
        regime = scan.get('regime', 'unknown')
        vix    = macro.get('VIX', {}).get('price', 'N/A')
        spy    = macro.get('SPY', {}).get('change_pct', 'N/A')
        xle    = macro.get('XLE', {}).get('change_pct', 'N/A')
        lines += [
            f'🌐 *Last Scan Context* _(run: {last_scan_time})_',
            f'VIX: {vix} | SPY: {spy}% | XLE: {xle}% | Regime: `{regime}`',
            '',
        ]

    lines.append('_Next scan: tonight 9:05 PM ICT_')

    return '\n'.join(lines)


# ─── Run ──────────────────────────────────────────────────────────────────────

def run():
    print(f"\n{'='*55}")
    print(f"MORNING REPORT — {datetime.now().strftime('%Y-%m-%d %H:%M')} ICT")
    print(f"{'='*55}\n")

    if not ALPACA_KEY or not ALPACA_SECRET:
        print('❌ Alpaca credentials missing — check /home/ubuntu/trading-bot/.env')
        return

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print('❌ Telegram credentials missing — check /home/ubuntu/trading-bot/.env')
        return

    report = build_report()
    print(report)
    print()
    send_telegram(report)
    print(f"\n{'='*55}\n")


if __name__ == '__main__':
    run()
