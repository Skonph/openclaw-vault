#!/usr/bin/env python3
"""
OpenClaw Position Monitor v3.0 — Complete Exit Strategy

Auto-exit rules (in priority order):
  1. Profit target  : unrealised ≥ 50% of max profit          → auto-close
  2. Stop loss      : position value ≤ 20% of debit (80% loss) → auto-close
  3. Expiry gate    : DTE ≤ 7                                  → auto-close (gamma risk)
  4. Dead trade     : DTE ≤ 21 AND loss > 25% of debit         → auto-close (no recovery time)
  5. Trend reversal : ETF/regime flipped against spread dir     → Telegram alert + flag
  6. Healthy        : none of the above                         → hold

Called by vault_updater.py at ~21:20 Bangkok each weeknight.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

# Try multiple possible .env paths for development flexibility
for env_path in ['/home/ubuntu/openclaw/.env', str(Path(__file__).parent / '.env'), str(Path(__file__).parent / '.env.local')]:
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

ALPACA_KEY    = os.environ.get('ALPACA_API_KEY', '')
ALPACA_SECRET = os.environ.get('ALPACA_SECRET_KEY', '')
ALPACA_BASE   = os.environ.get('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets/v2').rstrip('/')
if not ALPACA_BASE.endswith('/v2'):
    ALPACA_BASE += '/v2'

if os.path.exists('/home/ubuntu/openclaw-vault'):
    VAULT_DIR    = Path('/home/ubuntu/openclaw-vault')
else:
    VAULT_DIR    = Path(__file__).parent.parent

if os.path.exists('/home/ubuntu/openclaw'):
    SCANS_DIR    = Path('/home/ubuntu/openclaw/logs/snapshots')
else:
    SCANS_DIR    = Path(__file__).parent / 'logs' / 'snapshots'

MONITOR_LOG  = VAULT_DIR / 'OpenClaw/11_Position_Monitor.md'
PENDING_FILE = VAULT_DIR / 'OpenClaw/pending_orders.json'

# ─── Exit thresholds ──────────────────────────────────────────────────────────
PROFIT_TARGET_PCT  = 0.50   # auto-close at 50% of max profit
STOP_LOSS_PCT      = 0.20   # auto-close when value ≤ 20% of debit (80% loss)
DEAD_TRADE_DTE     = 21     # mid-life review DTE
DEAD_TRADE_LOSS    = 0.25   # auto-close if loss > 25% of debit at DEAD_TRADE_DTE
EXPIRY_GATE_DTE    = 7      # auto-close at ≤7 DTE regardless (gamma risk)


# ─── Alpaca helpers ───────────────────────────────────────────────────────────

def _headers() -> dict:
    return {
        'APCA-API-KEY-ID':     ALPACA_KEY,
        'APCA-API-SECRET-KEY': ALPACA_SECRET,
        'Content-Type':        'application/json',
    }


def get_positions() -> list:
    try:
        r = requests.get(f'{ALPACA_BASE}/positions', headers=_headers(), timeout=10)
        return r.json() if r.status_code == 200 else []
    except Exception as e:
        print(f'⚠️  positions error: {e}')
        return []


def get_account() -> dict:
    try:
        r = requests.get(f'{ALPACA_BASE}/account', headers=_headers(), timeout=10)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


# ─── Telegram ─────────────────────────────────────────────────────────────────

def send_telegram(message: str):
    token   = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        return
    try:
        requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat_id, 'text': message, 'parse_mode': 'Markdown'},
            timeout=10,
        )
    except Exception as e:
        print(f'⚠️  Telegram error: {e}')


# ─── Market regime (for trend reversal check) ─────────────────────────────────

def _get_market_regime() -> dict:
    """
    Read regime and ETF direction from latest scan snapshot.
    Returns dict with: regime, spy_above_ema, market_ok
    """
    try:
        scans = sorted(SCANS_DIR.glob('scan_*.json'))
        if not scans:
            return {}
        with open(scans[-1]) as f:
            snap = json.load(f)
        macro  = snap.get('macro', {})
        regime = snap.get('regime', 'unknown')
        spy    = macro.get('SPY', {})
        # SPY above EMA20 if change_pct positive and market_ok
        market_ok     = snap.get('market_ok', False)
        spy_change    = float(spy.get('change_pct', 0) or 0)
        spy_above_ema = market_ok and spy_change >= 0
        return {
            'regime':       regime,
            'spy_above_ema': spy_above_ema,
            'market_ok':    market_ok,
            'snap_time':    snap.get('scan_time', snap.get('timestamp', 'unknown')),
        }
    except Exception as e:
        print(f'⚠️  regime read error: {e}')
        return {}


# ─── Spread direction from legs ───────────────────────────────────────────────

def _get_spread_direction(legs: list) -> str:
    """Determine spread type from option leg types."""
    types = set(l['option_type'] for l in legs)
    if types == {'call'}:
        return 'bull_call'
    if types == {'put'}:
        return 'bear_put'
    return 'unknown'


def _trend_reversal(spread_type: str, regime: dict) -> bool:
    """Return True if current regime contradicts the spread direction."""
    r = regime.get('regime', 'unknown')
    spy_above = regime.get('spy_above_ema', None)
    if spread_type == 'bull_call':
        # Entered bullish — reversal if market now bearish or cash
        return r in ('bear', 'cash') or spy_above is False
    if spread_type == 'bear_put':
        # Entered bearish — reversal if market now bullish
        return r == 'bull' or spy_above is True
    return False


# ─── Pending orders (original trade data) ─────────────────────────────────────

def _load_pending_orders() -> list:
    try:
        if PENDING_FILE.exists():
            return json.loads(PENDING_FILE.read_text()).get('orders', [])
    except Exception:
        pass
    return []


def _get_trade_data(underlying: str, expiry: str) -> dict | None:
    for o in _load_pending_orders():
        if (o.get('symbol') == underlying
                and o.get('expiry') == expiry
                and o.get('status') == 'executed'):
            return o
    return None


def _add_cooling_off(underlying: str, days: int = 5):
    """Add ticker to cooling-off list for N days after a stop loss."""
    try:
        data          = json.loads(PENDING_FILE.read_text()) if PENDING_FILE.exists() else {'orders': []}
        cooling       = data.get('cooling_off', {})
        expiry_date   = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')
        cooling[underlying] = expiry_date
        data['cooling_off'] = cooling
        PENDING_FILE.write_text(json.dumps(data, indent=2))
        print(f'  ⏸ {underlying} cooling-off until {expiry_date}')
    except Exception as e:
        print(f'⚠️  cooling-off write: {e}')


def _record_pnl(underlying: str, expiry: str, spread_type: str,
                pnl: float, reason: str, trade_id: str = None):
    """Append realized P&L to pnl_history in pending_orders.json."""
    try:
        data    = json.loads(PENDING_FILE.read_text()) if PENDING_FILE.exists() else {'orders': []}
        history = data.get('pnl_history', [])
        history.append({
            'timestamp':   datetime.now().strftime('%Y-%m-%d %H:%M'),
            'symbol':      underlying,
            'expiry':      expiry,
            'spread_type': spread_type,
            'pnl':         round(pnl, 2),
            'reason':      reason,
            'trade_id':    trade_id,
        })
        data['pnl_history'] = history
        PENDING_FILE.write_text(json.dumps(data, indent=2))
        print(f'  📊 P&L recorded: {underlying} ${pnl:+.2f} ({reason})')
    except Exception as e:
        print(f'⚠️  P&L record: {e}')


# ─── Position parsing ─────────────────────────────────────────────────────────

def parse_option_position(pos: dict) -> dict | None:
    if pos.get('asset_class') != 'us_option':
        return None
    symbol = pos.get('symbol', '')
    try:
        i = 0
        while i < len(symbol) and not symbol[i].isdigit():
            i += 1
        underlying  = symbol[:i]
        date_part   = symbol[i:i+6]
        cp          = symbol[i+6]
        strike_raw  = symbol[i+7:i+15]
        expiry_dt   = datetime.strptime(date_part, '%y%m%d')
        expiry_str  = expiry_dt.strftime('%Y-%m-%d')
        dte         = (expiry_dt - datetime.now()).days
        strike      = int(strike_raw) / 1000.0
        option_type = 'call' if cp == 'C' else 'put'
    except Exception:
        underlying  = symbol
        expiry_str  = 'unknown'
        dte         = -1
        strike      = 0.0
        option_type = 'unknown'

    return {
        'occ_symbol':    symbol,
        'underlying':    underlying,
        'expiry':        expiry_str,
        'dte':           dte,
        'strike':        strike,
        'option_type':   option_type,
        'qty':           int(pos.get('qty', 0)),
        'side':          pos.get('side', ''),
        'avg_entry':     float(pos.get('avg_entry_price', 0) or 0),
        'current_price': float(pos.get('current_price', 0) or 0),
        'market_value':  float(pos.get('market_value', 0) or 0),
        'unrealised_pl': float(pos.get('unrealized_pl', 0) or 0),
        'cost_basis':    float(pos.get('cost_basis', 0) or 0),
    }


def group_spread_legs(positions: list) -> dict:
    groups: dict[str, list] = {}
    for pos in positions:
        parsed = parse_option_position(pos)
        if parsed is None:
            continue
        key = f'{parsed["underlying"]}_{parsed["expiry"]}'
        groups.setdefault(key, []).append(parsed)
    return groups


# ─── Auto-close execution ─────────────────────────────────────────────────────

def auto_close_spread(legs: list, reason: str) -> bool:
    """Submit mleg close order. Long → sell_to_close. Short → buy_to_close."""
    close_legs = []
    net_credit = 0.0
    for leg in legs:
        if leg['side'] == 'long':
            close_legs.append({
                'symbol':          leg['occ_symbol'],
                'ratio_qty':       1,
                'side':            'sell',
                'position_effect': 'close',
            })
            net_credit += leg['current_price']
        elif leg['side'] == 'short':
            close_legs.append({
                'symbol':          leg['occ_symbol'],
                'ratio_qty':       1,
                'side':            'buy',
                'position_effect': 'close',
            })
            net_credit -= leg['current_price']

    if not close_legs:
        print('  ⚠️  No legs to close')
        return False

    qty      = legs[0]['qty']
    limit_px = round(max(net_credit * 0.95, 0.01), 2)

    payload = {
        'order_class':   'mleg',
        'type':          'limit',
        'time_in_force': 'day',
        'limit_price':   str(limit_px),
        'qty':           str(qty),
        'legs':          close_legs,
    }

    try:
        r = requests.post(
            f'{ALPACA_BASE}/orders',
            headers=_headers(),
            json=payload,
            timeout=15,
        )
        if r.status_code in (200, 201):
            alpaca_id = r.json().get('id', 'unknown')
            print(f'  🚀 Close submitted — Alpaca: {alpaca_id}')
            return True
        print(f'  ❌ Alpaca {r.status_code}: {r.text[:200]}')
        return False
    except Exception as e:
        print(f'  ❌ Close error: {e}')
        return False


# ─── Core analysis + exit logic ───────────────────────────────────────────────

def analyse_and_exit(key: str, legs: list, regime: dict) -> dict:
    """
    Apply all exit rules in priority order.
    Returns status dict with outcome.
    """
    underlying   = legs[0]['underlying']
    expiry       = legs[0]['expiry']
    dte          = legs[0]['dte']
    qty          = legs[0]['qty']
    spread_type  = _get_spread_direction(legs)

    total_market_value = sum(l['market_value'] for l in legs)
    total_cost_basis   = abs(sum(l['cost_basis'] for l in legs))
    total_unrealised   = sum(l['unrealised_pl'] for l in legs)
    debit_paid         = total_cost_basis

    if debit_paid == 0:
        return {'key': key, 'status': 'skip', 'reason': 'zero cost basis'}

    # Max profit from original trade data
    trade = _get_trade_data(underlying, expiry)
    if trade:
        spread_width = abs(float(trade['short_strike']) - float(trade['long_strike']))
        spread_mid   = float(trade['spread_mid'])
        max_profit   = (spread_width - spread_mid) * 100 * qty
    else:
        strikes    = sorted(set(l['strike'] for l in legs))
        spread_width = (strikes[-1] - strikes[0]) if len(strikes) >= 2 else 2.0
        max_profit   = (spread_width * 100 * qty) - debit_paid

    max_profit  = max(max_profit, 1.0)
    profit_pct  = total_unrealised / max_profit
    value_pct   = total_market_value / debit_paid if debit_paid else 1.0
    loss_vs_debit = abs(total_unrealised) / debit_paid if total_unrealised < 0 else 0

    print(f'\n  {underlying} {expiry} ({dte} DTE) [{spread_type}]')
    print(f'  Debit: ${debit_paid:.2f} | Value: ${total_market_value:.2f} | '
          f'P&L: ${total_unrealised:+.2f} | Profit%: {profit_pct*100:.0f}%')

    trade    = _get_trade_data(underlying, expiry)
    trade_id = trade.get('trade_id') if trade else None

    # ── Rule 1: Profit target ─────────────────────────────────────────────────
    if profit_pct >= PROFIT_TARGET_PCT:
        reason  = f'50% profit target'
        success = auto_close_spread(legs, reason)
        if success:
            send_telegram(
                f'💰 *{underlying} — PROFIT TARGET CLOSE*\n'
                f'P&L: *${total_unrealised:+.2f}* ({profit_pct*100:.0f}% of ${max_profit:.0f} max)\n'
                f'Expiry: {expiry} ({dte} DTE)\n'
                f'_Rule 1: 50% profit target hit_'
            )
            _record_pnl(underlying, expiry, spread_type, total_unrealised, reason, trade_id)
        print(f'  💰 Rule 1: Profit target → close')
        return {'key': key, 'status': 'profit_close', 'pnl': total_unrealised, 'success': success}

    # ── Rule 2: Stop loss ─────────────────────────────────────────────────────
    if value_pct <= STOP_LOSS_PCT:
        reason  = f'Stop loss 80%'
        success = auto_close_spread(legs, reason)
        if success:
            send_telegram(
                f'🔴 *{underlying} — STOP LOSS CLOSE*\n'
                f'Value: ${total_market_value:.2f} ({value_pct*100:.0f}% of ${debit_paid:.2f} debit)\n'
                f'Loss: *${total_unrealised:+.2f}*\n'
                f'Expiry: {expiry} ({dte} DTE)\n'
                f'_Rule 2: 80% loss threshold hit_'
            )
            _record_pnl(underlying, expiry, spread_type, total_unrealised, reason, trade_id)
            _add_cooling_off(underlying, days=5)
        print(f'  🔴 Rule 2: Stop loss → close + cooling-off')
        return {'key': key, 'status': 'stop_close', 'pnl': total_unrealised, 'success': success}

    # ── Rule 3: Expiry gate (DTE ≤ 7) ────────────────────────────────────────
    if 0 <= dte <= EXPIRY_GATE_DTE:
        reason  = f'Expiry gate DTE {dte}'
        success = auto_close_spread(legs, reason)
        if success:
            icon = '💰' if total_unrealised >= 0 else '🔴'
            send_telegram(
                f'{icon} *{underlying} — EXPIRY GATE CLOSE*\n'
                f'{dte} days to expiry — closing to avoid gamma risk\n'
                f'P&L: *${total_unrealised:+.2f}*\n'
                f'_Rule 3: Auto-close at DTE ≤ 7_'
            )
            _record_pnl(underlying, expiry, spread_type, total_unrealised, reason, trade_id)
        print(f'  ⏰ Rule 3: Expiry gate DTE {dte} → close')
        return {'key': key, 'status': 'expiry_close', 'pnl': total_unrealised, 'success': success}

    # ── Rule 4: Dead trade (DTE ≤ 21, loss > 25%) ────────────────────────────
    if dte <= DEAD_TRADE_DTE and loss_vs_debit > DEAD_TRADE_LOSS:
        reason  = f'Dead trade DTE {dte} loss {loss_vs_debit*100:.0f}%'
        success = auto_close_spread(legs, reason)
        if success:
            send_telegram(
                f'🗑 *{underlying} — DEAD TRADE CLOSE*\n'
                f'DTE: {dte} days | Loss: *${total_unrealised:+.2f}* '
                f'({loss_vs_debit*100:.0f}% of ${debit_paid:.2f} debit)\n'
                f'_Rule 4: No recovery time at DTE ≤ 21 with >25% loss_'
            )
            _record_pnl(underlying, expiry, spread_type, total_unrealised, reason, trade_id)
        print(f'  🗑  Rule 4: Dead trade DTE {dte}, loss {loss_vs_debit*100:.0f}% → close')
        return {'key': key, 'status': 'dead_close', 'pnl': total_unrealised, 'success': success}

    # ── Rule 5: Trend reversal ────────────────────────────────────────────────
    if regime and _trend_reversal(spread_type, regime):
        r_name = regime.get('regime', 'unknown')
        send_telegram(
            f'⚠️ *{underlying} — TREND REVERSAL*\n'
            f'Spread: {spread_type.replace("_"," ").title()} | '
            f'Market regime now: `{r_name}`\n'
            f'P&L: ${total_unrealised:+.2f} | DTE: {dte}\n'
            f'_Rule 5: Original thesis may be broken — review position_'
        )
        print(f'  ⚠️  Rule 5: Trend reversal — regime {r_name} vs {spread_type}')
        return {'key': key, 'status': 'reversal_alert', 'pnl': total_unrealised,
                'regime': r_name, 'spread_type': spread_type}

    # ── Rule 6: Healthy — hold ────────────────────────────────────────────────
    print(f'  ✅ Healthy — holding')
    return {'key': key, 'status': 'ok', 'pnl': total_unrealised}


# ─── Vault output ─────────────────────────────────────────────────────────────

def write_monitor_report(groups: dict, account: dict, results: list):
    now          = datetime.now().strftime('%Y-%m-%d %H:%M')
    equity       = float(account.get('equity', 0) or 0)
    buying_power = float(account.get('buying_power', 0) or 0)

    report  = f'# Position Monitor\n**Last run:** {now} | Mode: 🤖 Auto-exit v3\n\n---\n\n'
    report += f'## Account\n- Equity: ${equity:,.2f}\n- Buying Power: ${buying_power:,.2f}\n\n'

    if not groups:
        report += '## Positions\nNo open options positions.\n\n'
    else:
        report += f'## Open Positions ({len(groups)} spread(s))\n\n'
        for key, legs in groups.items():
            dte          = legs[0]['dte']
            spread_type  = _get_spread_direction(legs)
            total_mv     = sum(l['market_value'] for l in legs)
            total_pl     = sum(l['unrealised_pl'] for l in legs)
            icon         = '🟢' if total_pl >= 0 else '🔴'
            report += f'### {legs[0]["underlying"]} — {legs[0]["expiry"]} ({dte} DTE) [{spread_type}]\n'
            report += f'- Market Value: ${total_mv:.2f} | {icon} Unrealised: ${total_pl:+.2f}\n'
            for leg in legs:
                report += (f'  - {leg["side"].upper()} {leg["option_type"].upper()} '
                           f'${leg["strike"]} × {leg["qty"]} '
                           f'@ ${leg["avg_entry"]:.2f} → ${leg["current_price"]:.2f}\n')
            report += '\n'

    status_map = {
        'profit_close':  '💰 Profit target close',
        'stop_close':    '🔴 Stop loss close',
        'expiry_close':  '⏰ Expiry gate close',
        'dead_close':    '🗑  Dead trade close',
        'reversal_alert':'⚠️  Trend reversal alert',
        'ok':            '✅ Holding',
    }

    if results:
        report += '## Exit Actions\n\n'
        for r in results:
            status = r.get('status', 'ok')
            label  = status_map.get(status, status)
            pnl    = r.get('pnl', 0)
            report += f'**{r["key"]}** — {label} | P&L: ${pnl:+.2f}'
            if status in ('profit_close', 'stop_close', 'expiry_close', 'dead_close'):
                report += f' | Executed: {"✅" if r.get("success") else "❌"}'
            if status == 'reversal_alert':
                report += f' | Regime: {r.get("regime","?")} vs {r.get("spread_type","?")}'
            report += '\n'

    report += '\n## Exit Rules\n'
    report += f'1. Profit target  : ≥{PROFIT_TARGET_PCT*100:.0f}% of max profit → auto-close\n'
    report += f'2. Stop loss      : value ≤{STOP_LOSS_PCT*100:.0f}% of debit → auto-close\n'
    report += f'3. Expiry gate    : DTE ≤{EXPIRY_GATE_DTE} → auto-close (gamma risk)\n'
    report += f'4. Dead trade     : DTE ≤{DEAD_TRADE_DTE} + loss >{DEAD_TRADE_LOSS*100:.0f}% of debit → auto-close\n'
    report += f'5. Trend reversal : regime contradicts spread direction → Telegram alert\n'
    report += '\n---\n*Auto-generated by position_monitor.py v3.0*\n'

    MONITOR_LOG.parent.mkdir(parents=True, exist_ok=True)
    MONITOR_LOG.write_text(report)
    print(f'\n✅ 11_Position_Monitor.md updated')


# ─── Main ─────────────────────────────────────────────────────────────────────

def run(stdout_only: bool = False):
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n{"="*55}')
    print(f'POSITION MONITOR v3.0 — {now}')
    print(f'{"="*55}')

    if not ALPACA_KEY or not ALPACA_SECRET:
        print('⚠️  Alpaca credentials missing')
        return

    account   = get_account()
    positions = get_positions()
    regime    = _get_market_regime()

    if regime:
        print(f'\nRegime: {regime.get("regime","unknown")} | '
              f'SPY above EMA: {regime.get("spy_above_ema","?")} | '
              f'Snap: {regime.get("snap_time","?")}')

    if not positions:
        print('\nNo open positions.')
        if not stdout_only:
            write_monitor_report({}, account, [])
        return

    groups = group_spread_legs(positions)
    print(f'\n{len(positions)} leg(s) across {len(groups)} spread(s)\n')
    print('--- Applying exit rules ---')

    results = []
    for key, legs in groups.items():
        result = analyse_and_exit(key, legs, regime)
        results.append(result)

    if not stdout_only:
        write_monitor_report(groups, account, results)

    closed  = [r for r in results if r['status'].endswith('_close')]
    alerts  = [r for r in results if r['status'] == 'reversal_alert']
    holding = [r for r in results if r['status'] == 'ok']

    print(f'\n  Auto-closed : {len(closed)}')
    print(f'  Alerts      : {len(alerts)}')
    print(f'  Holding     : {len(holding)}')
    print(f'\n{"="*55}\n')


if __name__ == '__main__':
    run('--stdout' in sys.argv)
