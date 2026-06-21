#!/usr/bin/env python3
"""
OpenClaw Vault Updater v4.0 — Autonomous Mode

Reads latest scan JSON → auto-executes qualifying orders → updates vault markdown → git push.
Sends Telegram per action (execute/skip/error) + nightly summary.

v4 changes:
  - Auto-executes orders that pass all gates (events clear + conviction ≥75 + not IC)
  - Fixed $200 risk per trade (contracts = floor(200 / spread_mid×100), min 1)
  - Skips Iron Condor (4-leg executor pending) with Telegram notice
  - Skips events-uncertain orders with Telegram notice
  - Morning report handled by morning_report.py (cron 00:30 UTC = 07:30 ICT)
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

# Allow importing sibling modules (position_monitor)
sys.path.insert(0, str(Path(__file__).parent))

# Try multiple possible .env paths for development flexibility
for env_path in ['/home/ubuntu/openclaw/.env', str(Path(__file__).parent / '.env'), str(Path(__file__).parent / '.env.local')]:
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

if os.path.exists('/home/ubuntu/openclaw-vault'):
    VAULT_DIR       = Path('/home/ubuntu/openclaw-vault')
else:
    VAULT_DIR       = Path(__file__).parent.parent

if os.path.exists('/home/ubuntu/openclaw'):
    SCANS_DIR       = Path('/home/ubuntu/openclaw/logs/snapshots')
    CANDIDATES_FILE = Path('/home/ubuntu/openclaw/candidates.txt')
else:
    SCANS_DIR       = Path(__file__).parent / 'logs' / 'snapshots'
    CANDIDATES_FILE = Path(__file__).parent / 'candidates.txt'

ALPACA_KEY    = os.environ.get('ALPACA_API_KEY', '')
ALPACA_SECRET = os.environ.get('ALPACA_SECRET_KEY', '')
ALPACA_BASE   = os.environ.get('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets/v2').rstrip('/')
if not ALPACA_BASE.endswith('/v2'):
    ALPACA_BASE += '/v2'

# ─── Multi-position portfolio policy (2026-06-19) ──────────────────────────────
# Replaces the old binary "max 1 spread" gate. Concurrency is governed by a
# PORTFOLIO RISK BUDGET (the real cap) + a count cap + a per-direction
# concentration cap — so the system accumulates a track record faster without
# stacking correlated leverage on the small paper account. All env-overridable.
MAX_CONCURRENT_POSITIONS = int(os.environ.get('OPENCLAW_MAX_POSITIONS', '2'))
PORTFOLIO_RISK_PCT       = float(os.environ.get('OPENCLAW_PORTFOLIO_RISK_PCT', '0.15'))
MAX_PER_DIRECTION        = int(os.environ.get('OPENCLAW_MAX_PER_DIRECTION', '1'))
_DIRECTION = {'bull_call': 'bull', 'bear_put': 'bear', 'iron_condor': 'neutral'}
OPEN_RISK_LEDGER = (Path('/home/ubuntu/openclaw') if os.path.exists('/home/ubuntu/openclaw')
                    else Path(__file__).parent) / 'open_risk_ledger.json'


# ─── Telegram ─────────────────────────────────────────────────────────────────

def send_telegram(message: str):
    """Send a message via the OpenClaw Telegram bot."""
    token   = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        print('⚠️  Telegram: BOT_TOKEN or CHAT_ID missing — skipping')
        return
    try:
        r = requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat_id, 'text': message, 'parse_mode': 'Markdown'},
            timeout=10,
        )
        if r.status_code == 200:
            print('✅ Telegram sent')
        else:
            print(f'⚠️  Telegram HTTP {r.status_code}: {r.text[:100]}')
    except Exception as e:
        print(f'⚠️  Telegram error: {e}')


def build_telegram_summary(scan: dict, exec_results: dict, today: str) -> str:
    """Build nightly summary after auto-execution completes."""
    executed = exec_results.get('executed', [])
    skipped  = exec_results.get('skipped', [])
    errors   = exec_results.get('errors', [])
    macro    = scan.get('macro', {})
    regime   = scan.get('regime', 'unknown')
    holds    = scan.get('holds', [])
    err_scan = scan.get('error', '')

    vix = macro.get('VIX', {}).get('price', 'N/A')
    spy = macro.get('SPY', {}).get('change_pct', 'N/A')

    if err_scan:
        return '\n'.join([
            '❌ *OpenClaw v3 — Scan Error*',
            f'_{today} Bangkok_', '',
            f'`{err_scan}`',
        ])

    lines = [
        '🤖 *OpenClaw v4 — Nightly Summary*',
        f'_{today} Bangkok_', '',
        f'VIX: {vix} | SPY: {spy}% | Regime: `{regime}`',
        '',
    ]

    if executed:
        lines.append(f'✅ *Auto-executed {len(executed)} trade(s)*')
        for o in executed:
            tl = {'bull_call': 'Bull Call', 'bear_put': 'Bear Put',
                  'iron_condor': 'Iron Condor'}.get(
                    o.get('spread_type', ''), o.get('spread_type', '').title())
            qty  = o.get('qty', 1)
            cost = round(float(o['spread_mid']) * qty * 100, 0)
            lines.append(
                f'`[{o["trade_id"]}]` {o["symbol"]} '
                f'${o["long_strike"]}/${o["short_strike"]} {tl} '
                f'× {qty} (${cost:.0f} risk)'
            )
        lines.append('')

    if skipped:
        lines.append(f'⏸ *Skipped {len(skipped)}*')
        for o in skipped:
            lines.append(f'`[{o["trade_id"]}]` {o["symbol"]} — {o.get("skip_reason","?")}')
        lines.append('')

    if errors:
        lines.append(f'❌ *Errors {len(errors)}*')
        for e in errors:
            o = e.get('order', {})
            lines.append(f'`[{o.get("trade_id","?")}]` {o.get("symbol","?")} — {e.get("error","")[:80]}')
        lines.append('')

    if not executed and not skipped and not errors:
        lines.append('😴 *No qualifying spreads tonight*')
        lines.append('')
        if holds:
            lines.append(f'⏸ Holds: {len(holds)}')
        lines.append('_Capital preserved. Standing by._')

    return '\n'.join(lines)


# ─── Alpaca helpers ───────────────────────────────────────────────────────────

def _alpaca_headers() -> dict:
    return {
        'APCA-API-KEY-ID':     ALPACA_KEY,
        'APCA-API-SECRET-KEY': ALPACA_SECRET,
        'Content-Type':        'application/json',
    }


def build_occ(symbol: str, expiry: str, opt_type: str, strike: float) -> str:
    """Build OCC option symbol e.g. NCLH260620C00017000"""
    exp_digits = expiry.replace('-', '')[2:]          # YYMMDD
    ot         = 'C' if opt_type.lower().startswith('c') else 'P'
    strike_str = f'{int(round(strike * 1000)):08d}'
    return f'{symbol}{exp_digits}{ot}{strike_str}'


def _calc_qty(spread_mid: float, conviction_score: float = 75) -> int:
    """
    Dynamic position sizing: 5% of account equity, scaled by a
    conviction-weighted multiplier (Improvement #5, 2026-06-13).

    Base risk_amount = clamp(equity * 5%, $200, $500)  [unchanged from before]

    Conviction tiers (conviction_score is gated >=75 upstream, so this only
    ever sees 75-100):
      Tier 1 (75-84):  multiplier 1.0x -> risk_amount in [$200,  $500]
      Tier 2 (85-94):  multiplier 1.5x -> risk_amount in [$300,  $750]
      Tier 3 (95-100): multiplier 2.0x -> risk_amount in [$400, $1000]

    The ceiling rises with conviction (top tier can risk up to $1000/trade
    instead of the previous flat $500 cap), so higher-conviction setups can
    take a meaningfully larger position without changing the underlying
    equity-based formula.

    Falls back to $200 (tier-1 floor) if account fetch fails.
    """
    if conviction_score >= 95:
        multiplier = 2.0
    elif conviction_score >= 85:
        multiplier = 1.5
    else:
        multiplier = 1.0

    try:
        r = requests.get(f'{ALPACA_BASE}/account', headers=_alpaca_headers(), timeout=5)
        if r.status_code == 200:
            equity    = float(r.json().get('equity', 0))
            base_risk = max(200.0, min(equity * 0.05, 500.0))
        else:
            base_risk = 200.0
    except Exception:
        base_risk = 200.0

    risk_amount = base_risk * multiplier
    cost_per_contract = float(spread_mid) * 100
    return max(1, int(risk_amount / cost_per_contract))


def _check_paper_mode() -> bool:
    """
    Safety guard: block live trading unless LIVE_TRADING=true is explicitly set.
    Returns True if safe to proceed, False if blocked.
    """
    is_paper = 'paper-api' in ALPACA_BASE
    if is_paper:
        return True
    live_ok = os.environ.get('LIVE_TRADING', '').lower() == 'true'
    if not live_ok:
        msg = ('🚫 *LIVE TRADING BLOCKED*\n'
               'ALPACA\\_BASE\\_URL points to live API but `LIVE_TRADING=true` '
               'is not set in openclaw/.env.\n'
               'Set it explicitly to enable real money trading.')
        print(f'\n🚫 Live trading blocked — LIVE_TRADING not set')
        send_telegram(msg)
        return False
    send_telegram('⚠️ *LIVE TRADING MODE ACTIVE* — real money at risk')
    return True


def _has_open_options() -> bool:
    """
    Returns True if Alpaca account already has open options positions.
    Enforces max 1 active spread at a time.
    """
    try:
        r = requests.get(f'{ALPACA_BASE}/positions', headers=_alpaca_headers(), timeout=10)
        if r.status_code == 200:
            opts = [p for p in r.json() if p.get('asset_class') == 'us_option']
            if opts:
                syms = list({p.get('symbol','?')[:6] for p in opts})
                print(f'  ⚠️  Open options positions: {syms} — skipping new entries')
                send_telegram(
                    f'⏸ *Entry skipped — existing position open*\n'
                    f'Tickers: {", ".join(syms)}\n'
                    f'_Max 1 spread at a time. Will retry when position closes._'
                )
                return True
    except Exception as e:
        print(f'  ⚠️  Position check error: {e}')
    return False


# ─── Multi-position portfolio gate ──────────────────────────────────────────────

def _occ_underlying(occ_symbol: str) -> str:
    """Underlying ticker from an OCC option symbol (e.g. 'TOST260717P00012000' -> 'TOST')."""
    m = re.match(r'^([A-Za-z\.]+)', occ_symbol or '')
    return m.group(1) if m else (occ_symbol or '')


def _open_option_underlyings() -> set:
    """Live set of underlying tickers with open option positions on Alpaca.
    Empty set on error (same conservative posture as the old gate)."""
    try:
        r = requests.get(f'{ALPACA_BASE}/positions', headers=_alpaca_headers(), timeout=10)
        if r.status_code == 200:
            return {_occ_underlying(p.get('symbol', ''))
                    for p in r.json() if p.get('asset_class') == 'us_option'}
    except Exception as e:
        print(f'  ⚠️  Position check error: {e}')
    return set()


def _get_equity(default: float = 2000.0) -> float:
    try:
        r = requests.get(f'{ALPACA_BASE}/account', headers=_alpaca_headers(), timeout=5)
        if r.status_code == 200:
            return float(r.json().get('equity', default))
    except Exception:
        pass
    return default


def _load_risk_ledger() -> dict:
    """Crash-safe read of {underlying: {direction, max_loss, trade_id, opened_at}}."""
    try:
        if OPEN_RISK_LEDGER.exists():
            data = json.loads(OPEN_RISK_LEDGER.read_text())
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_risk_ledger(ledger: dict) -> None:
    try:
        OPEN_RISK_LEDGER.write_text(json.dumps(ledger, indent=2))
    except OSError as e:
        print(f'  ⚠️  Could not save risk ledger: {e}')


def _reconcile_ledger(ledger: dict, open_underlyings: set) -> dict:
    """Drop ledger entries whose underlying is no longer open on Alpaca (closed
    out-of-band) so the budget reflects reality. Pure given its inputs."""
    return {u: e for u, e in ledger.items() if u in open_underlyings}


def _order_max_loss(spread_type: str, order: dict, qty: int, limit_px: float) -> float:
    """Defined max loss (USD). Debit spread = net debit paid; Iron Condor =
    (wing width − net credit) × 100 × qty."""
    if spread_type == 'iron_condor':
        width  = abs(float(order['put_short_strike']) - float(order['put_long_strike']))
        credit = abs(float(limit_px))
        return max(0.0, width - credit) * 100 * qty
    return float(order['spread_mid']) * 100 * qty


def _portfolio_admits(order_max_loss: float, direction: str, equity: float,
                      cur_count: int, cur_risk: float, dir_counts: dict,
                      max_positions: int = None, risk_pct: float = None,
                      max_per_dir: int = None) -> tuple:
    """PURE gate decision -> (ok, reason). The caller maintains the running
    tallies so several orders in one run respect the CUMULATIVE budget."""
    max_positions = MAX_CONCURRENT_POSITIONS if max_positions is None else max_positions
    risk_pct      = PORTFOLIO_RISK_PCT       if risk_pct      is None else risk_pct
    max_per_dir   = MAX_PER_DIRECTION        if max_per_dir   is None else max_per_dir
    budget = risk_pct * equity
    if cur_count >= max_positions:
        return False, f'position-count cap ({cur_count}/{max_positions})'
    if cur_risk + order_max_loss > budget:
        return False, (f'portfolio-risk cap (${cur_risk + order_max_loss:,.0f} > '
                       f'${budget:,.0f} = {risk_pct:.0%} of ${equity:,.0f})')
    if dir_counts.get(direction, 0) >= max_per_dir:
        return False, (f'direction-concentration cap '
                       f'({direction}: {dir_counts.get(direction, 0)}/{max_per_dir})')
    return True, ''


def _cross_system_allows(symbol: str, direction: str, order_max_loss: float,
                         ledger_path: Path = Path("/home/ubuntu/shared/active_portfolio_ledger.json")) -> tuple:
    """Check if the proposed order is allowed under cross-system risk and correlation limits."""
    try:
        import subprocess
        # Freshness Hook: Re-run updater to sync live positions on server
        subprocess.run(["python3", "/home/ubuntu/shared/update_portfolio_ledger.py"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    except Exception as e:
        print(f"[WARN] Failed to auto-update portfolio ledger: {e}")

    if not ledger_path.exists():
        return True, ""

    try:
        positions = json.loads(ledger_path.read_text())
    except Exception as e:
        print(f"[WARN] Failed to load active portfolio ledger: {e}")
        return True, ""

    # 1. Cross-System Risk Limit: Combined risk cap
    cross_risk_cap = float(os.environ.get("CROSS_SYSTEM_RISK_CAP", "5000.0"))
    existing_risk = sum(float(p.get("max_risk_usd", 0.0)) for p in positions)
    if existing_risk + order_max_loss > cross_risk_cap:
        return False, f"cross-system risk cap (${existing_risk + order_max_loss:,.0f} > ${cross_risk_cap:,.0f})"

    # 2. Correlation Filter: Avoid stacking correlated risk on SPY/QQQ/IWM
    correlated_indices = {"SPY", "QQQ", "IWM"}
    for p in positions:
        if p.get("system") == "openclaw":
            continue

        p_symbol = p.get("symbol", "")
        p_dir = p.get("direction", "unknown")
        if p_dir == "unknown" or p_dir != direction:
            continue

        if symbol in correlated_indices and p_symbol in correlated_indices:
            return False, f"cross-system index correlation ({symbol} and {p_symbol} both {direction})"
        
        if symbol == p_symbol:
            return False, f"cross-system position concentration ({symbol} already {direction} in {p.get('system')})"

    return True, ""


def _initial_portfolio_state(equity: float) -> dict:
    """Starting tally from LIVE Alpaca positions + the reconciled risk ledger.
    Unledgered open positions (e.g. a spread opened before this ledger existed)
    are counted and charged a conservative fair-share of the budget so the risk
    cap never under-counts."""
    open_unders = _open_option_underlyings()
    ledger = _reconcile_ledger(_load_risk_ledger(), open_unders)
    _save_risk_ledger(ledger)
    fair_share = PORTFOLIO_RISK_PCT * equity / max(1, MAX_CONCURRENT_POSITIONS)
    risk = 0.0
    dir_counts: dict = {}
    for u in open_unders:
        e = ledger.get(u)
        d = (e or {}).get('direction', 'unknown')
        risk += float((e or {}).get('max_loss', fair_share))
        dir_counts[d] = dir_counts.get(d, 0) + 1
    return {'open_unders': open_unders, 'ledger': ledger,
            'count': len(open_unders), 'risk': risk, 'dir_counts': dir_counts}


def _build_ic_payload(order: dict, qty: int) -> dict:
    """
    Build 4-leg Iron Condor mleg payload for Alpaca.
    Legs: buy put (wing) | sell put (body) | sell call (body) | buy call (wing)
    limit_price = minimum net credit we'll accept (95% of expected credit).
    """
    symbol = order['symbol']
    expiry = order['expiry']

    put_long_occ   = build_occ(symbol, expiry, 'put',  float(order['put_long_strike']))
    put_short_occ  = build_occ(symbol, expiry, 'put',  float(order['put_short_strike']))
    call_short_occ = build_occ(symbol, expiry, 'call', float(order['call_short_strike']))
    call_long_occ  = build_occ(symbol, expiry, 'call', float(order['call_long_strike']))

    net_credit = float(order['spread_mid'])   # scanner stores net credit as spread_mid for IC
    # Alpaca mleg sign convention: net DEBIT = positive limit_price, net CREDIT
    # = negative. An Iron Condor opens for a net credit, so submit a NEGATIVE
    # limit_price — the order only fills if the actual net credit is >= 95% of
    # expected (i.e. actual price <= -limit_floor). A positive value here would
    # be non-binding (any credit fill is "<=" a positive number), letting the
    # IC fill at an arbitrarily worse credit than intended.
    limit_floor = round(max(net_credit * 0.95, 0.01), 2)
    limit_px    = -limit_floor

    return {
        'order_class':   'mleg',
        'type':          'limit',
        'time_in_force': 'day',
        'limit_price':   str(limit_px),
        'qty':           str(qty),
        'legs': [
            {'symbol': put_long_occ,   'ratio_qty': 1, 'side': 'buy',  'position_effect': 'open'},
            {'symbol': put_short_occ,  'ratio_qty': 1, 'side': 'sell', 'position_effect': 'open'},
            {'symbol': call_short_occ, 'ratio_qty': 1, 'side': 'sell', 'position_effect': 'open'},
            {'symbol': call_long_occ,  'ratio_qty': 1, 'side': 'buy',  'position_effect': 'open'},
        ],
    }


def _check_candidates_freshness():
    """Alert via Telegram if candidates.txt has not been refreshed in over 7 days."""
    try:
        if CANDIDATES_FILE.exists():
            age_days = (datetime.now() - datetime.fromtimestamp(
                CANDIDATES_FILE.stat().st_mtime)).days
            if age_days > 7:
                last_updated = datetime.fromtimestamp(
                    CANDIDATES_FILE.stat().st_mtime).strftime('%Y-%m-%d')
                send_telegram(
                    f'⚠️ *Candidates list is {age_days} days old*\n'
                    f'Last updated: {last_updated}\n'
                    f'Run IBKR MultiSort screener and refresh candidates.txt\n'
                    f'_Quality degrades with stale tickers_'
                )
                print(f'⚠️  candidates.txt is {age_days} days old — refresh needed')
            else:
                print(f'✅ candidates.txt freshness OK ({age_days} days old)')
    except Exception as e:
        print(f'⚠️  freshness check: {e}')


# ─── Auto-executor ────────────────────────────────────────────────────────────

def _check_gates(order: dict, cooling_off: dict, today: str) -> tuple[str | None, bool]:
    """
    Evaluate safety gates for a single order.

    Returns (skip_reason, notify):
      - skip_reason is None if the order passes all gates, else a string
        describing why it should be skipped.
      - notify indicates whether a Telegram alert should be sent for the skip.

    IMPORTANT: Gate 1 (cooling-off) and Gate 3 (conviction) apply to ALL
    orders, including those explicitly marked 'approved' by Hermes.
    Hermes' `approval_manager.mark_approved()` only resolves Gate 2
    (events_status -> 'clear'); it does not re-check cooling-off or
    conviction. Bypassing those gates for 'approved' orders would let an
    order that never met the conviction threshold (or that hit a recent
    stop loss) execute purely because Hermes cleared an unrelated earnings
    check.
    """
    status = order['status']
    symbol = order['symbol']

    # ── Gate 1: Cooling-off period — always enforced ──────────────────────────
    if symbol in cooling_off and cooling_off[symbol] >= today:
        return f'Cooling-off until {cooling_off[symbol]} (recent stop loss)', False

    # ── Gate 2: Events not clear — bypassed only for explicitly 'approved' ────
    ev = order.get('events_status', 'uncertain')
    if status != 'approved' and ev != 'clear':
        return f'Events {ev.upper()} — verify calendar before executing', True

    # ── Gate 3: Conviction — always enforced, even for 'approved' orders ──────
    if not order.get('conviction_pass', False):
        return f'Conviction {order.get("conviction_score","?")} below threshold', False

    return None, False


def auto_execute_orders() -> dict:
    """
    Read pending_orders.json and auto-execute all orders that pass all gates.
    Gates (in order):
      ✅ Paper/live mode verified
      ✅ No existing open options positions (max 1 spread)
      ✅ Ticker not in cooling-off period (always enforced, even if 'approved')
      ✅ events_status == 'clear' (bypassed for explicitly 'approved' orders)
      ✅ conviction_pass == True (always enforced, even if 'approved')
      ✅ Iron Condor: all 4 strike fields present
    Sends individual Telegram per action. Returns summary dict.
    """
    if not ALPACA_KEY or not ALPACA_SECRET:
        print('⚠️  Alpaca credentials missing — skipping auto-execution')
        return {'executed': [], 'skipped': [], 'errors': []}

    # ── Safety gate: paper/live mode ─────────────────────────────────────────
    if not _check_paper_mode():
        return {'executed': [], 'skipped': [], 'errors': []}

    # ── Portfolio state: live Alpaca positions + reconciled risk ledger ──────
    # (Replaces the old binary "max 1 spread" gate. Per-order checks below
    #  enforce the count / risk-budget / direction caps cumulatively.)
    equity     = _get_equity()
    pf         = _initial_portfolio_state(equity)
    run_count  = pf['count']
    run_risk   = pf['risk']
    dir_counts = dict(pf['dir_counts'])
    ledger     = pf['ledger']
    print(f'  📊 Portfolio: {run_count}/{MAX_CONCURRENT_POSITIONS} positions, '
          f'${run_risk:,.0f}/${PORTFOLIO_RISK_PCT * equity:,.0f} risk budget used '
          f'(equity ${equity:,.0f})')

    pending_file = VAULT_DIR / 'OpenClaw/pending_orders.json'
    if not pending_file.exists():
        print('ℹ️  No pending_orders.json — nothing to execute')
        return {'executed': [], 'skipped': [], 'errors': []}

    data         = json.loads(pending_file.read_text())
    orders       = [o for o in data['orders'] if o['status'] in ('pending', 'approved')]
    cooling_off  = data.get('cooling_off', {})
    today        = datetime.now().strftime('%Y-%m-%d')

    if not orders:
        print('ℹ️  No pending or approved orders to execute')
        return {'executed': [], 'skipped': [], 'errors': []}

    executed = []
    skipped  = []
    errors   = []
    now      = datetime.now().strftime('%Y-%m-%d %H:%M')

    for order in orders:
        tid         = order['trade_id']
        symbol      = order['symbol']
        status      = order['status']
        spread_type = order.get('spread_type', 'bull_call')
        tl          = {'bull_call': 'Bull Call', 'bear_put': 'Bear Put',
                       'iron_condor': 'Iron Condor'}.get(
                         spread_type, spread_type.replace('_', ' ').title())

        print(f'\n  Processing [{tid}] {symbol} {tl} (status: {status})…')

        # ── Safety gates (cooling-off / events / conviction) ───────────────────
        skip_reason, notify = _check_gates(order, cooling_off, today)
        if skip_reason:
            ev = order.get('events_status', 'uncertain')
            if skip_reason.startswith('Events') and ev == 'uncertain':
                print(f"⚠️  WARNING: Order [{tid}] {symbol} is still marked 'uncertain' after the 21:10 Hermes resolver window!")
            order.update({'status': 'skipped', 'skip_reason': skip_reason, 'skipped_at': now})
            skipped.append(order)
            if notify:
                send_telegram(
                    f'⏸ *[{tid}] {symbol} {tl} — SKIPPED*\n'
                    f'Events status: *{ev.upper()}*\n'
                    f'Conviction: {order.get("conviction_score","?")}/100 ✅\n'
                    f'_Check IBKR calendar, then manually execute if clear._'
                )
            print(f'  ⏸ Skipped: {skip_reason}')
            continue

        # ── Build OCC symbols and payload ─────────────────────────────────────
        try:
            qty = _calc_qty(order['spread_mid'], order.get('conviction_score', 75))

            if spread_type == 'iron_condor':
                # Validate all 4 IC strike fields are present
                required = ['put_long_strike','put_short_strike',
                            'call_short_strike','call_long_strike']
                missing  = [f for f in required if not order.get(f)]
                if missing:
                    raise ValueError(f'IC missing fields: {missing}')
                payload  = _build_ic_payload(order, qty)
                limit_px = float(payload['limit_price'])
                print(f'  IC legs: put {order["put_long_strike"]}/{order["put_short_strike"]} '
                      f'| call {order["call_short_strike"]}/{order["call_long_strike"]}')
            else:
                otype     = 'call' if spread_type == 'bull_call' else 'put'
                long_occ  = build_occ(symbol, order['expiry'], otype, order['long_strike'])
                short_occ = build_occ(symbol, order['expiry'], otype, order['short_strike'])
                limit_px  = round(float(order['spread_mid']) * 1.08, 2)
                payload   = {
                    'order_class':   'mleg',
                    'type':          'limit',
                    'time_in_force': 'day',
                    'limit_price':   str(limit_px),
                    'qty':           str(qty),
                    'legs': [
                        {'symbol': long_occ,  'ratio_qty': 1, 'side': 'buy',  'position_effect': 'open'},
                        {'symbol': short_occ, 'ratio_qty': 1, 'side': 'sell', 'position_effect': 'open'},
                    ],
                }
                print(f'  Buy : {long_occ}')
                print(f'  Sell: {short_occ}')

            limit_desc = f'${-limit_px:.2f} credit' if limit_px < 0 else f'${limit_px:.2f} debit'
            print(f'  Qty : {qty} contracts | Limit: {limit_desc} (raw: {limit_px})')

        except Exception as e:
            errors.append({'order': order, 'error': str(e)})
            send_telegram(f'❌ *[{tid}] {symbol} — BUILD ERROR*\n`{e}`')
            print(f'  ❌ Build error: {e}')
            continue

        # ── Portfolio gate: count / risk-budget / direction concentration ──────
        direction = _DIRECTION.get(spread_type, 'neutral')
        order_ml  = _order_max_loss(spread_type, order, qty, limit_px)
        ok, why   = _portfolio_admits(order_ml, direction, equity,
                                      run_count, run_risk, dir_counts)
        if ok:
            # Phase 2: Cross-system portfolio risk auditor check
            ok, why = _cross_system_allows(symbol, direction, order_ml)

        if not ok:
            order.update({'status': 'skipped', 'skip_reason': why, 'skipped_at': now})
            skipped.append(order)
            send_telegram(
                f'⏸ *[{tid}] {symbol} {tl} — SKIPPED*\n'
                f'{why}\n_Portfolio budget protects the account; will retry when room frees up._'
            )
            print(f'  ⏸ Skipped (portfolio): {why}')
            continue

        # ── Submit to Alpaca ──────────────────────────────────────────────────
        try:
            r = requests.post(
                f'{ALPACA_BASE}/orders',
                headers=_alpaca_headers(),
                json=payload,
                timeout=15,
            )
        except Exception as e:
            errors.append({'order': order, 'error': str(e)})
            send_telegram(f'❌ *[{tid}] {symbol} — NETWORK ERROR*\n`{e}`')
            print(f'  ❌ Network error: {e}')
            continue

        if r.status_code in (200, 201):
            alpaca_id = r.json().get('id', 'unknown')
            cost      = round(float(order['spread_mid']) * qty * 100, 2)
            order.update({
                'status':          'executed',
                'executed_at':     now,
                'alpaca_order_id': alpaca_id,
                'approved_by':     'Auto',
                'approved_at':     now,
                'qty':             qty,
                'submitted_limit': limit_px,   # for fill quality tracking
            })
            executed.append(order)

            # ── Update portfolio tallies + persist the reconciled risk ledger ──
            run_count += 1
            run_risk  += order_ml
            dir_counts[direction] = dir_counts.get(direction, 0) + 1
            ledger[symbol] = {'direction': direction, 'max_loss': round(order_ml, 2),
                              'trade_id': tid, 'opened_at': now}
            _save_risk_ledger(ledger)

            # Append to execution log
            log_file  = VAULT_DIR / 'OpenClaw/10_Execution_Log.md'
            log_entry = (
                f'\n## {now} — [{tid}] AUTO-EXECUTED\n'
                f'- **Symbol**: {symbol}  {tl}\n'
                f'- **Strikes**: ${order["long_strike"]} / ${order["short_strike"]}\n'
                f'- **Expiry**: {order["expiry"]}  DTE: {order["dte"]}\n'
                f'- **Debit**: ${order["spread_mid"]:.2f}  '
                f'R:R: {order["rr"]:.1f}x  '
                f'Qty: {qty} contracts\n'
                f'- **Total risk**: ${cost:.2f}\n'
                f'- **Conviction**: {order["conviction_score"]}/100\n'
                f'- **Alpaca order ID**: {alpaca_id}\n'
                f'- **Mode**: Autonomous (vault_updater v4)\n'
            )
            if log_file.parent.exists():
                with open(log_file, 'a') as f:
                    f.write(log_entry)

            send_telegram(
                f'🚀 *[{tid}] {symbol} {tl} — EXECUTED*\n'
                f'Strikes: ${order["long_strike"]} / ${order["short_strike"]} | '
                f'Expiry: {order["expiry"]} ({order["dte"]}d)\n'
                f'Debit: ${order["spread_mid"]} × {qty} contracts = *${cost:.0f} risk*\n'
                f'Limit: {limit_desc} | R:R: {order["rr"]}x\n'
                f'Conviction: {order["conviction_score"]}/100\n'
                f'Alpaca ID: `{alpaca_id}`'
            )
            print(f'  🚀 Executed! Alpaca ID: {alpaca_id}')

        else:
            err_text = r.text[:300]
            errors.append({'order': order, 'error': f'HTTP {r.status_code}: {err_text}'})
            send_telegram(
                f'❌ *[{tid}] {symbol} — ALPACA ERROR*\n'
                f'HTTP {r.status_code}\n`{err_text}`'
            )
            print(f'  ❌ Alpaca error {r.status_code}: {err_text[:100]}')

    # ── Persist updated statuses ──────────────────────────────────────────────
    updated_map = {o['trade_id']: o for o in executed + skipped}
    data['orders'] = [updated_map.get(o['trade_id'], o) for o in data['orders']]
    pending_file.write_text(json.dumps(data, indent=2))
    print(f'\n  ✅ pending_orders.json saved '
          f'(executed: {len(executed)}, skipped: {len(skipped)}, errors: {len(errors)})')

    return {'executed': executed, 'skipped': skipped, 'errors': errors}


# ─── Account capital ──────────────────────────────────────────────────────────

def get_account_capital():
    """Fetch live equity from Alpaca paper account."""
    try:
        r = requests.get(
            f'{ALPACA_BASE}/account',
            headers=_alpaca_headers(),
            timeout=5,
        )
        if r.status_code == 200:
            equity = float(r.json().get('equity', 0))
            return f'${equity:,.0f}'
    except Exception as e:
        print(f'⚠️  Capital fetch failed: {e}')
    return '~$2,946'


# ─── Read latest scan ─────────────────────────────────────────────────────────

def read_latest_scan():
    scans = sorted(SCANS_DIR.glob('scan_*.json'))
    if not scans:
        print('❌ No scan file found')
        return None
    latest = scans[-1]
    print(f'📂 Reading: {latest.name}')
    with open(latest) as f:
        return json.load(f)


# ─── 07 Macro Context ─────────────────────────────────────────────────────────

def update_macro_context(scan):
    path      = VAULT_DIR / 'OpenClaw/07_Macro_Context.md'
    today     = datetime.now().strftime('%Y-%m-%d %H:%M')
    macro     = scan.get('macro', {})
    market_ok = scan.get('market_ok', False)
    regime    = scan.get('regime', 'unknown')
    alerts_count = len(scan.get('alerts', []))

    new_section  = f'\n## {today} — Auto Update\n\n'
    new_section += f'Regime: `{regime}` | Market: {"✅ OK" if market_ok else "⚠️ Elevated"}\n\n'
    new_section += '| Indicator | Price | Change |\n'
    new_section += '|-----------|-------|--------|\n'
    for sym, data in macro.items():
        chg   = data.get('change_pct', 0)
        arrow = '↑' if chg > 0 else '↓'
        new_section += f'| {sym} | ${data.get("price","N/A")} | {arrow} {chg}% |\n'

    new_section += f'\nSpreads found: {alerts_count}\n\n---\n'

    if path.exists():
        existing  = path.read_text()
        lines     = existing.split('\n')
        insert_at = 1
        for i, line in enumerate(lines):
            if i > 0 and line.startswith('#'):
                insert_at = i
                break
        lines.insert(insert_at, new_section)
        path.write_text('\n'.join(lines))
    else:
        path.write_text(f'# Macro Context\n{new_section}')
    print('✅ Updated 07_Macro_Context.md')


# ─── 08 Next Actions ──────────────────────────────────────────────────────────

def update_next_actions(scan, exec_results: dict):
    path    = VAULT_DIR / 'OpenClaw/08_Next_Actions.md'
    today   = datetime.now().strftime('%Y-%m-%d %H:%M')
    regime  = scan.get('regime', 'unknown')
    holds   = scan.get('holds', [])
    events_blocked = scan.get('events_blocked', [])
    executed = exec_results.get('executed', [])
    skipped  = exec_results.get('skipped', [])
    errors   = exec_results.get('errors', [])

    section  = f'\n## {today} — Autonomous Run\n\n'
    section += f'Regime: `{regime}` | Mode: 🤖 Autonomous\n\n'

    # ── Executed ──────────────────────────────────────────────────────────────
    if executed:
        section += f'### 🚀 AUTO-EXECUTED ({len(executed)})\n\n'
        for o in executed:
            tl  = {'bull_call': 'Bull Call', 'bear_put': 'Bear Put',
                   'iron_condor': 'Iron Condor'}.get(
                     o.get('spread_type', ''), o.get('spread_type', '').title())
            qty = o.get('qty', 1)
            section += (
                f'**[{o.get("trade_id","?")}] {o["symbol"]} '
                f'${o["long_strike"]}/{o["short_strike"]} ({tl})**\n'
                f'- Expiry: {o.get("expiry","")} ({o["dte"]} DTE) | '
                f'Debit: ${o["spread_mid"]} × {qty} contracts\n'
                f'- R:R: {o["rr"]}:1 | Conviction: {o.get("conviction_score","?")}/100\n'
                f'- Alpaca ID: `{o.get("alpaca_order_id","?")}`\n\n'
            )

    # ── Skipped ───────────────────────────────────────────────────────────────
    if skipped:
        section += f'### ⏸ SKIPPED ({len(skipped)})\n'
        for o in skipped:
            tl = {'bull_call': 'Bull Call', 'bear_put': 'Bear Put',
                  'iron_condor': 'Iron Condor'}.get(
                    o.get('spread_type', ''), o.get('spread_type', '').title())
            section += (f'- [{o["trade_id"]}] {o["symbol"]} ({tl}) '
                        f'— {o.get("skip_reason","?")}\n')
        section += '\n'

    # ── Errors ────────────────────────────────────────────────────────────────
    if errors:
        section += f'### ❌ ERRORS ({len(errors)})\n'
        for e in errors:
            o = e.get('order', {})
            section += f'- [{o.get("trade_id","?")}] {o.get("symbol","?")} — {e.get("error","")}\n'
        section += '\n'

    # ── Events blocked ────────────────────────────────────────────────────────
    if events_blocked:
        section += f'### 🚫 Events Blocked ({len(events_blocked)})\n'
        for e in events_blocked:
            section += f'- {e}\n'
        section += '\n'

    if not executed and not skipped and not errors:
        section += '### No qualifying spreads tonight — standing by\n\n'

    # ── Holds ──────────────────────────────────────────────────────────────────
    section += f'### Holds / Rejects ({len(holds)})\n'
    for h in holds:
        section += f'- {h}\n'

    section += '\n---\n'

    if path.exists():
        path.write_text(section + path.read_text())
    else:
        path.write_text(f'# Next Actions\n**Updated:** {today}\n{section}')
    print('✅ Updated 08_Next_Actions.md')


# ─── 09 Daily Briefing ────────────────────────────────────────────────────────

def generate_daily_briefing(scan, exec_results: dict):
    today    = datetime.now().strftime('%Y-%m-%d %H:%M')
    capital  = get_account_capital()
    macro    = scan.get('macro', {})
    regime   = scan.get('regime', 'unknown')
    market_ok= scan.get('market_ok', False)
    executed = exec_results.get('executed', [])
    skipped  = exec_results.get('skipped', [])

    vix = macro.get('VIX', {}).get('price', 'N/A')
    xle = macro.get('XLE', {}).get('change_pct', 'N/A')
    spy = macro.get('SPY', {}).get('change_pct', 'N/A')

    briefing = f"""# OpenClaw Daily Briefing
**Generated:** {today} Bangkok | Mode: 🤖 Autonomous

---

## Portfolio
- Capital: {capital}
- Scanner: ✅ v4.0 — autonomous execution

## Market
- Condition: {'✅ OK' if market_ok else '⚠️ Elevated risk'}
- Regime: `{regime}`
- VIX: {vix} | SPY: {spy}% | XLE: {xle}%

## Tonight's Results
- Auto-executed: {len(executed)}
- Skipped: {len(skipped)}
- Events blocked: {len(scan.get('events_blocked', []))}

"""

    if executed:
        briefing += f'## ✅ Executed ({len(executed)})\n\n'
        for o in executed:
            tl  = {'bull_call': 'Bull Call', 'bear_put': 'Bear Put',
                   'iron_condor': 'Iron Condor'}.get(
                     o.get('spread_type', ''), o.get('spread_type', '').title())
            qty  = o.get('qty', 1)
            cost = round(float(o['spread_mid']) * qty * 100, 2)
            briefing += (
                f'### [{o.get("trade_id","?")}] {o["symbol"]} '
                f'${o["long_strike"]}/{o["short_strike"]} {o.get("expiry","")} ({tl})\n\n'
                f'| Field | Value |\n|-------|-------|\n'
                f'| Debit | ${o["spread_mid"]} |\n'
                f'| Qty | {qty} contracts |\n'
                f'| Total risk | ${cost:.2f} |\n'
                f'| R:R | {o["rr"]}:1 |\n'
                f'| DTE | {o["dte"]} days |\n'
                f'| Conviction | {o.get("conviction_score","?")}/100 |\n'
                f'| Alpaca ID | `{o.get("alpaca_order_id","?")}` |\n\n'
            )

    if skipped:
        briefing += f'## ⏸ Skipped ({len(skipped)})\n\n'
        for o in skipped:
            tl = {'bull_call': 'Bull Call', 'bear_put': 'Bear Put',
                  'iron_condor': 'Iron Condor'}.get(
                    o.get('spread_type', ''), o.get('spread_type', '').title())
            briefing += (f'- **[{o["trade_id"]}] {o["symbol"]} ({tl})** — '
                         f'{o.get("skip_reason","?")}\n')
        briefing += '\n'

    if not executed and not skipped:
        briefing += '## No qualifying trades tonight. Capital preserved.\n\n'

    briefing += '---\n*Auto-generated by server. Pull from GitHub to see latest.*\n'

    path = VAULT_DIR / 'OpenClaw/09_Daily_Briefing.md'
    path.write_text(briefing)
    print('✅ Generated 09_Daily_Briefing.md')


# ─── Nova Session Prompt ──────────────────────────────────────────────────────

def generate_nova_prompt(scan, exec_results: dict):
    today    = datetime.now().strftime('%Y-%m-%d %H:%M')
    capital  = get_account_capital()
    executed = exec_results.get('executed', [])
    skipped  = exec_results.get('skipped', [])
    macro    = scan.get('macro', {})
    regime   = scan.get('regime', 'unknown')

    exec_text = ''
    if executed:
        exec_text = f'\n✅ AUTO-EXECUTED TONIGHT ({len(executed)}):\n'
        for o in executed:
            tl = {'bull_call': 'Bull Call', 'bear_put': 'Bear Put',
                  'iron_condor': 'Iron Condor'}.get(
                    o.get('spread_type', ''), o.get('spread_type', '').title())
            exec_text += (
                f'[{o.get("trade_id","?")}] {o["symbol"]} '
                f'${o["long_strike"]}/{o["short_strike"]} {o.get("expiry","")} ({tl})\n'
                f'- Debit: ${o["spread_mid"]} × {o.get("qty",1)} | Alpaca: {o.get("alpaca_order_id","?")}\n'
            )
    if skipped:
        exec_text += f'\n⏸ SKIPPED TONIGHT ({len(skipped)}):\n'
        for o in skipped:
            exec_text += f'[{o["trade_id"]}] {o["symbol"]} — {o.get("skip_reason","?")}\n'

    if not exec_text:
        exec_text = '\nNo trades tonight. Standing by.\n'

    macro_text = '\nMACRO:\n'
    for sym, data in macro.items():
        macro_text += f'- {sym}: ${data.get("price")} ({data.get("change_pct")}%)\n'
    macro_text += f'- Regime: {regime}\n'

    prompt = f"""NOVA — new session starting. Load complete context.

PROJECT: OpenClaw Autonomous Options System v4
ACCOUNT: Alpaca Paper Trading
CAPITAL: ~{capital} | DATE: {today}

RULESET v4.0:
- Conviction ≥75/100 | IV Rank ≤40% | IV Last ≤45% | Premium $0.30-$0.60
- Spread ≤$3 | Price $10-$40 | DTE 25-40 days
- Earnings ban ±14 days | OI ≥500 both legs
- Bid >$0.00 | Bid-ask ≤$0.10/leg | Max 1 position
- Events Calendar auto-checked (Tradier) | Conviction scored automatically
- IV Last >45% = auto-reject (L019)

PIPELINE v4 (fully autonomous):
- Events check: Tradier fundamentals/calendars (±14 day ban)
- Conviction: rule-based offline scorer (upgrades to Claude API if key present)
- Auto-execution: vault_updater executes on clear + conviction pass
- Position size: fixed $200 risk per trade
- Skip conditions: Iron Condor (4-leg pending), events uncertain
- Notifications: Telegram per action + nightly summary + 7:30 AM morning report

NOVA ROLE:
- Review execution log and answer questions about trades
- Assist with manual execution of skipped orders if calendar verified
- Strategy review and next-step planning
- No independent market data generation
{exec_text}
WATCHLIST:
1. PR ~$19.91 | KNOWN_HOLD — recheck Jun 17 after dividend Jun 16
2. CCL ~$26.84 | IV 55%+ | Iran deal catalyst needed
3. NCLH ~$17.36 | IV 58%+ | same as CCL
4. AAL ~$13.14 | Conviction ≥75 required
5. VALE ~$16.25 | OI thin | recheck after May 30 earnings
{macro_text}
RECENT TRADES:
- AAL $12/$13: -$23 (IV breach at entry)
- F $12.50/$14: +$76 paper (lucky — position assumed closed, L012/L017)
- IAG $22/$24 Jun18: -$50 est. (stop triggered May 14, gold pullback, L010)
- HMC $27.5/$30 Jun18: closed May 19 via mleg fill (L018)

ACTIVE POSITIONS: None (check Alpaca for latest)

KNOWN_HOLDS (do not score until recheck date):
- PR: recheck Jun 17, 2026

SERVER: ubuntu@43.156.9.185

Confirm context loaded. Standing by."""

    path = VAULT_DIR / 'templates/Nova_Session_Prompt.md'
    path.parent.mkdir(exist_ok=True)
    path.write_text(f'# Nova Session Prompt\n**Generated:** {today}\n\n---\n\n{prompt}')
    print('✅ Generated templates/Nova_Session_Prompt.md')

    backup = SCANS_DIR / f"Nova_Prompt_{datetime.now().strftime('%Y%m%d')}.txt"
    backup.write_text(prompt)
    print(f'✅ Backup: {backup.name}')


# ─── Git push ─────────────────────────────────────────────────────────────────

def git_push():
    subprocess.run(
        ['git', 'add', 'OpenClaw/', 'templates/'],
        cwd=VAULT_DIR, capture_output=True, text=True,
    )

    commit = subprocess.run(
        ['git', 'commit', '-m', f'Auto-update {datetime.now().strftime("%Y-%m-%d %H:%M")}'],
        cwd=VAULT_DIR, capture_output=True, text=True,
    )
    if 'nothing to commit' in commit.stdout:
        print('ℹ️  No changes to commit')
        return

    pull = subprocess.run(
        ['git', 'pull', '--rebase', 'origin', 'main'],
        cwd=VAULT_DIR, capture_output=True, text=True,
    )
    if pull.returncode != 0:
        print(f'⚠️  Git pull issue: {pull.stderr}')
    else:
        print('✅ Git pull successful')

    push = subprocess.run(
        ['git', 'push', 'origin', 'main'],
        cwd=VAULT_DIR, capture_output=True, text=True,
    )
    if push.returncode == 0:
        print('✅ Git pushed to GitHub')
    else:
        print(f'⚠️  Git push issue: {push.stderr}')


# ─── Scan timestamp ───────────────────────────────────────────────────────────

def _stamp_scan_time(scan_time: str):
    """Stamp last_scan_time into pending_orders.json every run."""
    pending_file = VAULT_DIR / 'OpenClaw/pending_orders.json'
    try:
        if pending_file.exists():
            data = json.loads(pending_file.read_text())
        else:
            data = {'orders': []}
        data['last_scan_time'] = scan_time
        pending_file.write_text(json.dumps(data, indent=2))
        print(f'✅ Stamped last_scan_time: {scan_time}')
    except Exception as e:
        print(f'⚠️  _stamp_scan_time: {e}')


# ─── Run ──────────────────────────────────────────────────────────────────────

def run():
    print(f"\n{'='*60}")
    print(f"VAULT UPDATER v4.0 (AUTONOMOUS) — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    scan = read_latest_scan()
    if not scan:
        return

    alerts_count   = len(scan.get('alerts', []))
    approved_count = len(scan.get('approved_alerts', []))
    print(f'Spreads: {alerts_count} | Approved: {approved_count} | '
          f'Holds: {len(scan.get("holds", []))} | Regime: {scan.get("regime","unknown")}\n')

    # ── Candidates freshness check ────────────────────────────────────────────
    print('--- Candidates Freshness ---')
    _check_candidates_freshness()

    # ── Auto-execute qualifying orders ────────────────────────────────────────
    print('\n--- Auto-Executor ---')
    exec_results = auto_execute_orders()

    # ── Position monitor + auto-exit ─────────────────────────────────────────
    print('\n--- Position Monitor ---')
    try:
        import position_monitor
        position_monitor.run()
    except Exception as e:
        print(f'⚠️  Position monitor error: {e}')

    # ── Stamp scan time ───────────────────────────────────────────────────────
    _stamp_scan_time(datetime.now().strftime('%Y-%m-%d %H:%M'))

    # ── Update vault markdown ─────────────────────────────────────────────────
    print('\n--- Vault Update ---')
    update_macro_context(scan)
    update_next_actions(scan, exec_results)
    generate_daily_briefing(scan, exec_results)
    generate_nova_prompt(scan, exec_results)
    git_push()

    # ── Nightly summary Telegram ──────────────────────────────────────────────
    print('\n--- Telegram Summary ---')
    msg = build_telegram_summary(scan, exec_results, datetime.now().strftime('%Y-%m-%d %H:%M'))
    send_telegram(msg)

    # ── Final status ──────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print('VAULT UPDATE COMPLETE')
    print(f'  Executed : {len(exec_results["executed"])}')
    print(f'  Skipped  : {len(exec_results["skipped"])}')
    print(f'  Errors   : {len(exec_results["errors"])}')
    print('Pull on Mac: cd /Users/SkonP/AI_Prompt/Obsidient/SkonVault && git pull origin main')
    print(f"{'='*60}\n")


if __name__ == '__main__':
    run()
