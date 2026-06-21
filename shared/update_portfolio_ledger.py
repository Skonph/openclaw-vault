#!/usr/bin/env python3
"""
update_portfolio_ledger.py
--------------------------
Consolidates active positions and risk metrics from OpenClaw (Alpaca),
Tradier, and IBKR Guardrail systems into a unified ledger file:
/home/ubuntu/shared/active_portfolio_ledger.json
"""

import os
import json
import datetime
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
OPENCLAW_RISK_PATH  = Path("/home/ubuntu/openclaw/open_risk_ledger.json")
TRADIER_ACTIVE_PATH = Path("/home/ubuntu/trading-bot/active_trades.json")
IBKR_POSITIONS_PATH = Path("/home/ubuntu/guardrail/data/session_positions.json")
OUTPUT_PATH         = Path("/home/ubuntu/shared/active_portfolio_ledger.json")

def parse_occ_strike(occ: str) -> float:
    """Parse option strike price from a standard 21-char OCC symbol."""
    if not occ or len(occ) < 8:
        return 0.0
    try:
        strike_str = occ[-8:]
        return float(strike_str) / 1000.0
    except Exception:
        return 0.0

def load_openclaw_positions() -> list:
    """Load open positions from OpenClaw's risk ledger."""
    positions = []
    if OPENCLAW_RISK_PATH.exists():
        try:
            data = json.loads(OPENCLAW_RISK_PATH.read_text())
            for underlying, info in data.items():
                positions.append({
                    "system":       "openclaw",
                    "symbol":       underlying,
                    "direction":    info.get("direction", "unknown"),
                    "qty":          info.get("qty", 1),
                    "max_risk_usd": float(info.get("max_loss", 0.0)),
                    "opened_at":    info.get("opened_at", "")
                })
        except Exception as e:
            print(f"[WARN] Error loading OpenClaw risk ledger: {e}")
    return positions

def load_tradier_positions() -> list:
    """Load active positions from Tradier's active_trades.json."""
    positions = []
    if TRADIER_ACTIVE_PATH.exists():
        try:
            data = json.loads(TRADIER_ACTIVE_PATH.read_text())
            for record in data:
                strategy = record.get("strategy", "")
                direction = "unknown"
                if "Bull" in strategy:
                    direction = "bull"
                elif "Bear" in strategy:
                    direction = "bear"
                elif "Iron Condor" in strategy:
                    direction = "neutral"

                # Parse strikes to compute max loss
                max_risk = 0.0
                qty = int(record.get("quantity", 1))
                credit = float(record.get("entry_credit", 0.0))

                if strategy == "Iron Condor":
                    short_put = record.get("put_short_symbol", "")
                    long_put = record.get("put_long_symbol", "")
                    width = abs(parse_occ_strike(short_put) - parse_occ_strike(long_put))
                    max_risk = max(0.0, width - credit) * 100.0 * qty
                else:
                    short = record.get("short_symbol", "")
                    long = record.get("long_symbol", "")
                    width = abs(parse_occ_strike(short) - parse_occ_strike(long))
                    max_risk = max(0.0, width - credit) * 100.0 * qty

                positions.append({
                    "system":       "tradier",
                    "symbol":       record.get("symbol", ""),
                    "direction":    direction,
                    "qty":          qty,
                    "max_risk_usd": round(max_risk, 2),
                    "opened_at":    record.get("entered_at", "")
                })
        except Exception as e:
            print(f"[WARN] Error loading Tradier active trades: {e}")
    return positions

def load_ibkr_positions() -> list:
    """Load open positions from IBKR's session_positions.json."""
    positions = []
    if IBKR_POSITIONS_PATH.exists():
        try:
            data = json.loads(IBKR_POSITIONS_PATH.read_text())
            for pos in data:
                if pos.get("status") == "OPEN":
                    structure = pos.get("structure", "")
                    direction = "unknown"
                    if "BULL" in structure:
                        direction = "bull"
                    elif "BEAR" in structure:
                        direction = "bear"
                    elif "CONDOR" in structure:
                        direction = "neutral"

                    positions.append({
                        "system":       "ibkr",
                        "symbol":       pos.get("symbol", ""),
                        "direction":    direction,
                        "qty":          int(pos.get("qty", 1)),
                        "max_risk_usd": float(pos.get("max_loss_usd", 0.0)),
                        "opened_at":    pos.get("opened_at", "")
                    })
        except Exception as e:
            print(f"[WARN] Error loading IBKR positions: {e}")
    return positions

def main():
    print("=" * 60)
    print("  update_portfolio_ledger.py — compiling active positions")
    print("=" * 60)

    all_positions = []
    all_positions.extend(load_openclaw_positions())
    all_positions.extend(load_tradier_positions())
    all_positions.extend(load_ibkr_positions())

    print(f"Loaded {len(all_positions)} total active position(s).")
    for p in all_positions:
        print(f" - [{p['system'].upper()}] {p['symbol']} ({p['direction']}) "
              f"qty={p['qty']} risk=${p['max_risk_usd']}")

    # Write output JSON
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(all_positions, indent=2))
    print(f"\n✅ Unified portfolio ledger updated: {OUTPUT_PATH}")

if __name__ == "__main__":
    main()
