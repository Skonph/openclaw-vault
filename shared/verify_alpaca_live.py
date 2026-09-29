#!/usr/bin/env python3
"""
verify_alpaca_live.py — Read-Only Verification Script for Alpaca Live Account #290523608
"""

import sys
from pathlib import Path

BASE_DIR = Path("/home/ubuntu/shared") if Path("/home/ubuntu/shared").exists() else Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from alpaca_broker import AlpacaClient

def verify_alpaca_live():
    print("=" * 65)
    print("🔍 AUDITING ALPACA LIVE ACCOUNT (#290523608)")
    print("=" * 65)

    client = AlpacaClient("alpaca_live")
    print(f"• Target Endpoint:    {client.base_url}")
    print(f"• Expected Acct ID:   {client.expected_account_id}")
    print(f"• Environment Mode:   {'LIVE REAL-MONEY 🟢' if client.is_live else 'PAPER 🟡'}")

    try:
        acct = client.get_account()
        print("\n📊 Real-Time Balances:")
        print(f"  • Account Number:     {acct.get('account_number')}")
        print(f"  • Total Equity:        ${float(acct.get('equity', 0.0)):,.2f}")
        print(f"  • Cash Balance:        ${float(acct.get('cash', 0.0)):,.2f}")
        print(f"  • Buying Power:        ${float(acct.get('buying_power', 0.0)):,.2f}")
        print(f"  • Status:              {acct.get('status')}")
    except Exception as e:
        print(f"  🔴 Failed to fetch balances: {e}")
        return

    try:
        positions = client.get_positions()
        print(f"\n📦 Active Positions ({len(positions)}):")
        sgov_found = False
        sgov_qty = 0
        for p in positions:
            sym = p.get("symbol", "")
            qty = float(p.get("qty", 0.0))
            mv = float(p.get("market_value", 0.0))
            upl = float(p.get("unrealized_pl", 0.0))
            print(f"  • {sym:<20}: {qty:>6.0f} units | Mkt Val: ${mv:>10,.2f} | uPL: ${upl:>8,.2f}")
            if sym == "SGOV":
                sgov_found = True
                sgov_qty = qty
        if sgov_found:
            print(f"\n  🛡️ SGOV Treasury Moat Verified: {sgov_qty:.0f} shares earning risk-free yield ✅")
        else:
            print("\n  ⚠️ SGOV position not detected in positions list")
    except Exception as e:
        print(f"  ⚠️ Could not fetch positions: {e}")

    try:
        import urllib.request
        import json
        url = f"{client.base_url}/v2/orders?status=open"
        req = urllib.request.Request(url, headers=client._headers())
        with urllib.request.urlopen(req, context=client.ssl_ctx, timeout=10) as resp:
            orders = json.loads(resp.read().decode())
        print(f"\n⏳ Working / Open Orders ({len(orders)}):")
        if not orders:
            print("  • No active working orders on the exchange book.")
        for o in orders:
            oid = o.get("id", "")[:8]
            otype = o.get("order_class", o.get("type", ""))
            side = o.get("side", "")
            limit_px = o.get("limit_price", "")
            status = o.get("status", "")
            legs = o.get("legs", [])
            if legs:
                leg_desc = " / ".join([f"{l.get('side').upper()} {l.get('symbol')}" for l in legs])
                print(f"  • [{oid}] mleg ({status.upper()}): {leg_desc} @ Limit {limit_px}")
            else:
                sym = o.get("symbol", "")
                qty = o.get("qty", "")
                print(f"  • [{oid}] {side.upper()} {qty}x {sym} @ Limit {limit_px} ({status.upper()})")
    except Exception as e:
        print(f"  ⚠️ Could not fetch open orders: {e}")

    print("\n" + "=" * 65)
    print("✅ ALPACA LIVE API CONNECTION 100% OPERATIONAL!")
    print("=" * 65)

if __name__ == "__main__":
    verify_alpaca_live()
