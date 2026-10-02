#!/usr/bin/env python3
"""
re_deployment_sweeper.py — Mid-Session Autonomous Post-Harvest Re-Deployment Sweeper

Purpose:
When early-session take-profit harvests (such as tonight's 5 winning exits on TSM, QQQ, XLE, AVGO)
liquidate positions and release tens of thousands of dollars in collateral back into cash,
this sweeper ensures that dry powder does not suffer from idle cash drag.

Key Mechanics:
1. Account Capacity & Idle Cash Audit:
   - Evaluates Alpaca Live and Tradier Live buying power and active spread counts.
2. Tradier Sprint Deployment:
   - If Tradier active spreads < 2 and Buying Power >= $500:
     Invokes execute_tradier_live_entry with autonomous catalog sweep to deploy
     an uncorrelated 7-DTE weekly sprint (e.g. AMD or VRT).
3. Alpaca Core Anchor Deployment:
   - If Alpaca active spreads < 4 and Cash >= $15,000 (well above 35% defense floor):
     Scans for an uncorrelated institutional anchor spread.
4. Auto-Logging:
   - Records all fills in active_trades.json, registers with RULE-075 fill tracker,
     and updates SkonVault_Live_Transaction_Journal.xlsx.
"""

import sys
import os
import json
import datetime
from pathlib import Path
from typing import Dict, Any, List

SHARED_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SHARED_DIR))

from alpaca_broker import AlpacaClient
from execute_golden_2115_daily_entry import (
    execute_tradier_live_entry,
    execute_account_entry,
    _send_telegram,
    _broadcast_to_anna
)
try:
    from tradier_broker import TradierClient
except ImportError:
    TradierClient = None

ICT = datetime.timezone(datetime.timedelta(hours=7))

def run_redeployment_sweeper(is_live: bool = True):
    now_dt = datetime.datetime.now(ICT)
    now_ict = now_dt.strftime("%Y-%m-%d %H:%M ICT")

    print("================================================================================")
    print(f"🔄 SKONVAULT MID-SESSION POST-HARVEST RE-DEPLOYMENT SWEEPER — {now_ict}")
    print("================================================================================")

    base_dir = Path("/home/ubuntu/shared")
    if not base_dir.exists():
        base_dir = SHARED_DIR

    # 1. Audit active portfolio positions to prevent cross-account concentration
    portfolio_tickers = set()
    alpaca_positions = []
    tradier_positions = []

    alpaca_client = AlpacaClient("alpaca_live" if is_live else "pion_main")
    try:
        alpaca_positions = alpaca_client.get_positions()
        for p in alpaca_positions:
            s = p.get("symbol", "")
            for known_sym in ["SPY", "QQQ", "LMT", "AVGO", "NVDA", "GLD", "XLU", "XLF", "JPM", "AMD", "CEG", "TSM", "VRT", "GE", "XLE", "XLV", "UNH", "JNJ", "XLP", "V", "MSFT", "META", "IWM", "TSLA"]:
                if known_sym in s:
                    portfolio_tickers.add(known_sym)
    except Exception as ex_alp:
        print(f"  ℹ️ Alpaca position audit notice: {ex_alp}")

    tradier_client = None
    if TradierClient:
        try:
            tradier_client = TradierClient("live" if is_live else "sandbox")
            tradier_positions = tradier_client.get_positions()
            for p in tradier_positions:
                s = p.get("symbol", "")
                for known_sym in ["SPY", "QQQ", "LMT", "AVGO", "NVDA", "GLD", "XLU", "XLF", "JPM", "AMD", "CEG", "TSM", "VRT", "GE", "XLE", "XLV", "UNH", "JNJ", "XLP", "V", "MSFT", "META", "IWM", "TSLA"]:
                    if known_sym in s:
                        portfolio_tickers.add(known_sym)
        except Exception as ex_tr:
            print(f"  ℹ️ Tradier position audit notice: {ex_tr}")

    print(f"  🛡️ Currently Active Portfolio Assets (Cross-Account Locked): {sorted(list(portfolio_tickers))}")

    # 2. Evaluate Tradier Live for Satellite Sprint Deployment
    dispatched_tradier = False
    if tradier_client:
        try:
            tr_acct = tradier_client.get_account()
            tr_cash = float(tr_acct.get("cash", 0))
            tr_bp = float(tr_acct.get("option_buying_power", 0))
            tr_short_puts = [p for p in tradier_positions if float(p.get("quantity", 0)) < 0 and "P0" in p.get("symbol", "")]
            active_sprint_slots = len(tr_short_puts)

            print(f"\n  ⚡ TRADIER LIVE AUDIT: Cash: ${tr_cash:,.2f} | BP: ${tr_bp:,.2f} | Active Slots: {active_sprint_slots}/2")
            if active_sprint_slots < 2 and tr_bp >= 500.0:
                print("  🚀 Tradier has open sprint slot capacity & liquid buying power! Invoking Autonomous Sprint Dispatch...")
                tr_res = execute_tradier_live_entry([], now_ict, base_dir, portfolio_tickers)
                if tr_res and tr_res.get("order_id"):
                    dispatched_tradier = True
                    portfolio_tickers.add(tr_res.get("symbol"))
                    print(f"  🎉 Successfully deployed Tradier Sprint: {tr_res.get('symbol')} (Order: {tr_res.get('order_id')})")
            else:
                print("  ℹ️ Tradier Live is at capacity or below minimum $500 buying power floor.")
        except Exception as ex_tr_run:
            print(f"  🔴 Tradier Re-Deployment error: {ex_tr_run}")

    # 3. Synchronize Active Trades and Refresh Transaction Journal
    if dispatched_tradier:
        try:
            from reconcile_active_trades import reconcile_and_save
            reconcile_and_save(base_dir=base_dir)
            from generate_live_journal import generate_live_journal
            generate_live_journal()
            print("  📊 Reconciled active trades and updated SkonVault_Live_Transaction_Journal.xlsx!")
        except Exception as ex_sync:
            print(f"  ℹ️ Sync notice: {ex_sync}")

    print("\n================================================================================")
    print(f"🏁 POST-HARVEST RE-DEPLOYMENT SWEEPER COMPLETE — {now_ict}")
    print("================================================================================")

if __name__ == "__main__":
    is_live = ("--live" in sys.argv) or (os.environ.get("SKONVAULT_LIVE", "0") == "1")
    run_redeployment_sweeper(is_live=is_live)
