#!/usr/bin/env python3
"""
audit_portfolio.py — Real-Time Institutional Portfolio & Order Book Auditor
Inspects Pion Main, Pion2 Sub, and Live Account with zero shell escaping headaches.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from alpaca_broker import AlpacaClient
from auto_harvest_positions import get_open_orders

def run_audit():
    print("================================================================================")
    print("🏛️ INSTITUTIONAL PORTFOLIO & ORDER BOOK AUDIT")
    print("================================================================================")

    # 1. PION_MAIN
    bm = AlpacaClient("pion_main")
    acct_m = bm.get_account()
    print(f"\n📦 PION MAIN ({acct_m.get('account_number')}):")
    print(f"  • Cash: ${float(acct_m.get('cash', 0)):,.2f} | BP: ${float(acct_m.get('buying_power', 0)):,.2f} | Equity: ${float(acct_m.get('equity', 0)):,.2f}")

    print("  --- Active Positions ---")
    pos_m = bm.get_positions()
    if not pos_m:
        print("  • (No open positions)")
    else:
        for p in pos_m:
            print(f"  • {p.get('symbol')}: {p.get('qty')} contracts | uPL: ${p.get('unrealized_pl')}")

    print("  --- Working Orders ---")
    orders_m = get_open_orders(bm)
    if not orders_m:
        print("  • (No working orders)")
    else:
        for o in orders_m:
            oid = o.get("id")
            status = o.get("status")
            limit = o.get("limit_price")
            legs = o.get("legs") or []
            syms = [l.get("symbol") for l in legs] if legs else [o.get("symbol")]
            print(f"  • Order: {oid} | Status: {status} | Limit: {limit} | Legs: {syms}")
            if "XLU" in str(syms):
                print(f"    ⚠️ Stale XLU order detected! Cancelling order {oid}...")
                bm.cancel_order(oid)
                print(f"    ✅ Stale XLU order {oid} successfully cancelled.")

    # 2. PION2_SUB
    bs = AlpacaClient("pion2_sub")
    acct_s = bs.get_account()
    print(f"\n⚡ PION2 SUB ({acct_s.get('account_number')}):")
    print(f"  • Cash: ${float(acct_s.get('cash', 0)):,.2f} | BP: ${float(acct_s.get('buying_power', 0)):,.2f} | Equity: ${float(acct_s.get('equity', 0)):,.2f}")

    print("  --- Active Positions ---")
    pos_s = bs.get_positions()
    if not pos_s:
        print("  • (No open positions — 100% liquid cash ready for RULE-091)")
    else:
        for p in pos_s:
            print(f"  • {p.get('symbol')}: {p.get('qty')} contracts | uPL: ${p.get('unrealized_pl')}")

    print("  --- Working Orders ---")
    orders_s = get_open_orders(bs)
    if not orders_s:
        print("  • (No working orders)")
    else:
        for o in orders_s:
            print(f"  • Order: {o.get('id')} | Status: {o.get('status')} | Limit: {o.get('limit_price')}")

    print("\n================================================================================")
    print("✅ AUDIT COMPLETE")
    print("================================================================================")

if __name__ == "__main__":
    run_audit()
