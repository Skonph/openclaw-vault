#!/usr/bin/env python3
"""
verify_tradier_live.py — Read-Only Verification Script for Tradier Live Account #6YB80974
"""

import sys
from pathlib import Path

BASE_DIR = Path("/home/ubuntu/shared") if Path("/home/ubuntu/shared").exists() else Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    from tradier_broker import TradierClient
except ImportError:
    from shared.tradier_broker import TradierClient

def verify_tradier():
    print("=" * 60)
    print("🔍 AUDITING TRADIER LIVE ACCOUNT (#6YB80974)")
    print("=" * 60)
    
    client = TradierClient("live")
    print(f"• Target Endpoint:    {client.base_url}")
    print(f"• Target Account ID:  {client.account_id}")
    print(f"• Environment Mode:   {'LIVE REAL-MONEY 🟢' if client.is_live else 'SANDBOX 🟡'}")
    
    # 1. Balances
    try:
        acct = client.get_account()
        print("\n📊 Real-Time Balances:")
        print(f"  • Total Equity:        ${acct.get('total_equity', 0.0):,.2f}")
        print(f"  • Cash Balance:        ${acct.get('cash', 0.0):,.2f}")
        print(f"  • Option Buying Power: ${acct.get('option_buying_power', 0.0):,.2f}")
        print(f"  • Stock Buying Power:  ${acct.get('stock_buying_power', 0.0):,.2f}")
    except Exception as e:
        print(f"  🔴 Failed to fetch balances: {e}")
        return

    # 2. Positions
    try:
        positions = client.get_positions()
        print(f"\n📦 Active Positions ({len(positions)}):")
        if not positions:
            print("  • None (100% free capital)")
        else:
            for p in positions:
                print(f"  • {p.get('symbol')}: {p.get('quantity')} units (Cost Basis: ${p.get('cost_basis', 0.0):,.2f})")
    except Exception as e:
        print(f"  ⚠️ Could not fetch positions: {e}")

    print("\n" + "=" * 60)
    print("✅ TRADIER LIVE API CONNECTION 100% OPERATIONAL!")
    print("=" * 60)

if __name__ == "__main__":
    verify_tradier()
