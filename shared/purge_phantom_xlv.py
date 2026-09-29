#!/usr/bin/env python3
"""
purge_phantom_xlv.py — Purges un-filled/expired XLV paper order from active_trades.json
"""

import json
from pathlib import Path

def purge():
    p = Path("/home/ubuntu/shared/active_trades.json")
    if not p.exists():
        p = Path(__file__).parent / "active_trades.json"
    
    if not p.exists():
        print("  ℹ️ active_trades.json not found.")
        return

    data = json.loads(p.read_text(encoding="utf-8"))
    modified = False
    for acct, info in data.get("accounts", {}).items():
        positions = info.get("positions", [])
        clean_pos = [pos for pos in positions if pos.get("symbol") != "XLV"]
        if len(clean_pos) != len(positions):
            info["positions"] = clean_pos
            modified = True
            print(f"  🧹 Removed phantom XLV trade from {acct.upper()}!")

    if modified:
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print("  ✅ Purged phantom XLV trade from active_trades.json successfully!")
    else:
        print("  ✅ No phantom XLV trades found in active_trades.json.")

if __name__ == "__main__":
    purge()
