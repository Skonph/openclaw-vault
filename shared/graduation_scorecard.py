#!/usr/bin/env python3
"""
graduation_scorecard.py — unified, COST-AWARE track-record scorecard across the
three paper trading systems (Tradier, OpenClaw, IBKR guardrail). Answers one
question honestly: *is the edge real and large enough — AFTER real-world costs —
to graduate to a live account?*

It reads each system's REALIZED (closed-trade) P&L, normalizes it, subtracts
per-trade commissions, and assesses graduation readiness on NET numbers against
explicit thresholds PLUS the fixed monthly overhead (server + API) that the
operation must out-earn. Writes a Markdown scorecard + JSON, optionally Telegram.

Costs (env-tunable):
  - Commissions/contract: Tradier $0.35, Alpaca ~$0.05 (reg/exchange; $0 comm), IBKR $0.65.
    Round-trip fee = per_contract × legs × 2 × qty  (open + close).
  - Fixed monthly: $90 (server $70 + API/data $20).
  - Real account size to deploy: $12,000.

DESIGN: parsers normalize each source and attach fee + net_pnl; the metric,
economics, and assessment functions are PURE (no I/O) and unit-tested.

    python3 graduation_scorecard.py [--include-test] [--telegram] [--stdout]
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path


# ── path helpers (exception-safe on inaccessible parents) ───────────────────────
def _exists(path) -> bool:
    try:
        return path is not None and Path(path).exists()
    except OSError:
        return False


def _read_text_safe(path):
    try:
        if _exists(path):
            return Path(path).read_text()
    except OSError:
        pass
    return None


def _first_existing(*paths):
    for p in paths:
        if _exists(p):
            return Path(p)
    return Path(paths[0]) if paths else None


# ── Config (env-overridable) ────────────────────────────────────────────────────
TRADIER_LOG = Path(os.environ.get('SC_TRADIER_LOG',
    _first_existing('/home/ubuntu/trading-bot/trade_log.jsonl',
                    str(Path.home() / 'trading-bot/trade_log.jsonl'))))
OPENCLAW_PENDING = Path(os.environ.get('SC_OPENCLAW_PENDING',
    _first_existing('/home/ubuntu/openclaw-vault/OpenClaw/pending_orders.json',
                    '/home/ubuntu/openclaw/pending_orders.json')))
GUARDRAIL_POS = Path(os.environ.get('SC_GUARDRAIL_POS',
    _first_existing('/home/ubuntu/guardrail/data/session_positions.json')))

STARTING_CAPITAL = {
    'tradier':   float(os.environ.get('SC_CAP_TRADIER',   '16000')),  # PRIMARY real account (2026-06-20)
    'openclaw':  float(os.environ.get('SC_CAP_OPENCLAW',  '3000')),   # secondary / paper
    'guardrail': float(os.environ.get('SC_CAP_GUARDRAIL', '100000')), # secondary / paper (sizing optimistic — see note)
}

# Real-world costs (broker research + user inputs, all env-tunable)
COMMISSION_PER_CONTRACT = {
    'tradier':   float(os.environ.get('SC_COMM_TRADIER',   '0.35')),  # Tradier $0.35/contract
    'openclaw':  float(os.environ.get('SC_COMM_OPENCLAW',  '0.05')),  # Alpaca $0 comm + ~$0.05 reg/exchange
    'guardrail': float(os.environ.get('SC_COMM_GUARDRAIL', '0.65')),  # IBKR ~$0.65/contract
}
FIXED_MONTHLY_COST = float(os.environ.get('SC_FIXED_MONTHLY', '90'))    # server $70 + API/data $20
REAL_ACCOUNT_SIZE  = float(os.environ.get('SC_REAL_CAPITAL', '16000'))  # capital to deploy live (2026-06-20: raised 15k→16k → fixed drag 7.2%→6.75%)
TARGET_FIXED_DRAG  = float(os.environ.get('SC_TARGET_DRAG', '0.07'))    # acceptable fixed-cost drag (≤7%/yr)

# Graduation thresholds (on NET numbers). A system is "ready" only if ALL pass.
GRAD = {
    'min_sample':        int(os.environ.get('SC_MIN_SAMPLE', '30')),
    'min_expectancy':    float(os.environ.get('SC_MIN_EXPECTANCY', '0.0')),
    'min_profit_factor': float(os.environ.get('SC_MIN_PF', '1.3')),
    'max_dd_pct':        float(os.environ.get('SC_MAX_DD_PCT', '0.15')),
}

_DIRECTION = {
    'bull put spread': 'bull', 'bear call spread': 'bear', 'iron condor': 'neutral',
    'bull_call': 'bull', 'bear_put': 'bear', 'iron_condor': 'neutral',
    'debit_call_spread': 'bull', 'debit_put_spread': 'bear',
    'credit_put_spread': 'bull', 'credit_call_spread': 'bear',
}


def _is_test_record(trade_id, order_id=None, success=None) -> bool:
    if success is False:
        return True
    for v in (trade_id, order_id):
        if not v:
            continue
        s = str(v)
        if re.search(r'TEST', s, re.I) or re.fullmatch(r'[A-Z]{1,3}', s) \
           or s.startswith('reconciled') or s in ('TEST001', 'AAA', 'BBB', 'CCC', '?'):
            return True
    return False


def _trade_fee(system: str, strategy: str, qty=1) -> float:
    """Round-trip commission: per_contract × legs × 2 (open+close) × qty."""
    legs = 4 if 'condor' in (strategy or '').lower() else 2
    per_contract = COMMISSION_PER_CONTRACT.get(system, 0.35)
    try:
        q = max(1, int(qty or 1))
    except (TypeError, ValueError):
        q = 1
    return round(per_contract * legs * 2 * q, 2)


def _parse_date(s):
    if not s:
        return None
    s = str(s)
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00')).replace(tzinfo=None)
    except ValueError:
        return None


# ── Parsers → common trade schema ───────────────────────────────────────────────
# {system, symbol, strategy, direction, pnl(gross), qty, fee, net_pnl, closed_at, reason, is_test}
def _norm(system, symbol, strategy, pnl, qty, closed_at, reason, is_test):
    fee = _trade_fee(system, strategy, qty)
    return {'system': system, 'symbol': symbol, 'strategy': strategy,
            'direction': _DIRECTION.get((strategy or '').lower(), 'neutral'),
            'pnl': round(float(pnl), 2), 'qty': qty, 'fee': fee,
            'net_pnl': round(float(pnl) - fee, 2),
            'closed_at': closed_at or '', 'reason': reason or '', 'is_test': is_test}


def parse_tradier(path: Path) -> list:
    out, text = [], _read_text_safe(path)
    if text is None:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get('type') != 'exit' or 'realized_pnl' not in r:
            continue
        out.append(_norm('tradier', r.get('symbol', '?'), r.get('strategy', 'Unknown'),
                         r.get('realized_pnl', 0), r.get('qty', 1),
                         r.get('exited_at') or r.get('date', ''), r.get('exit_reason', ''),
                         _is_test_record(r.get('trade_id'), r.get('order_id'), r.get('success'))))
    return out


def parse_openclaw(path: Path) -> list:
    out, text = [], _read_text_safe(path)
    if text is None:
        return out
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return out
    for r in data.get('pnl_history', []):
        out.append(_norm('openclaw', r.get('symbol', '?'), r.get('spread_type', 'unknown'),
                         r.get('pnl', 0), r.get('qty', 1), r.get('timestamp', ''),
                         r.get('reason', ''), _is_test_record(r.get('trade_id'))))
    return out


def parse_guardrail(path: Path) -> list:
    out, text = [], _read_text_safe(path)
    if text is None:
        return out
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return out
    for p in (data if isinstance(data, list) else []):
        if p.get('status') != 'CLOSED' or p.get('realized_pnl_usd') is None:
            continue
        struct = p.get('structure', 'unknown')
        t = _norm('guardrail', p.get('symbol', '?'), struct, p.get('realized_pnl_usd', 0),
                  p.get('qty', 1), p.get('closed_at', ''), p.get('close_reason', ''),
                  _is_test_record(p.get('plan_id')))
        t['direction'] = p.get('direction') or t['direction']
        out.append(t)
    return out


# ── Pure metrics ────────────────────────────────────────────────────────────────
def compute_metrics(trades: list, starting_capital: float = 0.0, pnl_key: str = 'pnl') -> dict:
    n = len(trades)
    if n == 0:
        return {'n': 0, 'total_pnl': 0.0, 'win_rate': None, 'expectancy': None,
                'profit_factor': None, 'avg_win': None, 'avg_loss': None,
                'max_drawdown': 0.0, 'max_dd_pct': None, 'best': None, 'worst': None,
                'by_strategy': {}, 'by_direction': {}, 'total_fees': 0.0}
    pnls = [t[pnl_key] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total = sum(pnls)
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    ordered = sorted(trades, key=lambda t: t.get('closed_at') or '')
    cum = peak = max_dd = 0.0
    for t in ordered:
        cum += t[pnl_key]; peak = max(peak, cum); max_dd = max(max_dd, peak - cum)

    def _bucket(key):
        b = {}
        for t in trades:
            d = b.setdefault(t.get(key, '?'), {'n': 0, 'pnl': 0.0, 'wins': 0})
            d['n'] += 1; d['pnl'] += t[pnl_key]; d['wins'] += 1 if t[pnl_key] > 0 else 0
        for d in b.values():
            d['win_rate'] = d['wins'] / d['n'] if d['n'] else None
            d['pnl'] = round(d['pnl'], 2)
        return b

    return {
        'n': n, 'total_pnl': round(total, 2), 'win_rate': len(wins) / n,
        'expectancy': round(total / n, 2),
        'profit_factor': (gross_win / gross_loss) if gross_loss > 0 else float('inf'),
        'avg_win': round(sum(wins) / len(wins), 2) if wins else None,
        'avg_loss': round(sum(losses) / len(losses), 2) if losses else None,
        'max_drawdown': round(max_dd, 2),
        'max_dd_pct': (max_dd / starting_capital) if starting_capital else None,
        'best': round(max(pnls), 2), 'worst': round(min(pnls), 2),
        'by_strategy': _bucket('strategy'), 'by_direction': _bucket('direction'),
        'total_fees': round(sum(t.get('fee', 0) for t in trades), 2),
    }


def compute_economics(net_total: float, trades: list, fixed_monthly: float,
                      real_capital: float, target_drag: float) -> dict:
    """Fixed-cost coverage + return on the real account size to be deployed."""
    dates = sorted(d for d in (_parse_date(t.get('closed_at')) for t in trades) if d)
    months = None
    if len(dates) >= 2:
        days = (dates[-1] - dates[0]).days
        months = max(days / 30.44, 1 / 30.44)  # avoid div-by-zero on same-day
    monthly_runrate = (net_total / months) if months else None
    fixed_annual = fixed_monthly * 12
    return {
        'fixed_monthly': fixed_monthly, 'fixed_annual': fixed_annual,
        'real_capital': real_capital,
        'fixed_drag_pct': (fixed_annual / real_capital) if real_capital else None,
        'months_elapsed': round(months, 2) if months else None,
        'net_monthly_runrate': round(monthly_runrate, 2) if monthly_runrate is not None else None,
        'net_annual_runrate': round(monthly_runrate * 12, 2) if monthly_runrate is not None else None,
        'net_annual_return_pct': (monthly_runrate * 12 / real_capital)
                                 if (monthly_runrate is not None and real_capital) else None,
        'covers_fixed': (monthly_runrate is not None and monthly_runrate > fixed_monthly),
        'min_viable_capital': round(fixed_annual / target_drag, 0) if target_drag else None,
        'target_drag': target_drag,
    }


def assess_graduation(m: dict, grad: dict = None, econ: dict = None) -> dict:
    """READY iff sample + NET expectancy + NET profit factor + DD pass, AND (if
    economics supplied) the net run-rate covers the fixed monthly overhead."""
    grad = grad or GRAD
    reasons = []
    if m['n'] < grad['min_sample']:
        reasons.append(f"sample {m['n']} < {grad['min_sample']} required")
    if m['n'] > 0:
        if (m['expectancy'] or 0) <= grad['min_expectancy']:
            reasons.append(f"net expectancy ${m['expectancy']} not > ${grad['min_expectancy']}")
        pf = m['profit_factor']
        if pf != float('inf') and pf < grad['min_profit_factor']:
            reasons.append(f"net profit factor {pf:.2f} < {grad['min_profit_factor']}")
        if m['max_dd_pct'] is not None and m['max_dd_pct'] > grad['max_dd_pct']:
            reasons.append(f"max drawdown {m['max_dd_pct']:.1%} > {grad['max_dd_pct']:.0%} cap")
    if econ is not None and not econ['covers_fixed']:
        rr = econ['net_monthly_runrate']
        reasons.append(f"net run-rate ${rr if rr is not None else '?'}/mo does not cover "
                       f"${econ['fixed_monthly']:.0f}/mo fixed overhead")
    return {'ready': len(reasons) == 0 and m['n'] >= grad['min_sample'], 'reasons': reasons}


# ── Rendering ────────────────────────────────────────────────────────────────────
def _fmt_pf(pf):
    return '∞' if pf == float('inf') else (f'{pf:.2f}' if pf is not None else '—')


def _section(title, gross, net, cap, assess):
    if gross['n'] == 0:
        return f"### {title}\n_No closed trades yet._\n"
    wr = f"{gross['win_rate']*100:.0f}%"
    dd = f"${net['max_drawdown']:,.0f}" + (f" ({net['max_dd_pct']:.1%})" if net['max_dd_pct'] is not None else '')
    status = '✅ READY' if assess['ready'] else '⏳ NOT READY'
    lines = [
        f"### {title} — {status}",
        f"- Trades: **{gross['n']}**  |  Win rate: **{wr}**  |  Fees paid: **${net['total_fees']:,.2f}**",
        f"- **Gross**: P&L ${gross['total_pnl']:,.2f} · exp ${gross['expectancy']} · PF {_fmt_pf(gross['profit_factor'])}",
        f"- **Net (after commissions)**: P&L ${net['total_pnl']:,.2f} · exp ${net['expectancy']} · "
        f"PF {_fmt_pf(net['profit_factor'])} · max DD {dd}",
    ]
    if net['by_strategy']:
        lines.append("- By strategy (net): " + "; ".join(
            f"{k} ({v['n']}, {v['win_rate']*100:.0f}%WR, ${v['pnl']:+.0f})"
            for k, v in sorted(net['by_strategy'].items())))
    if not assess['ready']:
        lines.append("- Gaps: " + "; ".join(assess['reasons']))
    return "\n".join(lines) + "\n"


def _economics_block(econ) -> str:
    fd = f"{econ['fixed_drag_pct']:.1%}" if econ['fixed_drag_pct'] is not None else '—'
    rr = f"${econ['net_monthly_runrate']:,.0f}/mo" if econ['net_monthly_runrate'] is not None else '— (need ≥2 dated trades)'
    ar = f"{econ['net_annual_return_pct']:.1%}" if econ['net_annual_return_pct'] is not None else '—'
    cov = '✅ covers fixed costs' if econ['covers_fixed'] else '❌ does NOT cover fixed costs'
    return "\n".join([
        "## Economics (the real break-even)",
        f"- Fixed overhead: **${econ['fixed_monthly']:.0f}/mo (${econ['fixed_annual']:,.0f}/yr)** "
        f"= **{fd}** of the ${econ['real_capital']:,.0f} live account.",
        f"- Net P&L run-rate: **{rr}**  (≈ annualized **{ar}** on ${econ['real_capital']:,.0f})  →  {cov}.",
        f"- Min viable capital for ≤{econ['target_drag']:.0%} fixed-cost drag: "
        f"**${econ['min_viable_capital']:,.0f}**.",
        "",
    ])


def render_markdown(per_system, combined_g, combined_n, combined_assess, econ, include_test) -> str:
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    out = [
        "# Graduation Scorecard — Paper → Real Account (cost-aware)",
        f"_Generated {now}. Verdict on NET-of-cost numbers. Criteria: ≥{GRAD['min_sample']} trades, "
        f"net expectancy > $0, net PF ≥ {GRAD['min_profit_factor']}, max DD ≤ {GRAD['max_dd_pct']:.0%}, "
        f"and net run-rate > ${FIXED_MONTHLY_COST:.0f}/mo fixed overhead. "
        f"Commissions: Tradier ${COMMISSION_PER_CONTRACT['tradier']}/Alpaca ${COMMISSION_PER_CONTRACT['openclaw']}/"
        f"IBKR ${COMMISSION_PER_CONTRACT['guardrail']} per contract. "
        f"{'INCLUDING test records.' if include_test else 'Real trades only.'}_",
        "",
        f"## Combined — {'✅ READY TO GRADUATE' if combined_assess['ready'] else '⏳ KEEP PAPER TRADING'}",
        _section("All systems", combined_g, combined_n, REAL_ACCOUNT_SIZE, combined_assess),
        _economics_block(econ),
        "## Per system",
    ]
    for s in ('tradier', 'openclaw', 'guardrail'):
        g, nt, a = per_system[s]
        out.append(_section(s.capitalize(), g, nt, STARTING_CAPITAL[s], a))
    out.append("\n_Graduation hinges on NET expectancy + profit factor + bounded drawdown over a real "
               "sample AND out-earning the fixed monthly overhead — not gross paper P&L or raw win rate._")
    return "\n".join(out)


# ── Orchestration ────────────────────────────────────────────────────────────────
def build(include_test: bool = False):
    raw = {'tradier': parse_tradier(TRADIER_LOG),
           'openclaw': parse_openclaw(OPENCLAW_PENDING),
           'guardrail': parse_guardrail(GUARDRAIL_POS)}
    per_system, all_trades = {}, []
    for s, trades in raw.items():
        used = trades if include_test else [t for t in trades if not t['is_test']]
        g = compute_metrics(used, STARTING_CAPITAL[s], 'pnl')
        nt = compute_metrics(used, STARTING_CAPITAL[s], 'net_pnl')
        per_system[s] = (g, nt, assess_graduation(nt))
        all_trades += used
    combined_g = compute_metrics(all_trades, REAL_ACCOUNT_SIZE, 'pnl')
    combined_n = compute_metrics(all_trades, REAL_ACCOUNT_SIZE, 'net_pnl')
    econ = compute_economics(combined_n['total_pnl'], all_trades, FIXED_MONTHLY_COST,
                             REAL_ACCOUNT_SIZE, TARGET_FIXED_DRAG)
    combined_assess = assess_graduation(combined_n, econ=econ)
    excluded = {s: sum(1 for t in raw[s] if t['is_test']) for s in raw}
    return per_system, combined_g, combined_n, combined_assess, econ, excluded


def main():
    include_test = '--include-test' in sys.argv
    per_system, cg, cn, ca, econ, excluded = build(include_test)
    md = render_markdown(per_system, cg, cn, ca, econ, include_test)
    if '--stdout' in sys.argv:
        print(md)
    else:
        out_dir = Path(os.environ.get('SC_OUT_DIR',
                       _first_existing('/home/ubuntu/status', str(Path(__file__).parent))))
        (out_dir / 'GRADUATION_SCORECARD.md').write_text(md)
        (out_dir / 'graduation_scorecard.json').write_text(json.dumps({
            'generated_at': datetime.now().isoformat(),
            'combined_gross': cg, 'combined_net': cn, 'combined_ready': ca['ready'],
            'economics': econ, 'excluded_test_records': excluded,
            'per_system_net': {s: per_system[s][1] for s in per_system},
        }, default=str, indent=2))
        print(f"✅ Scorecard → {out_dir}/GRADUATION_SCORECARD.md")
        print(f"   Combined: {cn['n']} real trades, net ${cn['total_pnl']:,.0f}, "
              f"{'READY' if ca['ready'] else 'NOT READY'}; excluded {sum(excluded.values())} test records")
    if '--telegram' in sys.argv:
        _send_telegram(cn, ca, econ)


def _send_telegram(cn, ca, econ):
    import urllib.request, urllib.parse
    token, chat = os.environ.get('TELEGRAM_BOT_TOKEN', ''), os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat:
        print("⚠️  Telegram creds missing — skipping send"); return
    status = '✅ READY' if ca['ready'] else '⏳ KEEP PAPER TRADING'
    rr = econ['net_monthly_runrate']
    msg = (f"📊 Graduation Scorecard — {status}\n"
           f"Net: {cn['n']} trades, exp ${cn['expectancy']}, PF {_fmt_pf(cn['profit_factor'])}, "
           f"P&L ${cn['total_pnl']:,.0f}\n"
           f"Run-rate {('$'+format(rr,',.0f')+'/mo') if rr is not None else '—'} vs "
           f"${econ['fixed_monthly']:.0f}/mo fixed → {'covers' if econ['covers_fixed'] else 'short'}")
    try:
        data = urllib.parse.urlencode({'chat_id': chat, 'text': msg}).encode()
        urllib.request.urlopen(f'https://api.telegram.org/bot{token}/sendMessage', data=data, timeout=10)
        print("✅ Telegram sent")
    except Exception as e:
        print(f"⚠️  Telegram send failed: {e}")


if __name__ == '__main__':
    main()
