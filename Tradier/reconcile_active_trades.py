#!/usr/bin/env python3
"""
reconcile_active_trades.py — One-shot active_trades.json repair tool.

Reads open positions from the Tradier sandbox, matches paired put/call legs
into spreads, and writes a properly-formatted active_trades.json so
position_monitor.py can start applying exit rules immediately.

Run once after any gap where active_trades.json fell out of sync:
    python3 reconcile_active_trades.py            # dry-run (prints what it would write)
    python3 reconcile_active_trades.py --write    # actually writes active_trades.json

This makes NO orders — read-only except for the JSON file write.
"""

import os
import sys
import json
import re
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# ─── Load .env ────────────────────────────────────────────────────────────────
for env_path in [Path(__file__).parent / '.env',
                 Path('/home/ubuntu/trading-bot/.env')]:
    if env_path.exists():
        load_dotenv(env_path)
        break

SANDBOX_TOKEN   = os.getenv("TRADIER_SANDBOX_TOKEN", "")
ACCOUNT_ID      = os.getenv("TRADIER_SANDBOX_ACCOUNT", "")
SANDBOX_URL     = "https://sandbox.tradier.com/v1"
SANDBOX_HEADERS = {
    "Authorization": f"Bearer {SANDBOX_TOKEN}",
    "Accept": "application/json",
}

WRITE_MODE = "--write" in sys.argv


def get_positions():
    url = f"{SANDBOX_URL}/accounts/{ACCOUNT_ID}/positions"
    r = requests.get(url, headers=SANDBOX_HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    positions = data.get("positions", {})
    if not positions or positions == "null":
        return []
    pos_list = positions.get("position", [])
    if isinstance(pos_list, dict):
        pos_list = [pos_list]
    return pos_list


def parse_occ(symbol):
    """
    Parse OCC option symbol: SPY260626P00700000
    Returns (underlying, expiry YYYY-MM-DD, type P/C, strike float)
    """
    m = re.match(r'^([A-Z]+)(\d{6})([PC])(\d{8})$', symbol)
    if not m:
        return None
    underlying = m.group(1)
    exp_raw    = m.group(2)
    opt_type   = m.group(3)
    strike     = int(m.group(4)) / 1000.0
    expiry     = f"20{exp_raw[:2]}-{exp_raw[2:4]}-{exp_raw[4:]}"
    return underlying, expiry, opt_type, strike


def match_spreads(positions):
    """
    Pair short + long legs of the same underlying/expiry/type into spreads.
    Returns list of spread dicts.
    """
    # Separate by underlying + expiry + type
    buckets = {}
    for p in positions:
        sym  = p.get("symbol", "")
        parsed = parse_occ(sym)
        if not parsed:
            print(f"  ⚠️  Skipping non-option or unrecognised symbol: {sym}")
            continue
        underlying, expiry, opt_type, strike = parsed
        qty  = float(p.get("quantity", 0))
        cost = float(p.get("cost_basis", 0))  # total cost basis (negative for short)
        key  = (underlying, expiry, opt_type)
        buckets.setdefault(key, []).append({
            "symbol": sym, "qty": qty, "cost_basis": cost,
            "strike": strike, "underlying": underlying,
            "expiry": expiry, "opt_type": opt_type,
        })

    spreads = []
    for key, legs in buckets.items():
        if len(legs) == 2:
            short_leg = next((l for l in legs if l["qty"] < 0), None)
            long_leg  = next((l for l in legs if l["qty"] > 0), None)
            if short_leg and long_leg:
                qty = int(abs(short_leg["qty"]))
                # cost_basis per contract (short leg is negative = premium received)
                short_cb = short_leg["cost_basis"] / abs(short_leg["qty"]) / 100
                long_cb  = long_leg["cost_basis"]  / abs(long_leg["qty"])  / 100
                net_credit = round(abs(short_cb) - long_cb, 4)

                if short_leg["opt_type"] == "P":
                    strategy = "Bull Put Spread"
                else:
                    strategy = "Bear Call Spread"

                spreads.append({
                    "underlying":  key[0],
                    "expiry":      key[1],
                    "opt_type":    key[2],
                    "strategy":    strategy,
                    "qty":         qty,
                    "short_leg":   short_leg,
                    "long_leg":    long_leg,
                    "net_credit":  net_credit,
                })
        elif len(legs) == 4:
            # Iron Condor (put spread + call spread share same expiry bucket if IC)
            # Split by type handled separately — shouldn't reach here normally
            print(f"  ℹ️  4 legs in bucket {key} — may be IC, handle manually")

    return spreads


def build_record(spread):
    entry_credit        = spread["net_credit"]
    profit_target_debit = round(entry_credit * 0.50, 4)   # 50% profit
    stop_loss_debit     = round(entry_credit * 2.00, 4)   # 2× stop

    record = {
        "trade_id":            f"reconciled-{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "strategy":            spread["strategy"],
        "symbol":              spread["underlying"],
        "expiration":          spread["expiry"],
        "entry_credit":        entry_credit,
        "profit_target_debit": profit_target_debit,
        "stop_loss_debit":     stop_loss_debit,
        "order_id":            "reconciled-manual",
        "entered_at":          datetime.now().isoformat(),
        "quantity":            spread["qty"],
        "short_symbol":        spread["short_leg"]["symbol"],
        "long_symbol":         spread["long_leg"]["symbol"],
        "_note":               "Reconstructed by reconcile_active_trades.py — verify entry_credit against original fill",
    }
    return record


def main():
    if not SANDBOX_TOKEN or not ACCOUNT_ID:
        print("❌ TRADIER_SANDBOX_TOKEN or TRADIER_SANDBOX_ACCOUNT not set in .env")
        sys.exit(1)

    print("🔍 Fetching sandbox positions...")
    positions = get_positions()
    if not positions:
        print("  ✅ No open positions — active_trades.json should be []")
        return

    print(f"  Found {len(positions)} position leg(s):")
    for p in positions:
        print(f"    {p.get('symbol')}  qty={p.get('quantity')}  cost_basis={p.get('cost_basis')}")

    print("\n📐 Matching into spreads...")
    spreads = match_spreads(positions)

    if not spreads:
        print("  ⚠️  Could not auto-match any spread pairs. Check for IC or unusual structure.")
        return

    records = []
    for s in spreads:
        rec = build_record(s)
        print(f"\n  ✅ {s['strategy']} — {s['underlying']} {s['short_leg']['symbol']} / {s['long_leg']['symbol']}")
        print(f"     qty={s['qty']}, entry_credit=${s['net_credit']:.4f}")
        print(f"     profit_target=${rec['profit_target_debit']:.4f}, stop_loss=${rec['stop_loss_debit']:.4f}")
        print(f"     expiry={s['expiry']}")
        records.append(rec)

    active_path = Path(__file__).parent / "active_trades.json"

    if WRITE_MODE:
        with open(active_path, "w") as f:
            json.dump(records, f, indent=2)
        print(f"\n✅ Written {len(records)} record(s) → {active_path}")
        print("   position_monitor.py will now track these on its next run.")
    else:
        print(f"\n📋 DRY RUN — would write to {active_path}:")
        print(json.dumps(records, indent=2))
        print("\n  Re-run with --write to apply.")


if __name__ == "__main__":
    main()
