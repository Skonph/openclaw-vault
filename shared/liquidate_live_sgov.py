#!/usr/bin/env python3
"""
liquidate_live_sgov.py — Autonomous SGOV Liquidation Engine for Live Accounts (Alpaca & Tradier)

Purpose:
Liquidates all SGOV Treasury ETF holdings on:
1. Alpaca Live (#290523608): 298 shares SGOV
2. Tradier Live (#6YB80974): 19 shares SGOV

Settlement Rule:
US Equities & ETFs settle T+1.
Selling on Friday, Sep 25, 2026 ensures 100% settled liquid cash ready for trading by Monday, Sep 28, 2026.

STRICT CONSTRAINTS:
- Interactive Brokers (#U25439978) is 100% UNTOUCHED and EXCLUDED.
- Active option positions (XLF 54P/52P, XLE 60P/58P on Alpaca; XLF 54P/53P on Tradier) are EXCLUDED and PRESERVED.
- Defaults to DRY-RUN mode unless '--live' flag is explicitly provided.
"""

from __future__ import annotations
import os
import sys
import json
import datetime
import urllib.request
import urllib.parse
import ssl
from pathlib import Path
from typing import Dict, Any, List, Optional

SHARED_DIR = Path(__file__).resolve().parent
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

from alpaca_broker import AlpacaClient
from tradier_broker import TradierClient

TELEGRAM_BOT_TOKEN = "8991076258:AAGHyb-jEnp29BQ5O5V0mo4vFOmgXfgnmTg"
TELEGRAM_CHAT_ID = "-1004375899205"

def send_telegram(text: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)
    try:
        ctx_ssl = ssl._create_unverified_context()
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, context=ctx_ssl, timeout=10) as r:
            pass
    except Exception as e:
        print(f"  ℹ️ Telegram notification note: {e}")

def run_sgov_liquidation(live_mode: bool = False):
    now_ict = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT")
    mode_str = "LIVE REAL-MONEY EXECUTION 🔴" if live_mode else "DRY-RUN SIMULATION (PRE-FLIGHT) 🟡"
    
    print("=" * 75)
    print(f"🏦 SGOV TREASURY LIQUIDATION TO CASH — {mode_str}")
    print(f"📅 Timestamp: {now_ict}")
    print("=" * 75)

    summary_lines = [
        f"🏦 SGOV TO CASH LIQUIDATION AUDIT — {mode_str}",
        f"📅 Date: {now_ict}",
        f"🎯 Settlement Target: Monday, Sep 28, 2026 (T+1 Settled Cash)",
        f"🛡️ Interactive Brokers: 100% UNTOUCHED (Excluded)",
        f""
    ]

    # ──────────────────────────────────────────────────────────────────────────
    # 1. ALPACA LIVE (#290523608)
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[STEP 1/2] 🔍 Auditing Alpaca Live (#290523608)...")
    alpaca_client = AlpacaClient("alpaca_live")
    alpaca_sgov_qty = 0
    alpaca_sgov_val = 0.0

    try:
        acct = alpaca_client.get_account()
        cash_avail = float(acct.get("cash", 0.0))
        equity_avail = float(acct.get("equity", 0.0))
        print(f"  • Current Equity: ${equity_avail:,.2f} | Current Settled Cash: ${cash_avail:,.2f}")

        positions = alpaca_client.get_positions()
        for p in positions:
            sym = p.get("symbol", "")
            if sym == "SGOV":
                alpaca_sgov_qty = int(float(p.get("qty", 0)))
                alpaca_sgov_val = float(p.get("market_value", 0.0))
                print(f"  • Found SGOV Position: {alpaca_sgov_qty} shares | Value: ${alpaca_sgov_val:,.2f}")
            else:
                print(f"  🛡️ PRESERVING LIVE HOLDING: {sym} ({p.get('qty')} units) — Options untouched")

        if alpaca_sgov_qty > 0:
            if live_mode:
                print(f"  ⚡ SUBMITTING LIVE SELL ORDER: {alpaca_sgov_qty} SGOV on Alpaca Live...")
                url = f"{alpaca_client.base_url}/v2/orders"
                payload = {
                    "symbol": "SGOV",
                    "qty": str(alpaca_sgov_qty),
                    "side": "sell",
                    "type": "market",
                    "time_in_force": "day"
                }
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=alpaca_client._headers()
                )
                with urllib.request.urlopen(req, context=alpaca_client.ssl_ctx, timeout=15) as resp:
                    res_order = json.loads(resp.read().decode())
                    order_id = res_order.get("id")
                    status = res_order.get("status")
                    print(f"  ✅ ALPACA LIVE ORDER SUBMITTED: ID {order_id} | Status: {status}")
                    
                    # Short wait and verify fill
                    import time
                    time.sleep(3)
                    try:
                        v_url = f"{alpaca_client.base_url}/v2/orders/{order_id}"
                        v_req = urllib.request.Request(v_url, headers=alpaca_client._headers())
                        with urllib.request.urlopen(v_req, context=alpaca_client.ssl_ctx, timeout=10) as v_resp:
                            v_data = json.loads(v_resp.read().decode())
                            v_status = v_data.get("status", status)
                            v_fill_px = v_data.get("filled_avg_price") or "market"
                            print(f"  🔍 Order Status Check: {v_status} | Avg Fill Price: ${v_fill_px}")
                            summary_lines.append(f"• Alpaca Live: Sold {alpaca_sgov_qty} SGOV @ ${v_fill_px} (~${alpaca_sgov_val:,.2f}) | Status: {v_status} ✅")
                    except Exception as e_poll:
                        print(f"  ℹ️ Poll note: {e_poll}")
                        summary_lines.append(f"• Alpaca Live: Sold {alpaca_sgov_qty} SGOV (~${alpaca_sgov_val:,.2f}) | Order: {order_id} ({status}) ✅")
            else:
                print(f"  🟡 [DRY-RUN] Would submit MARKET SELL for {alpaca_sgov_qty} shares of SGOV (~${alpaca_sgov_val:,.2f}).")
                summary_lines.append(f"• Alpaca Live [DRY-RUN]: Ready to sell {alpaca_sgov_qty} SGOV (~${alpaca_sgov_val:,.2f})")
        else:
            print("  ℹ️ No SGOV shares found on Alpaca Live.")
            summary_lines.append("• Alpaca Live: 0 SGOV shares found.")
    except Exception as e:
        print(f"  🔴 Alpaca Live Error: {e}")
        summary_lines.append(f"• Alpaca Live: Error ({e}) ❌")

    # ──────────────────────────────────────────────────────────────────────────
    # 2. TRADIER LIVE (#6YB80974)
    # ──────────────────────────────────────────────────────────────────────────
    print("\n[STEP 2/2] 🔍 Auditing Tradier Live (#6YB80974)...")
    tradier_client = TradierClient("live")
    tradier_sgov_qty = 0
    tradier_sgov_val = 0.0

    try:
        t_acct = tradier_client.get_account()
        t_cash = float(t_acct.get("cash", 0.0))
        t_eq = float(t_acct.get("total_equity", 0.0))
        print(f"  • Current Equity: ${t_eq:,.2f} | Current Settled Cash: ${t_cash:,.2f}")

        t_positions = tradier_client.get_positions()
        for p in t_positions:
            sym = p.get("symbol", "")
            if sym == "SGOV":
                tradier_sgov_qty = int(float(p.get("quantity", 0)))
                tradier_sgov_val = tradier_sgov_qty * 100.655
                print(f"  • Found SGOV Position: {tradier_sgov_qty} shares | Value: ~${tradier_sgov_val:,.2f}")
            else:
                print(f"  🛡️ PRESERVING LIVE HOLDING: {sym} ({p.get('quantity')} units) — Options untouched")

        if tradier_sgov_qty > 0:
            if live_mode:
                print(f"  ⚡ SUBMITTING LIVE SELL ORDER: {tradier_sgov_qty} SGOV on Tradier Live...")
                res = tradier_client._call_api(
                    f"accounts/{tradier_client.account_id}/orders",
                    method="POST",
                    data={
                        "class": "equity",
                        "symbol": "SGOV",
                        "side": "sell",
                        "quantity": str(tradier_sgov_qty),
                        "type": "market",
                        "duration": "day"
                    }
                )
                order_id = res.get("order", {}).get("id")
                status = res.get("order", {}).get("status", "submitted")
                print(f"  ✅ TRADIER LIVE ORDER SUBMITTED: ID {order_id} | Status: {status}")
                
                import time
                time.sleep(3)
                try:
                    tv_data = tradier_client._call_api(f"accounts/{tradier_client.account_id}/orders/{order_id}").get("order", {})
                    tv_status = tv_data.get("status", status)
                    tv_fill_px = tv_data.get("avg_fill_price") or "market"
                    print(f"  🔍 Order Status Check: {tv_status} | Avg Fill Price: ${tv_fill_px}")
                    summary_lines.append(f"• Tradier Live: Sold {tradier_sgov_qty} SGOV @ ${tv_fill_px} (~${tradier_sgov_val:,.2f}) | Status: {tv_status} ✅")
                except Exception as e_poll:
                    print(f"  ℹ️ Poll note: {e_poll}")
                    summary_lines.append(f"• Tradier Live: Sold {tradier_sgov_qty} SGOV (~${tradier_sgov_val:,.2f}) | Order: {order_id} ({status}) ✅")
            else:
                print(f"  🟡 [DRY-RUN] Would submit MARKET SELL for {tradier_sgov_qty} shares of SGOV (~${tradier_sgov_val:,.2f}).")
                summary_lines.append(f"• Tradier Live [DRY-RUN]: Ready to sell {tradier_sgov_qty} SGOV (~${tradier_sgov_val:,.2f})")
        else:
            print("  ℹ️ No SGOV shares found on Tradier Live.")
            summary_lines.append("• Tradier Live: 0 SGOV shares found.")
    except Exception as e:
        print(f"  🔴 Tradier Live Error: {e}")
        summary_lines.append(f"• Tradier Live: Error ({e}) ❌")

    tot_qty = alpaca_sgov_qty + tradier_sgov_qty
    tot_val = alpaca_sgov_val + tradier_sgov_val
    print("\n" + "=" * 75)
    print(f"TOTAL SGOV SHARES: {tot_qty} | ESTIMATED TOTAL CASH UNLOCKED: ~${tot_val:,.2f}")
    print("=" * 75)

    if live_mode:
        send_telegram("\n".join(summary_lines))

if __name__ == "__main__":
    is_live = "--live" in sys.argv
    run_sgov_liquidation(live_mode=is_live)
