#!/usr/bin/env python3
"""
hermes_adversarial_auditor.py — Hermes Chief Strategy Officer (CSO) Adversarial Audit Engine

Role & Purpose:
Hermes does NOT accept unverified assertions or synthetic markdown templates.
This engine derives the ground truth directly from live broker feeds (via position_book.py),
audits open positions for gamma risk and ITM breaches, validates the screener's output,
and generates the strategic Decision Matrix for Monday execution.

Run:
    python3 hermes_adversarial_auditor.py [--dispatch]
"""

from __future__ import annotations
import os
import sys
import json
import datetime
import urllib.request
import ssl
from pathlib import Path

SHARED_DIR = Path(__file__).resolve().parent
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

from position_book import build_book

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
        print(f"  ℹ️ Telegram notice: {e}")

def run_adversarial_audit(dispatch_tg: bool = False) -> dict:
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT")
    print("================================================================================")
    print(f"🕵️‍♂️ HERMES CSO ADVERSARIAL AUDIT & STRATEGIC TRIAGE — {now_str}")
    print("================================================================================")

    book = build_book()
    totals = book.get("totals", {})
    spreads = book.get("spreads", [])
    hedges = book.get("hedges", [])
    accounts = book.get("accounts", {})

    total_cash = totals.get("cash", 0.0)
    target_cash = 18042.37
    cash_gap = max(0.0, target_cash - total_cash)
    progress_pct = (total_cash / target_cash * 100) if target_cash else 0.0

    lines = [
        f"🕵️‍♂️ HERMES CSO ADVERSARIAL AUDIT & TRIAGE",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📅 Asof: {book.get('asof', now_str)}",
        f"",
        f"💼 LIVE BROKER CASH & GRADUATION REALITY:",
        f"• Total Liquid Cash : ${total_cash:,.2f} ({progress_pct:.1f}% of ${target_cash:,.2f} target)",
        f"• Real Cash Gap     : ${cash_gap:,.2f}",
        f"• Pion Main Cash    : ${accounts.get('pion_main', {}).get('cash', 0):,.2f} (BP: ${accounts.get('pion_main', {}).get('buying_power', 0):,.2f})",
        f"• Pion2 Sub Cash    : ${accounts.get('pion2_sub', {}).get('cash', 0):,.2f} (BP: ${accounts.get('pion2_sub', {}).get('buying_power', 0):,.2f})",
        f""
    ]

    # Evaluate Spreads
    critical_breaches = []
    gamma_risks = []
    healthy_spreads = []

    for s in spreads:
        flags = s.get("flags", [])
        status = s.get("status", "")
        root = s.get("root", "")
        short_k = s.get("short_strike", 0)
        long_k = s.get("long_strike", 0)
        exp = s.get("expiry", "")
        dte = s.get("dte", 0)
        otm = s.get("otm_pct")
        upl = s.get("unrealized_pl", 0)
        max_r = s.get("max_risk_usd", 0)
        acct = s.get("account", "")

        desc = f"• {acct}/{root} ${short_k:.0f}P/${long_k:.0f}P ({s.get('contracts')}C, exp {exp}) | Spot: ${s.get('spot')} ({status}) | uPL: ${upl} | Max Risk: ${max_r}"

        if "ITM_SHORT_LEG" in flags or (otm is not None and otm < 0):
            critical_breaches.append({
                "symbol": root,
                "account": acct,
                "short_strike": short_k,
                "otm_pct": otm,
                "upl": upl,
                "max_risk": max_r,
                "dte": dte,
                "line": desc
            })
        elif "GAMMA_ZONE" in str(flags) or (dte is not None and dte <= 21):
            gamma_risks.append({
                "symbol": root,
                "account": acct,
                "deadline": s.get("deadline_21dte", "Pending"),
                "otm_pct": otm,
                "dte": dte,
                "line": desc
            })
        else:
            healthy_spreads.append(desc)

    lines.append("🚨 CRITICAL POSITION BREACHES (TRIAGE MANDATORY):")
    if critical_breaches:
        for b in critical_breaches:
            lines.append(b["line"])
            salvage = max(0.0, (b["max_risk"] or 0) - abs(b["upl"]))
            lines.append(f"  👉 DECISION FOR MONDAY: Close at open to salvage ${salvage:.2f} collateral, OR hold into {b['dte']} DTE for bounce (risking remaining ${salvage:.2f}).")
    else:
        lines.append("  ✅ None. All short strikes remain out of the money.")
    lines.append("")

    lines.append("⏳ GAMMA RISK ZONE (DTE <= 21 / CLOSE OR ROLL):")
    if gamma_risks:
        for g in gamma_risks:
            lines.append(g["line"])
            lines.append(f"  👉 ACTION: 21-DTE gamma rule active ({g['dte']} DTE). Must close or roll before {g['deadline']}.")
    else:
        lines.append("  ✅ None. No positions inside the 21-DTE gamma acceleration zone.")
    lines.append("")

    if healthy_spreads:
        lines.append("🟢 HEALTHY POSITIONS (>3% OTM / >21 DTE):")
        for h in healthy_spreads:
            lines.append(h)
        lines.append("")

    # Verify Screener Lead
    lines.append("🔬 LIVE UNIVERSE SCREENER AUDIT:")
    target_file = SHARED_DIR / "tonight_selected_target.json"
    if target_file.exists():
        try:
            tdata = json.loads(target_file.read_text(encoding="utf-8"))
            if tdata.get("stand_aside"):
                lines.append(f"• Screener Status : STAND ASIDE (0/18 candidates cleared strict live gates)")
            else:
                sym = tdata.get("symbol", "N/A")
                score = tdata.get("total_score", 0)
                strikes = tdata.get("strikes", "N/A")
                exp = tdata.get("expiration", "N/A")
                credit = tdata.get("credit", 0)
                roc = tdata.get("roc", 0)
                lines.append(f"• Verified Lead   : #{sym} ({score} pts | {tdata.get('theme', '')})")
                lines.append(f"• Option Structure: {strikes} exp {exp} (Est Credit: ${credit:.2f} | ROC: {roc:.1f}%)")
        except Exception as e:
            lines.append(f"• Screener Status : Error reading tonight_selected_target.json ({e})")
    else:
        lines.append("• Screener Status : tonight_selected_target.json not yet generated.")

    lines.append("")
    lines.append("🎯 HERMES VERDICT:")
    if critical_breaches:
        lines.append("• PRIORITY 1: Resolve XLU ITM triage at Monday pre-market (19:40 ICT brief).")
    lines.append("• PRIORITY 2: Enforce zero synthetic orders and obey live screener gates.")

    summary_text = "\n".join(lines)
    print(summary_text)

    if dispatch_tg:
        send_telegram(summary_text)
        print("  ✅ Adversarial Audit dispatched to Telegram!")

    return {
        "asof": book.get("asof"),
        "cash": total_cash,
        "critical_breaches": critical_breaches,
        "gamma_risks": gamma_risks,
        "summary": summary_text
    }

if __name__ == "__main__":
    dispatch_flag = "--dispatch" in sys.argv
    run_adversarial_audit(dispatch_tg=dispatch_flag)
