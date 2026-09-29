#!/usr/bin/env python3
"""
pre_session_sanity_check.py — Sunday Pre-Session Sanity Check (Anna/Hermes pre-flight)

1. GREEKS / VEGA EXPOSURE: live Alpaca positions -> Tradier PROD greeks -> net delta/vega/theta
   per spread + portfolio, with tail-risk distro flags (% of equity at risk per 1 vol pt).
2. CITADEL STRESS TEST: runs the 5-scenario defined-risk stress engine on every open position
   and reports PASS/FAIL + capped shock loss per $5 width.
3. SNOWBALL CADENCE: equity-tier contract scaling (max(1, int(equity//2000))), 1.5-day turnover
   feasibility, 50% TP targets, and next-session (Express window) alignment.

Output: console report + JSON at ~/shared/pre_session_sanity_<date>.json
"""
import sys, os, json, ssl, re, math, datetime, urllib.request, urllib.parse
from pathlib import Path

SHARED = Path('/home/ubuntu/shared')
sys.path.insert(0, str(SHARED))
sys.path.insert(0, str(SHARED / 'archive' / 'legacy_scripts'))

from alpaca_broker import AlpacaClient          # noqa: E402
from citadel_stress_tester import run_citadel_stress_test  # noqa: E402

# ---------------------------------------------------------------- env / clients
def load_env():
    out = {}
    p = Path('/home/ubuntu/openclaw/.env')
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out

ENV = load_env()
TOKEN = ENV.get('TRADIER_PROD_TOKEN')
CTX = ssl._create_unverified_context()

def tradier(path, **params):
    url = 'https://api.tradier.com/v1' + path + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        'Authorization': 'Bearer ' + (TOKEN or ''),
        'Accept': 'application/json'})
    with urllib.request.urlopen(req, context=CTX, timeout=25) as r:
        return json.loads(r.read().decode())

def quote(syms):
    d = tradier('/markets/quotes', symbols=','.join(syms))
    q = d.get('quotes', {}).get('quote', [])
    if isinstance(q, dict):
        q = [q]
    return {x['symbol']: x for x in q}

def chain(symbol, expiration):
    d = tradier('/markets/options/chains', symbol=symbol, expiration=expiration, greeks='true')
    o = (d.get('options') or {}).get('option', [])
    if isinstance(o, dict):
        o = [o]
    return o

def history(symbol, days=45):
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days)
    d = tradier('/markets/history', symbol=symbol, interval='daily',
                start=str(start), end=str(end))
    return (d.get('history') or {}).get('day', []) or []

def hv20(symbol):
    """20-day annualised realised vol from Tradier daily closes."""
    try:
        rows = history(symbol)
        closes = [float(r['close']) for r in rows][-21:]
        if len(closes) < 6:
            return None
        rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        m = sum(rets) / len(rets)
        var = sum((x - m) ** 2 for x in rets) / (len(rets) - 1)
        return math.sqrt(var) * math.sqrt(252) * 100.0
    except Exception:
        return None

OCC = re.compile(r'^([A-Z\.]+?)(\d{6})([CP])(\d{8})$')

def parse_occ(sym):
    m = OCC.match(sym)
    if not m:
        return None
    root, ymd, right, strike = m.groups()
    return {'root': root, 'exp': '20%s-%s-%s' % (ymd[:2], ymd[2:4], ymd[4:6]),
            'right': right, 'strike': int(strike) / 1000.0}

# ---------------------------------------------------------------- 1. positions
POSITIONS = []
for acct in ('pion_main', 'pion2_sub'):
    try:
        cli = AlpacaClient(acct)
        acct_info = cli.get_account()
        for p in cli.get_positions():
            p['_account'] = acct
            POSITIONS.append(p)
        globals().setdefault('ACCOUNTS', {})[acct] = {
            'equity': float(acct_info.get('equity', 0)),
            'cash': float(acct_info.get('cash', 0)),
            'buying_power': float(acct_info.get('buying_power', 0)),
            'status': acct_info.get('status'),
        }
    except Exception as e:
        globals().setdefault('ACCOUNTS', {})[acct] = {'error': str(e)}

ACCOUNTS = globals().get('ACCOUNTS', {})

# ---------------------------------------------------------------- greeks
chains_cache, hv_cache, quote_cache = {}, {}, {}
report = {'generated_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S ICT'),
          'accounts': ACCOUNTS, 'positions': [], 'tail_flags': [], 'citadel': [],
          'snowball': {}, 'errors': []}

underlyings = set()
for p in POSITIONS:
    info = parse_occ(p['symbol'])
    if not info:
        continue
    underlyings.add(info['root'])

try:
    quote_cache = quote(sorted(underlyings))
except Exception as e:
    report['errors'].append(f'quote fetch: {e}')

for u in sorted(underlyings):
    try:
        hv_cache[u] = hv20(u)
    except Exception as e:
        report['errors'].append(f'hv {u}: {e}')

leg_rows = []
for p in POSITIONS:
    info = parse_occ(p['symbol'])
    if not info:
        continue
    u, exp, right, strike = info['root'], info['exp'], info['right'], info['strike']
    key = (u, exp)
    if key not in chains_cache:
        try:
            chains_cache[key] = chain(u, exp)
        except Exception as e:
            report['errors'].append(f'chain {u} {exp}: {e}')
            chains_cache[key] = []
    g = next((c for c in chains_cache[key]
              if abs(float(c.get('strike', 0)) - strike) < 0.001
              and c.get('option_type', '').lower().startswith(right.lower()[0])), None)
    qty = float(p.get('qty', 0))
    spot = float((quote_cache.get(u) or {}).get('last') or (quote_cache.get(u) or {}).get('close') or 0)
    rows = {
        'account': p['_account'], 'symbol': p['symbol'], 'underlying': u, 'exp': exp,
        'right': 'PUT' if right == 'P' else 'CALL', 'strike': strike, 'qty': qty,
        'avg_entry': float(p.get('avg_entry_price', 0) or 0),
        'current_price': float(p.get('current_price', 0) or 0),
        'market_value': float(p.get('market_value', 0) or 0),
        'unrealized_pl': float(p.get('unrealized_pl', 0) or 0),
        'spot': spot,
        'iv': None, 'delta': None, 'gamma': None, 'theta': None, 'vega': None,
        'oi': None, 'volume': None,
    }
    if g:
        gk = g.get('greeks') or {}
        rows.update({
            'iv': float(g.get('mid_iv') or gk.get('mid_iv') or 0) * 100 or None,
            'delta': gk.get('delta'), 'gamma': gk.get('gamma'),
            'theta': gk.get('theta'), 'vega': gk.get('vega'),
            'oi': g.get('open_interest'), 'volume': g.get('volume'),
        })
    leg_rows.append(rows)

# aggregate per spread contract group (account + underlying + exp + right)
groups = {}
for r in leg_rows:
    k = (r['account'], r['underlying'], r['exp'], r['right'])
    groups.setdefault(k, []).append(r)

for k, legs in sorted(groups.items()):
    acct, u, exp, right = k
    net_delta = net_vega = net_theta = net_gamma = 0.0
    credits = 0.0
    mult = 100.0
    for L in legs:
        sign = L['qty']  # already negative for short
        if L['delta'] is not None:
            net_delta += L['qty'] * L['delta']
            net_gamma += L['qty'] * (L['gamma'] or 0)
            net_theta += L['qty'] * (L['theta'] or 0) * mult
            net_vega += L['qty'] * (L['vega'] or 0) * mult
        credits += -L['qty'] * L['avg_entry'] * mult
    strikes = sorted(L['strike'] for L in legs)
    width = round(strikes[-1] - strikes[0], 2) if len(strikes) > 1 else None
    spot = legs[0]['spot']
    ivs = [L['iv'] for L in legs if L['iv']]
    avg_iv = round(sum(ivs) / len(ivs), 2) if ivs else None
    hv = hv_cache.get(u)
    short_leg = max(legs, key=lambda L: L['strike']) if right == 'PUT' \
        else min(legs, key=lambda L: L['strike'])
    rec = {
        'account': acct, 'underlying': u, 'expiry': exp, 'type': right + ' spread',
        'legs': [{'strike': L['strike'], 'qty': L['qty'], 'iv': L['iv'],
                  'delta': L['delta'], 'vega': L['vega'], 'theta': L['theta'],
                  'oi': L['oi'], 'vol': L['volume']} for L in legs],
        'width': width, 'spot': spot,
        'short_strike': short_leg['strike'],
        'short_strike_delta': short_leg['delta'],
        'otm_buffer_pct': round((spot - short_leg['strike']) / spot * 100, 2) if spot else None,
        'net_delta_shares': round(net_delta, 1),
        'net_gamma': round(net_gamma, 3),
        'net_theta_usd_per_day': round(net_theta, 2),
        'net_vega_usd_per_volpt': round(net_vega, 2),
        'net_credit_received': round(credits, 2),
        'max_risk_usd': round((width or 0) * 100 * max(abs(L['qty']) for L in legs), 2),
        'avg_iv': avg_iv, 'hv20': round(hv, 2) if hv else None,
        'iv_hv_ratio': round(avg_iv / hv, 3) if (avg_iv and hv) else None,
        'unrealized_pl': round(sum(L['unrealized_pl'] for L in legs), 2),
    }
    eq = ACCOUNTS.get(acct, {}).get('equity') or 0
    rec['vega_pct_of_equity_per_volpt'] = round(abs(net_vega) / eq * 100, 3) if eq else None
    rec['delta_pct_of_equity'] = round(abs(net_delta) * spot / eq * 100, 2) if eq and spot else None
    report['positions'].append(rec)

# tail-risk flags
VEGA_ALERT = 0.50     # >0.5% of equity per vol pt = elevated
LEG_OI_FLOOR = 50     # thin OI on a short leg = illiquid tail
for rec in report['positions']:
    for L in rec['legs']:
        if L['qty'] < 0 and (L['oi'] or 0) < LEG_OI_FLOOR:
            report['tail_flags'].append(
                f"{rec['underlying']} {rec['expiry']} {L['strike']}{rec['type'][0]} short leg OI={L['oi']} < {LEG_OI_FLOOR}")
    if rec['vega_pct_of_equity_per_volpt'] and rec['vega_pct_of_equity_per_volpt'] > VEGA_ALERT:
        report['tail_flags'].append(
            f"{rec['account']}/{rec['underlying']}: vega {rec['vega_pct_of_equity_per_volpt']}% of equity per vol pt > {VEGA_ALERT}%")
    if rec['iv_hv_ratio'] and rec['iv_hv_ratio'] < 1.15:
        report['tail_flags'].append(
            f"{rec['underlying']}: IV/HV {rec['iv_hv_ratio']} < 1.15 VRP floor (thin edge)")

# ---------------------------------------------------------------- 2. Citadel
for rec in report['positions']:
    try:
        res = run_citadel_stress_test(rec['underlying'], rec['spot'],
                                      abs(rec['short_strike_delta'] or 0.15),
                                      rec['avg_iv'] or 20.0, rec['width'] or 5.0)
        worst = max(r['est_loss_pct'] for r in res['results'])
        report['citadel'].append({
            'underlying': rec['underlying'], 'expiry': rec['expiry'],
            'width': rec['width'], 'max_risk_usd': rec['max_risk_usd'],
            'all_passed': res['all_passed'], 'worst_shock_loss_pct_of_width': worst,
            'scenarios': [{'scenario': r['scenario'], 'est_loss_pct': r['est_loss_pct'],
                           'capped_loss_usd': r['capped_loss_usd'], 'passed': r['passed']}
                          for r in res['results']],
        })
    except Exception as e:
        report['errors'].append(f'citadel {rec["underlying"]}: {e}')

# ---------------------------------------------------------------- 3. Snowball
total_equity = sum(a.get('equity', 0) for a in ACCOUNTS.values() if 'equity' in a)
total_cash = sum(a.get('cash', 0) for a in ACCOUNTS.values() if 'cash' in a)
open_spreads = len(report['positions'])
deployed = sum(r['max_risk_usd'] for r in report['positions'])
tier_contracts = max(1, int(total_equity // 2000))
report['snowball'] = {
    'total_equity': round(total_equity, 2),
    'total_cash': round(total_cash, 2),
    'open_spreads': open_spreads,
    'deployed_max_risk_usd': round(deployed, 2),
    'deployed_pct_of_equity': round(deployed / total_equity * 100, 2) if total_equity else None,
    'tier_contract_target': tier_contracts,
    'tier_thresholds': {'1 contract': '<= $3,999', '2 contracts': '>= $4,000',
                        '3 contracts': '>= $6,000', '4 contracts': '>= $8,000'},
    'per_acct_tier_contracts': {a: max(1, int(v.get('equity', 0) // 2000))
                                for a, v in ACCOUNTS.items() if 'equity' in v},
    'turnover_note': 'Snowball rule: limit exit at 50% max profit on fill; recycle collateral in 1.5-2.0 days',
}

# ---------------------------------------------------------------- print
def line(c='-', n=78):
    print(c * n)

line('=')
print('PRE-SESSION SANITY CHECK — GREEKS / CITADEL / SNOWBALL')
print(report['generated_at'])
line('=')
print('\nACCOUNTS')
for a, v in ACCOUNTS.items():
    print(f"  {a:<11} equity=${v.get('equity', 0):>10,.2f}  cash=${v.get('cash', 0):>10,.2f}  "
          f"BP=${v.get('buying_power', 0):>10,.2f}  {v.get('status', v.get('error', ''))}")

print('\n[1] GREEKS / VEGA EXPOSURE PER SPREAD')
for r in report['positions']:
    print(f"  {r['account']}/{r['underlying']} {r['type']} {r['expiry']}  width=${r['width']}  "
          f"maxRisk=${r['max_risk_usd']:,.0f}  credit=${r['net_credit_received']:,.0f}")
    print(f"      spot=${r['spot']}  short={r['short_strike']} (delta {r['short_strike_delta']}, "
          f"{r['otm_buffer_pct']}% OTM)  IV={r['avg_iv']}  HV20={r['hv20']}  IV/HV={r['iv_hv_ratio']}")
    print(f"      netDelta={r['net_delta_shares']} sh ({r['delta_pct_of_equity']}% eq)  "
          f"theta=${r['net_theta_usd_per_day']}/day  vega=${r['net_vega_usd_per_volpt']}/volpt "
          f"({r['vega_pct_of_equity_per_volpt']}% eq)  uPL=${r['unrealized_pl']}")

print('\n[2] CITADEL STRESS TEST (5 scenarios, defined-risk capped)')
for c in report['citadel']:
    print(f"  {c['underlying']} {c['expiry']} width=${c['width']}  all_passed={c['all_passed']}  "
          f"worst shock = {c['worst_shock_loss_pct_of_width']}% of width "
          f"(${c['max_risk_usd'] * c['worst_shock_loss_pct_of_width'] / 100:,.2f} of ${c['max_risk_usd']:,.0f})")

print('\n[3] SNOWBALL CADENCE')
for k, v in report['snowball'].items():
    print(f"  {k}: {v}")

print('\nTAIL-RISK FLAGS')
print('  none' if not report['tail_flags'] else '')
for f in report['tail_flags']:
    print('  ! ' + f)

if report['errors']:
    print('\nERRORS / DATA GAPS')
    for e in report['errors']:
        print('  - ' + e)

out = SHARED / f"pre_session_sanity_{datetime.date.today().isoformat()}.json"
out.write_text(json.dumps(report, indent=2, default=str))
print(f"\nJSON -> {out}")
