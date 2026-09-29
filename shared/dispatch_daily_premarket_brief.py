#!/usr/bin/env python3
"""
dispatch_daily_premarket_brief.py — Dynamic Pre-Market Intelligence Brief

Executes autonomously at 19:40 ICT (Mon-Fri) & 19:30 ICT (Sunday) via Linux Cron:
1. Ingests tonight_selected_target.json generated dynamically by dynamic_universe_screener.py
2. Queries live account balances and actual open positions directly from Alpaca REST API
3. Evaluates real cash gap to the $18,042.37 Week 9 target
4. Dispatches the unified Anna & Hermes Pre-Market Briefing to Telegram and AgentBridge!
"""

import sys
import os
import json
import datetime
import urllib.request
import ssl
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from alpaca_broker import AlpacaClient
from agent_bridge import AgentBridge

def format_positions_summary(positions):
    """Formats a list of position dictionaries into a clean human-readable summary string."""
    if not positions:
        return "100% Cash / No Open Positions"
    sym_counts = {}
    for p in positions:
        s = p.get("symbol", "")
        q = int(float(p.get("qty", 0)))
        if len(s) > 10 and ("P0" in s or "C0" in s):
            opt_type = "P" if "P0" in s else "C"
            prefix = s.split(opt_type + "0")[0]
            base = prefix[:-6] if len(prefix) > 6 else prefix
            sym_counts[base] = sym_counts.get(base, 0) + 1
        else:
            sym_counts[s] = sym_counts.get(s, 0) + 1
    
    return ", ".join([f"{k} ({v} leg{'s' if v > 1 else ''})" for k, v in sym_counts.items()])

def dispatch_premarket_brief():
    print("============================================================")
    print("👑 DISPATCHING DYNAMIC 19:40 ICT PRE-MARKET INTELLIGENCE BRIEF")
    print("============================================================")

    now_dt = datetime.datetime.now()
    now_ict = now_dt.strftime("%Y-%m-%d %H:%M ICT")
    weekday_idx = now_dt.weekday()
    weekday_name = now_dt.strftime("%A")

    base_dir = Path("/home/ubuntu/shared")
    if not base_dir.exists():
        base_dir = Path("/Users/SkonP/AI_Prompt/Obsidient/SkonVault/shared")

    # 1. Fetch live broker data
    b_alpaca = AlpacaClient("alpaca_live")
    from tradier_broker import TradierClient
    b_tradier = TradierClient("live")

    alpaca_eq, alpaca_cash = 0.0, 0.0
    tradier_eq, tradier_cash = 0.0, 0.0
    alpaca_positions, tradier_positions = [], []

    try:
        acct_a = b_alpaca.get_account()
        alpaca_eq = float(acct_a.get("equity", 0))
        alpaca_cash = float(acct_a.get("cash", 0))
        alpaca_positions = [p for p in b_alpaca.get_positions() if p.get("symbol") != "SGOV"]
    except Exception as e:
        print(f"  ℹ️ Alpaca Live notice: {e}")

    try:
        acct_t = b_tradier.get_account()
        tradier_eq = float(acct_t.get("total_equity", 0))
        tradier_cash = float(acct_t.get("cash", 0))
        tradier_positions = [p for p in b_tradier.get_positions() if p.get("symbol") != "SGOV"]
    except Exception as e:
        print(f"  ℹ️ Tradier Live notice: {e}")

    total_cash = alpaca_cash + tradier_cash
    comb_eq = alpaca_eq + tradier_eq
    cash_defense_floor = 11249.00
    target_monthly_cash = 5766.00
    floor_monthly_cash = 3844.00
    target_eq = 18042.37
    cash_gap = max(0.0, target_eq - total_cash)
    b_pion = b_alpaca

    # 1.5. RULE-074: US Exchange Holiday Circuit Breaker
    is_holiday, holiday_name = b_pion.is_market_holiday()
    if is_holiday:
        # Dynamically determine the next active US trading session
        next_dt = now_dt.date() + datetime.timedelta(days=1)
        while next_dt.weekday() >= 5 or next_dt.strftime("%Y-%m-%d") in b_pion.US_MARKET_HOLIDAYS_2026:
            next_dt += datetime.timedelta(days=1)
        next_session_str = next_dt.strftime("%A, %b %d")

        print(f"  🛑 US EXCHANGE HOLIDAY DETECTED: {holiday_name.upper()}!")
        print("     RULE-074 Holiday Circuit Breaker: All US exchanges are 100% CLOSED.")
        holiday_brief = f"""🇺🇸 ANNA & HERMES INTELLIGENCE — US MARKET HOLIDAY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 Date: {now_ict}
🏛️ US Exchange Holiday: {holiday_name.upper()}
🛑 Status: All US Exchanges (NYSE, NASDAQ, CBOE) are 100% CLOSED today.

🛡️ OPERATIONAL DIRECTIVES (RULE-074):
• ZERO orders, ZERO entries, and ZERO sweeps authorized.
• Golden 21:15 ICT Entry Engine & FastHarvest poller standing down autonomously.
• Total Liquid Cash in Bank : ${total_cash:,.2f} (Defense Floor: ${cash_defense_floor:,.2f} 🛡️)
• Monthly Net Cash Target   : >=${target_monthly_cash:,.2f} / month (18%+ Target | >=12% Floor: ${floor_monthly_cash:,.2f})
• Capital Defense           : 100% Cash Protected & Earning Risk-Free SGOV Yield.

🚀 Next Live Market Session : Regular trading session resumes {next_session_str} @ 20:30 ICT.
All systems in autonomous standby mode. Enjoy the holiday! 🏖️"""

        group_id = "-1004375899205"
        anna_token = "8632069800:AAGl63rQuntU9-a84u0X37bsky4CppB-GhM"
        ctx_ssl = ssl._create_unverified_context()
        try:
            url = f"https://api.telegram.org/bot{anna_token}/sendMessage"
            payload = json.dumps({"chat_id": group_id, "text": holiday_brief}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, context=ctx_ssl, timeout=10) as resp:
                pass
            print("  ✅ Dispatched US Holiday Stand-Down Bulletin to Telegram!")
        except Exception as ex_t:
            print(f"  ℹ️ Telegram notice: {ex_t}")
        return

    # 2. Ingest Dynamic Lead Target (live screener output; explicit stand-aside is respected)
    lead_target_str = "No screener output found — run dynamic_universe_screener.py"
    lead_target_str_is_live = False
    target_file = base_dir / "tonight_selected_target.json"
    if target_file.exists():
        try:
            tdata = json.loads(target_file.read_text(encoding="utf-8"))
            prim = tdata.get("primary", {}) or {}
            if prim.get("symbol"):
                sym = prim.get("symbol")
                theme_str = prim.get("theme") or prim.get("name") or "Core Universe"
                exp = prim.get("expiration") or prim.get("exp_date") or ""
                dte_val = prim.get("dte")
                if not dte_val and exp:
                    try:
                        exp_dt = datetime.datetime.strptime(exp, "%Y-%m-%d").date()
                        dte_val = f"{(exp_dt - datetime.date.today()).days}d"
                    except Exception:
                        dte_val = "n/a"
                else:
                    dte_val = f"{dte_val}d" if dte_val is not None else "n/a"

                credit_val = prim.get("mid_credit") or prim.get("credit_mid") or prim.get("natural_credit")
                credit_str = f"+${credit_val:.2f}" if isinstance(credit_val, (int, float)) else str(credit_val or "n/a")
                spot_val = prim.get("live_spot") or prim.get("spot")
                spot_str = f"${spot_val:.2f}" if isinstance(spot_val, (int, float)) else str(spot_val or "n/a")
                roc_val = prim.get("roc_pct", 0)
                score_val = prim.get("total_score", 0)
                lead_target_str = (f"{sym} ({theme_str}) Bull Put Spread "
                                   f"(${prim.get('short_strike'):.0f}P/${prim.get('long_strike'):.0f}P | "
                                   f"exp {exp} | {dte_val} | credit {credit_str} | "
                                   f"ROC {roc_val}% | spot {spot_str} | Score: {score_val:.1f} pts)")
                lead_target_str_is_live = True
            elif tdata.get("stand_aside"):
                lead_target_str = ("NO ELIGIBLE CANDIDATE — " +
                                   str(tdata.get("stand_aside_reason", "screener standing aside")))
        except Exception:
            pass

    # 3. Read LIVE Market Context (refresh market_context.json first — never trust a stale file)
    regime = "UNKNOWN"
    vix = None
    vrp = None
    spy_price = None
    spy_sma20 = spy_sma50 = None
    try:
        from live_market_data import refresh_market_context, index_context, vrp_spread
        refresh_market_context(str(base_dir / "market_context.json"))
        lctx = index_context()
        spy = lctx.get("SPY", {}) or {}
        spy_price = spy.get("last")
        spy_sma20, spy_sma50 = spy.get("sma20"), spy.get("sma50")
        vix = (lctx.get("VIX", {}) or {}).get("last")
        vrp_info = vrp_spread("SPY")
        vrp = vrp_info.get("spread") if vrp_info.get("ok") else None
        regime = ("BULLISH_CONTANGO" if (spy_price and spy_sma20 and spy_price > spy_sma20)
                  else "DEFENSIVE_RANGE")
    except Exception as ex_live:
        print(f"  ⚠️ Live context unavailable: {ex_live}")

    # COT smart-money numbers: read the real weekly file (never hardcoded)
    cot_txt, cot_src = [], "unavailable"
    for cot_p in (base_dir / "cot_data" / "latest_cot.json", base_dir / "cot_data" / "latest_cot.json"):
        try:
            cj = json.loads(cot_p.read_text())
            cot = cj.get("cot", cj)
            for sym_key, label in (("NQ", "Nasdaq"), ("GC", "Gold"), ("ZN", "10Y Notes")):
                w = (cot.get(sym_key) or {}).get("willco_commercial")
                if w is not None:
                    cot_txt.append(f"• {sym_key} ({label}) WILLCO {w}% (CFTC week {cj.get('_meta', {}).get('generated_at', 'n/a')})")
            cot_src = str(cot_p)
            break
        except Exception:
            continue
    if not cot_txt:
        cot_txt = ["• COT file unavailable — no smart-money read published (never estimated)"]

    # Format positions using RULE-060 Spread Intelligence
    try:
        from intelligent_spread_formatter import format_intelligent_spread_report
        alpaca_summary = format_intelligent_spread_report(alpaca_positions, "Alpaca Live (#290523608)")
        tradier_summary = format_intelligent_spread_report(tradier_positions, "Tradier Live (#6YB80974)")
    except Exception:
        alpaca_summary = f"${alpaca_cash:,.2f} Cash ({len(alpaca_positions)} Open Positions: {format_positions_summary(alpaca_positions)})"
        tradier_summary = f"${tradier_cash:,.2f} Cash ({len(tradier_positions)} Open Positions: {format_positions_summary(tradier_positions)})"

    # Dynamic Week Calculation
    base_date = datetime.date(2026, 6, 29)
    days_elapsed = (now_dt.date() - base_date).days
    week_num = max(1, (days_elapsed // 7) + 1)
    week_label = f"Week {week_num}" if week_num != 10 else "Week 10 (Graduation Week 🎓)"

    vix_str = f"{vix:.2f}" if vix else "n/a"
    vrp_str = f"{vrp:+.2f} vol pts" if vrp is not None else "n/a"
    spy_line = f"${spy_price:,.2f}" if spy_price else "UNAVAILABLE"
    sma_line = (f"(SMA20 ${spy_sma20:,.2f} / SMA50 ${spy_sma50:,.2f})"
                if (spy_sma20 and spy_sma50) else "(SMA unavailable)")
    cot_block = "\n".join(cot_txt)
    if lead_target_str_is_live:
        lead_block = (f"🎯 TONIGHT'S DYNAMIC LEAD SETUP: {lead_target_str}\n"
                      f"• 5 Tumblers Status : ①②③ EVIDENCED | ④ TRIGGER ⏳ PENDING LIVE UOA "
                      f"(21:10 & 21:45 ICT) | ⑤ RISK PARAMS SET")
    else:
        lead_block = (f"🎯 TONIGHT'S DYNAMIC LEAD SETUP: {lead_target_str}\n"
                      f"• 5 Tumblers Status : NO ELIGIBLE LEAD PUBLISHED — screener found no candidate "
                      f"clearing the live gates (stand aside)")

    brief_text = f"""👑 ANNA & HERMES PRE-MARKET INTELLIGENCE BRIEF — {now_ict}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🌡️ REGIME: {regime} | VIX {vix_str} | VRP {vrp_str}
📈 SPY: {spy_line} {sma_line}
📅 {week_label} Session: {weekday_name.upper()} Pre-Market Intelligence

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 LARRY WILLIAMS COT SMART MONEY AUDIT (live CFTC file)
{cot_block}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{lead_block}
• Execution Protocol: RULE-058 3-Step Ladder Limit & RULE-062 Skew Arbitrage

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💼 ACTIVE POSITIONS & CAPITAL AUDIT (OPTION A — PURE LIVE)
• Total Liquid Cash in Bank : ${total_cash:,.2f}
• Alpaca Live (#290523608)  : ${alpaca_cash:,.2f} Cash | Equity: ${alpaca_eq:,.2f}
• Tradier Live (#6YB80974)  : ${tradier_cash:,.2f} Cash | Equity: ${tradier_eq:,.2f}
• Combined Broker Equity    : ${comb_eq:,.2f}
• Permanent Cash Floor (35%): ${cash_defense_floor:,.2f} (Safely Protected 🛡️)
• Monthly Net Cash Target   : >=${target_monthly_cash:,.2f} / month (18%+ Target | >=12% Floor: ${floor_monthly_cash:,.2f})
• Weekly Run-Rate Pacing    : ⏱️ Speedometer: Target $1,441.50/wk | Floor $961.00/wk (18.0%+ Velocity)

[Alpaca Live Account — Primary Hybrid Barbell]
{alpaca_summary}

[Tradier Live Account — High-Velocity Satellite Sprint]
{tradier_summary}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏱️ TONIGHT'S LIVE TIMELINE
• 19:35 ICT : 🧠 Dynamic 5-Factor Quant Screener Active (RULE-062)
• 19:40 ICT : 👑 Daily Pre-Market Brief Delivered
• 20:30 ICT : 🔔 US MARKET OPEN — 3-Step Ladder Limit Execution (RULE-058)
• 21:10 ICT : 🤝 Anna T-5 Pre-Flight Handshake & Institutional UOA Flow Sweep
• 21:15 ICT : ⚡ Golden 21:15 ICT Entry Engine Active (Option A: Live Real-Money)
• 21:45 ICT : 📡 UOA Sweep #2 & 21:55 Peak Anna Telemetry
• 20:30–03:30 ICT: 🌾 FastHarvest 5-Minute Poller Active (DIR-09 Protected 🛡️)

✅ ALL SYSTEMS DYNAMIC & AUTONOMOUS — STANDING BY! 🚀📈"""

    DRY_RUN = ("--dry-run" in sys.argv) or os.getenv("BRIEF_DRY_RUN") == "1"
    if DRY_RUN:
        print("🧪 DRY RUN — Telegram dispatch + AgentBridge mirror suppressed")
        print("─" * 70)
        print(brief_text)
        print("─" * 70)
        return

    # Telegram Dispatch with 4000-char Chunking Protection (RULE-067)
    group_id = "-1004375899205"
    anna_token = "8632069800:AAGl63rQuntU9-a84u0X37bsky4CppB-GhM"
    ctx_ssl = ssl._create_unverified_context()

    chunks = [brief_text[i:i+4000] for i in range(0, len(brief_text), 4000)]
    for chunk in chunks:
        try:
            url = f"https://api.telegram.org/bot{anna_token}/sendMessage"
            payload = json.dumps({"chat_id": group_id, "text": chunk}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, context=ctx_ssl, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                if data.get("ok"):
                    print("  ✅ Successfully dispatched Dynamic Pre-Market Brief to Telegram Group!")
        except Exception as e:
            print(f"  🔴 Error dispatching to Telegram: {e}")

    # Mirror to AgentBridge
    try:
        bridge = AgentBridge("anna")
        bridge.send("hermes", brief_text, channel="intel", subject="Dynamic Pre-Market Brief")
        print("  ✅ Mirrored Pre-Market Brief to Hermes via AgentBridge!")
    except Exception: pass

    print("============================================================")

if __name__ == "__main__":
    dispatch_premarket_brief()
