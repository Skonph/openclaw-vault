#!/usr/bin/env python3
"""
sync_openclaw_anna_memory.py — Publish the LIVE trading state to Anna / OpenClaw memory.

DATA CONTRACT (hard rule, added 2026-09-20): every number in this document is derived from live
broker/market data (position_book + live_spot). Hardcoded spot prices, fabricated "100% SAFE"
statuses and handwritten position lists are FORBIDDEN — on 2026-09-20 this script published
"XLU $45.80 vs $44P (+3.9% OTM, 100% SAFE)" while the real close was $41.10 and the short 44P was
7% IN THE MONEY with -$1,300 unrealised. That is how an ITM position gets reported as safe.

Writes: MEMORY.md, weekly-snapshot.md and today's dated snapshot in the OpenClaw memory dirs.
Historical dated files are NEVER overwritten.
"""

import datetime
import json
import shutil
import sys
from pathlib import Path

SHARED_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SHARED_DIR))

from position_book import build_book          # noqa: E402
from live_spot import get_spots                # noqa: E402

TARGET_EQUITY = 18042.37                       # graduation target (plan constant, not a price)
SCORECARD = SHARED_DIR / "graduation_scorecard.json"

OPENCLAW_DIRS = [
    Path("/home/ubuntu/openclaw"),
    Path("/home/ubuntu/.openclaw"),
    Path("/home/ubuntu/.openclaw/workspace"),
    Path("/home/ubuntu/.openclaw/workspace/memory"),
    Path("/home/ubuntu/shared"),
]


def _scorecard() -> dict:
    try:
        d = json.loads(SCORECARD.read_text())
        return d.get("fast_track_progress", {}) or {}
    except Exception:
        return {}


def _fmt_ts():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT")


def build_state_document() -> str:
    now_ict = _fmt_ts()
    book = build_book()
    totals = book["totals"]
    sc = _scorecard()
    idx = get_spots(["SPY", "QQQ", "IWM", "VIX"])

    def px(sym):
        p = idx.get(sym, {}).get("price")
        return f"${p:,.2f}" if p else "UNAVAILABLE"

    lines = []
    lines.append("# 📊 SKONVAULT LIVE GROUND TRUTH & OPERATING STATE")
    lines.append(f"*Synchronized: {now_ict} — source: LIVE broker + market data (no hardcoded prices)*")
    lines.append("")
    lines.append("## 💼 Dual-Account Live Broker Balances")
    lines.append(f"• Combined Broker Equity : ${totals['equity']:,.2f}")
    lines.append(f"• Total Cash (live)      : ${totals['cash']:,.2f} "
                 f"({totals['cash'] / TARGET_EQUITY * 100:.1f}% of ${TARGET_EQUITY:,.2f} graduation target)")
    lines.append(f"• Real Cash Gap to Goal  : ${max(0.0, TARGET_EQUITY - totals['cash']):,.2f}")
    lines.append(f"• Buying Power (total)   : ${totals['buying_power']:,.2f}")
    lines.append(f"• Deployed Max Risk      : ${totals['deployed_max_risk']:,.2f} "
                 f"({totals['deployed_max_risk'] / totals['equity'] * 100:.1f}% of equity)"
                 if totals["equity"] else "")
    for acct, a in book["accounts"].items():
        if "error" in a:
            lines.append(f"• {acct:<11}: ERROR — {a['error']}")
        else:
            lines.append(f"• {acct:<11}: ${a['cash']:,.2f} cash | ${a['equity']:,.2f} equity | "
                         f"${a['buying_power']:,.2f} buying power")
    lines.append(f"• Fast-Track Progress     : {sc.get('completed_trades', 'n/a')}/"
                 f"{sc.get('fast_track_goal', 10)} closed | win rate "
                 f"{sc.get('win_rate_pct', 'n/a')}% | graduation target "
                 f"{sc.get('projected_graduation_display', 'n/a')}")
    lines.append("")
    lines.append("## 🌡️ Live Index Snapshot (Tradier/Finnhub/Alpaca)")
    lines.append(f"• SPY {px('SPY')} | QQQ {px('QQQ')} | IWM {px('IWM')} | VIX {px('VIX')}")
    lines.append("")
    lines.append("## 🌾 Active Defined Spreads (LIVE marks, live spots)")
    if not book["spreads"]:
        lines.append("• No defined spreads open.")
    for s in book["spreads"]:
        lines.append(f"• {s['root']} ${s['short_strike']:.2f}P / ${s['long_strike']:.2f}P "
                     f"({s['contracts']}C | {s['account']}) — exp {s['expiry']} ({s['dte']} DTE)")
        lines.append(f"   ◦ Spot ${s['spot']} ({s['spot_source']}) vs short strike → {s['otm_pct']}% OTM")
        lines.append(f"   ◦ Credit ${s['credit_usd']:,.2f} | Mark ${s['mark_usd']:,.2f} | uPL ${s['unrealized_pl']:,.2f} | "
                     f"Max risk ${s['max_risk_usd']:,.2f} | 50% TP ${s['profit_target_50pct_usd']:,.2f}")
        lines.append(f"   ◦ 21-DTE exit deadline: {s['deadline_21dte']} | Flags: "
                     f"{', '.join(s['flags']) if s['flags'] else 'none'}")
        lines.append(f"   ◦ STATUS: {s['status']}")
    lines.append("")
    lines.append("## 🛡️ Tail-Hedge Protection Legs")
    if not book["hedges"]:
        lines.append("• None open.")
    for h in book["hedges"]:
        lines.append(f"• {h['root']} ${h['strike']:.0f}{h['right'][0]} {h['exp']} ({h['contracts']}C, "
                     f"{h['account']}) — cost ${h['cost_usd']:,.2f} | mark ${h['mark_usd']:,.2f} | "
                     f"{h['dte']} DTE | spot ${h['spot']}")
    lines.append("")
    lines.append("## ⚙️ Production Crontab (live)")
    lines.append("• 07:15 ICT daily_report.py | 07:35 run_anna_dispatcher.py (Anna morning brief)")
    lines.append("• 18:30 ICT Sun master_sunday_reset_daemon.py (Sunday pre-reset)")
    lines.append("• 19:35 ICT dynamic_universe_screener.py (LIVE chain selection)")
    lines.append("• 19:40 ICT dispatch_daily_premarket_brief.py (live SPY/VIX/positions)")
    lines.append("• 21:10 & 21:45 ICT run_uoa_scan.py | 21:15 execute_golden_2115_daily_entry.py")
    lines.append("• 21:55 ICT dispatch_peak_hour_brief.py | 20:30-03:30 auto_harvest_positions.py")
    lines.append("")
    lines.append("## ⚠️ Standing Data-Integrity Rules")
    lines.append("• Any position safety claim must show live spot + source + as-of timestamp.")
    lines.append("• NEVER publish a hardcoded spot, strike or '100% SAFE' status.")
    lines.append("• ITM short legs / 21-DTE gamma-zone flags override every 'coasting' narrative.")
    return "\n".join(l for l in lines if l is not None) + "\n"


def sync_openclaw_memory():
    print(f"🔄 SYNCING LIVE STATE TO OPENCLAW / ANNABEL MEMORY — {_fmt_ts()}")
    doc = build_state_document()
    today_md = f"{datetime.datetime.now().strftime('%Y-%m-%d')}.md"
    targets = ["MEMORY.md", "weekly-snapshot.md", today_md]      # never historical dated files

    for d in OPENCLAW_DIRS:
        if not d.exists():
            continue
        for fname in targets:
            fpath = d / fname
            try:
                fpath.write_text(doc, encoding="utf-8")
                print(f"  ✅ Updated: {fpath}")
            except Exception as e:
                print(f"  ⚠️ Skipped {fpath}: {e}")
        for json_name in ["active_trades.json", "market_context.json"]:
            src = SHARED_DIR / json_name
            if src.exists() and d.resolve() != SHARED_DIR.resolve():
                try:
                    shutil.copy2(src, d / json_name)
                except Exception:
                    pass

    try:
        import market_heartbeat_gate
        is_active, reason, meta = market_heartbeat_gate.evaluate_market_status()
        market_heartbeat_gate.sync_heartbeat_files(is_active, reason, meta)
    except Exception as ex:
        print(f"  ℹ️ Heartbeat gate notice: {ex}")

    print("🎉 Ground-truth memory now reflects LIVE broker data (no literals).")
    return doc


if __name__ == "__main__":
    sync_openclaw_memory()
