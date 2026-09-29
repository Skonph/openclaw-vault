#!/usr/bin/env python3
"""
Closed-Loop Verification - GLD Rollover Check
Checks if GLD Sep 10 bull put spread was rolled on Tue Sep 08.
Silent on success. Alerts if still open.
"""
import os, json, sys, urllib.request

HOME = os.path.expanduser("~")
SHARED = os.path.join(HOME, "shared")

env_path = os.path.join(SHARED, ".env")
KEY = ""
SECRET = ""
if os.path.exists(env_path):
    for line in open(env_path):
        line = line.strip()
        if line.startswith("PION_ALPACA_KEY") and "=" in line:
            KEY = line.split("=", 1)[1].strip().strip("'\"")
        elif line.startswith("PION_ALPACA_SECRET") and "=" in line:
            SECRET = line.split("=", 1)[1].strip().strip("'\"")

if not KEY or not SECRET:
    print("No Alpaca credentials found")
    sys.exit(2)

HEADERS = {"APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SECRET}

req = urllib.request.Request("https://paper-api.alpaca.markets/v2/positions", headers=HEADERS)
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        positions = json.loads(r.read())
except Exception as e:
    print(f"Failed to fetch positions: {e}")
    sys.exit(2)

gld_positions = [p for p in positions if "GLD" in p.get("symbol", "")]
if gld_positions:
    print("CLOSED-LOOP ALERT: GLD positions STILL OPEN")
    for p in gld_positions:
        sym = p["symbol"]
        qty = int(p["qty"])
        mv = float(p["market_value"])
        upnl = float(p["unrealized_pl"])
        print(f"   {sym}: qty {qty} | MV ${mv:.2f} | UPL ${upnl:.2f}")
    sys.exit(2)
else:
    sys.exit(0)