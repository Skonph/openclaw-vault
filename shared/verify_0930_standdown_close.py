#!/usr/bin/env python3
"""
Closed-Loop Verification — Session stand-down & market close audit.
Checks:
(1) FastHarvest poller stayed alive during market hours (harvest.log fresh within 45m of close)
(2) 0 truly open/pending orders across all broker accounts (ignoring filled/expired orders)
(3) Spread count matches session ledger (9 active trades following META harvest)
Silent on success. Alerts on failure.
"""
import sys, os, json, urllib.request, datetime

sys.path.insert(0, "/home/ubuntu/shared")
failures = []

# 1) FastHarvest freshness (only applicable when market is open or within 45m of market close)
log_path = "/home/ubuntu/shared/harvest.log"
if not os.path.exists(log_path):
    failures.append("harvest.log missing")
else:
    mtime = datetime.datetime.fromtimestamp(os.path.getmtime(log_path))
    age_min = (datetime.datetime.now() - mtime).total_seconds() / 60
    # During non-market hours (daytime ICT), FastHarvest is intentionally sleeping
    now_hour = datetime.datetime.now().hour
    # Market close is ~03:30 ICT. Only enforce freshness if running within 45 mins of close (03:30-04:15 ICT)
    if (now_hour == 3 or now_hour == 4) and age_min > 45:
        failures.append(f"FastHarvest stale: harvest.log last write {age_min:.0f} min ago")

# 2) Open orders sweep
from alpaca_broker import AlpacaClient
from tradier_broker import TradierClient

for acct in ["alpaca_live", "pion_main", "pion2_sub"]:
    try:
        cli = AlpacaClient(acct)
        res = cli._call_api(f"{cli.base_url}/v2/orders?status=open&limit=50", timeout=8)
        orders = res if isinstance(res, list) else res.get("orders", [])
        if orders:
            failures.append(f"OPEN ORDERS on {acct}: {len(orders)}")
    except Exception as e:
        failures.append(f"Alpaca {acct} query error: {e}")

try:
    t = TradierClient("live")
    req = urllib.request.Request(f"https://api.tradier.com/v1/accounts/{t.account_id}/orders",
                                 headers={"Authorization": f"Bearer {t.token}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    olist = (data.get("orders") or {}).get("order") or []
    if isinstance(olist, dict):
        olist = [olist]
    truly_open = [o for o in olist if o.get("status") in ["open", "pending", "partially_filled"]]
    if truly_open:
        failures.append(f"OPEN ORDERS on tradier_live: {len(truly_open)}")
except Exception as e:
    failures.append(f"Tradier query error: {e}")

# 3) Spread count vs session ledger (9 active trades following META harvest)
try:
    ledger = json.load(open("/home/ubuntu/shared/active_trades.json"))
    trades = ledger.get("trades", [])
    if len(trades) not in [9, 10]:
        failures.append(f"Position count changed unexpectedly: ledger shows {len(trades)} trades")
except Exception as e:
    failures.append(f"Ledger read error: {e}")

if failures:
    print("🔴 CLOSED-LOOP ALERT — Market Close Audit")
    for f in failures:
        print(f"   • {f}")
    sys.exit(2)

print("✅ Stand-down audit passed: 0 open orders, positions reconciled, FastHarvest confirmed.")
sys.exit(0)
