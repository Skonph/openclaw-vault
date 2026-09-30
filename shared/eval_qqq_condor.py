import json, urllib.request
from shared.alpaca_broker import AlpacaClient

broker = AlpacaClient('alpaca_live')

symbols = [
    'QQQ261016P00700000', 'QQQ261016P00675000',
    'QQQ261016P00705000', 'QQQ261016P00680000',
    'QQQ261016C00770000', 'QQQ261016C00795000',
    'QQQ261016C00775000', 'QQQ261016C00800000',
    'QQQ261016C00780000', 'QQQ261016C00805000'
]

url = 'https://data.alpaca.markets/v1beta1/options/snapshots?symbols=' + ','.join(symbols)
req = urllib.request.Request(url, headers=broker._headers())
with urllib.request.urlopen(req, context=broker.ssl_ctx) as resp:
    data = json.loads(resp.read().decode())
    snaps = data.get('snapshots', {})

def get_quote(s):
    q = snaps.get(s, {}).get('latestQuote', {})
    g = snaps.get(s, {}).get('greeks', {})
    return q.get('bp', 0), q.get('ap', 0), g.get('delta', 0)

# Spot
q_url = 'https://data.alpaca.markets/v2/stocks/QQQ/quotes/latest'
q_req = urllib.request.Request(q_url, headers=broker._headers())
with urllib.request.urlopen(q_req, context=broker.ssl_ctx) as resp:
    q_data = json.loads(resp.read().decode())
    spot = (q_data['quote']['bp'] + q_data['quote']['ap']) / 2.0
print(f"QQQ LIVE SPOT: {spot:.2f}\n")

combos = [
    ("Conservative IC", "QQQ261016P00700000", "QQQ261016P00675000", "QQQ261016C00775000", "QQQ261016C00800000"),
    ("Balanced IC",     "QQQ261016P00705000", "QQQ261016P00680000", "QQQ261016C00775000", "QQQ261016C00800000"),
    ("High Credit IC",  "QQQ261016P00705000", "QQQ261016P00680000", "QQQ261016C00770000", "QQQ261016C00795000"),
    ("Ultra Safe IC",   "QQQ261016P00700000", "QQQ261016P00675000", "QQQ261016C00780000", "QQQ261016C00805000"),
]

for name, sp, lp, sc, lc in combos:
    sp_b, sp_a, sp_d = get_quote(sp)
    lp_b, lp_a, lp_d = get_quote(lp)
    sc_b, sc_a, sc_d = get_quote(sc)
    lc_b, lc_a, lc_d = get_quote(lc)

    put_nat = sp_b - lp_a
    put_mid = ((sp_b + sp_a)/2) - ((lp_b + lp_a)/2)
    call_nat = sc_b - lc_a
    call_mid = ((sc_b + sc_a)/2) - ((lc_b + lc_a)/2)

    total_nat = put_nat + call_nat
    total_mid = put_mid + call_mid
    roc_nat = (total_nat / 25.0) * 100
    roc_mid = (total_mid / 25.0) * 100

    sp_otm = (spot - float(sp[11:16])) / spot * 100
    sc_otm = (float(sc[11:16]) - spot) / spot * 100

    print(f"=== {name} ($25w) ===")
    print(f"  Strikes: Put {sp[11:16]}P/{lp[11:16]}P (-{sp_otm:.2f}% OTM, delta {sp_d:.2f}) | Call {sc[11:16]}C/{lc[11:16]}C (+{sc_otm:.2f}% OTM, delta {sc_d:.2f})")
    print(f"  Put Wing Credit : Nat {put_nat:.2f} | Mid {put_mid:.2f}")
    print(f"  Call Wing Credit: Nat {call_nat:.2f} | Mid {call_mid:.2f}")
    print(f"  TOTAL CONDOR    : Nat {total_nat:.2f} (ROC {roc_nat:.1f}%) | Mid {total_mid:.2f} (ROC {roc_mid:.1f}%)")
    print(f"  2C Cash Inflow  : Nat {total_nat*200:.2f} | Mid {total_mid*200:.2f}")
    print()
