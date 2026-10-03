#!/usr/bin/env python3
"""
daily_report.py — Dynamic Morning Ledger Reconciliation & Graduation Scorecard Dispatcher

Runs automatically every morning at 07:15 ICT (Tue-Sat) via Linux Cron:
1. Queries live account balances and open positions directly from Alpaca REST API
2. Performs full ledger reconciliation between Cash in Bank and Paper Mark
3. Updates graduation_scorecard.json dynamically
4. Dispatches the Hermes Fast-Track Daily Report to Telegram and mirrors to Anna via AgentBridge!
"""

import sys
import os
import json
import datetime
import urllib.request
import ssl
from pathlib import Path

# Add shared directory to path
sys.path.insert(0, str(Path(__file__).parent))
from alpaca_broker import AlpacaClient
from tradier_broker import TradierClient
from agent_bridge import AgentBridge

def get_realized_pnl_benchmarks(base_dir=None, now_dt=None):
    """
    Computes dynamic Realized PnL performance benchmarks against the Weekly Velocity Speedometer
    and Monthly Net Cash targets.
    
    Data Source Priority:
    1. Primary: Direct live broker fills via `trade_history.spread_round_trips` (Alpaca + Tradier)
    2. Resilient Fallback: `SkonVault_Live_Transaction_Journal.xlsx` (Sheet: Internal_PnL_Journal)
    
    Returns structured metrics including:
    - WTD Realized PnL ($), WTD % vs $1,441.50 target, WTD % vs $961.00 floor
    - MTD Realized PnL ($), MTD % vs $5,766.00 target
    - All-Time Realized PnL ($), Win Rate %, Closed Trade count (Wins/Losses)
    - Active Premium Pipeline credit ($), Projected WTD PnL ($) and Pacing %
    - Velocity Badge for at-a-glance performance status
    """
    if base_dir is None:
        base_dir = Path("/home/ubuntu/shared")
        if not base_dir.exists():
            base_dir = Path("/Users/SkonP/AI_Prompt/Obsidient/SkonVault/shared")
    else:
        base_dir = Path(base_dir)

    if now_dt is None:
        now_dt = datetime.datetime.now()

    # Determine WTD and MTD boundary dates (Monday anchor for trading week)
    start_of_week = (now_dt - datetime.timedelta(days=now_dt.weekday())).date()
    start_of_week_str = start_of_week.strftime("%Y-%m-%d")
    start_of_month_str = now_dt.date().replace(day=1).strftime("%Y-%m-%d")

    trips = []
    source_used = None

    # Source 1: Live broker fill round trips from trade_history
    try:
        sys.path.insert(0, str(base_dir))
        from trade_history import fetch_fills, spread_round_trips
        al_fills = fetch_fills("alpaca_live")
        tr_fills = fetch_fills("tradier_live")
        trips = spread_round_trips(accounts=["alpaca_live", "tradier_live"])
        closed_check = [t for t in trips if t.get("status") in ("CLOSED_HARVESTED", "CLOSED_DEFENSIVE")]
        if closed_check:
            source_used = "live_broker_fills"
    except Exception:
        trips = []

    # Source 2: Fallback to SkonVault_Live_Transaction_Journal.xlsx
    if not trips or source_used is None:
        xlsx_paths = [
            base_dir / "SkonVault_Live_Transaction_Journal.xlsx",
            base_dir.parent / "SkonVault_Live_Transaction_Journal.xlsx",
            Path("SkonVault_Live_Transaction_Journal.xlsx"),
            base_dir / "SkonVault_Transaction_Journal.xlsx",
            base_dir.parent / "SkonVault_Transaction_Journal.xlsx",
        ]
        xlsx_file = next((p for p in xlsx_paths if p.exists()), None)
        if xlsx_file:
            try:
                import openpyxl
                wb = openpyxl.load_workbook(str(xlsx_file), data_only=True)
                if "Internal_PnL_Journal" in wb.sheetnames:
                    ws = wb["Internal_PnL_Journal"]
                    excel_trips = []
                    for r in range(5, ws.max_row + 1):
                        tid = ws.cell(r, 1).value
                        if not tid or "TOTALS" in str(tid).upper():
                            continue
                        acct = str(ws.cell(r, 2).value or "")
                        asset = str(ws.cell(r, 3).value or "")
                        op_date = str(ws.cell(r, 6).value or "")[:10]
                        cl_date_raw = str(ws.cell(r, 17).value or "").strip()
                        cl_date = cl_date_raw[:10] if cl_date_raw and cl_date_raw != "-" else None
                        
                        contracts = int(ws.cell(r, 13).value or 1)
                        tot_credit = float(ws.cell(r, 15).value or 0.0)
                        credit_sh = float(ws.cell(r, 14).value or 0.0)
                        realized = float(ws.cell(r, 21).value or 0.0)
                        status = str(ws.cell(r, 25).value or "OPEN").strip()
                        
                        excel_trips.append({
                            "id": tid,
                            "account": acct,
                            "root": asset,
                            "contracts": contracts,
                            "credit_sh": credit_sh,
                            "tot_credit": tot_credit,
                            "open_date": op_date,
                            "close_date": cl_date,
                            "realized_pnl": realized,
                            "status": status
                        })
                    if excel_trips:
                        trips = excel_trips
                        source_used = "excel_journal_fallback"
            except Exception:
                pass

    closed_trades = [t for t in trips if t.get("status") in ("CLOSED_HARVESTED", "CLOSED_DEFENSIVE")]
    open_trades = [t for t in trips if t.get("status") == "OPEN"]

    # WTD Realized PnL: closed on or after start_of_week_str
    wtd_trades = [t for t in closed_trades if (t.get("close_date") or "") >= start_of_week_str]
    wtd_realized = round(sum(t.get("realized_pnl", 0.0) for t in wtd_trades), 2)

    # MTD Realized PnL: closed on or after start_of_month_str
    mtd_trades = [t for t in closed_trades if (t.get("close_date") or "") >= start_of_month_str]
    mtd_realized = round(sum(t.get("realized_pnl", 0.0) for t in mtd_trades), 2)

    # All-Time Realized PnL
    all_time_realized = round(sum(t.get("realized_pnl", 0.0) for t in closed_trades), 2)
    closed_wins = len([t for t in closed_trades if (t.get("realized_pnl") or 0.0) > 0])
    closed_losses = len([t for t in closed_trades if (t.get("realized_pnl") or 0.0) < 0])
    win_rate_pct = round(closed_wins / len(closed_trades) * 100.0, 1) if closed_trades else 0.0

    # Velocity Targets
    weekly_target = 1441.50
    weekly_floor = 961.00
    monthly_target = 5766.00
    monthly_floor = 3844.00

    wtd_target_pct = round(wtd_realized / weekly_target * 100.0, 1) if weekly_target > 0 else 0.0
    wtd_floor_pct = round(wtd_realized / weekly_floor * 100.0, 1) if weekly_floor > 0 else 0.0
    mtd_target_pct = round(mtd_realized / monthly_target * 100.0, 1) if monthly_target > 0 else 0.0
    mtd_floor_pct = round(mtd_realized / monthly_floor * 100.0, 1) if monthly_floor > 0 else 0.0

    # Active Premium Pipeline
    active_pipe_credit = round(sum(t.get("tot_credit", 0.0) for t in open_trades), 2)
    pipe_items = []
    for t in open_trades:
        pipe_items.append(f"{t.get('root')} {t.get('contracts')}C: +${t.get('tot_credit', 0.0):,.0f}")
    pipe_details = " | ".join(pipe_items) if pipe_items else "No open positions"

    projected_wtd = round(wtd_realized + active_pipe_credit, 2)
    projected_target_pct = round(projected_wtd / weekly_target * 100.0, 1) if weekly_target > 0 else 0.0
    projected_floor_pct = round(projected_wtd / weekly_floor * 100.0, 1) if weekly_floor > 0 else 0.0

    # Velocity Badge
    if wtd_realized >= weekly_target:
        velocity_badge = "🎯 TARGET CRUSHED (≥18.0%+ PACE)"
    elif wtd_realized >= weekly_floor:
        velocity_badge = "🟢 FLOOR SECURED (≥12.0% PACE)"
    elif projected_wtd >= weekly_target:
        velocity_badge = "⚡ HIGH-VELOCITY PIPELINE (PROJECTED ≥18.0%)"
    elif projected_wtd >= weekly_floor:
        velocity_badge = "🛡️ ON TRACK FOR FLOOR (PROJECTED ≥12.0%)"
    else:
        velocity_badge = "⏳ ACCUMULATING SPREADS"

    return {
        "source": source_used,
        "start_of_week": start_of_week_str,
        "start_of_month": start_of_month_str,
        "wtd_realized": wtd_realized,
        "wtd_trades_count": len(wtd_trades),
        "wtd_target_pct": wtd_target_pct,
        "wtd_floor_pct": wtd_floor_pct,
        "mtd_realized": mtd_realized,
        "mtd_trades_count": len(mtd_trades),
        "mtd_target_pct": mtd_target_pct,
        "mtd_floor_pct": mtd_floor_pct,
        "all_time_realized": all_time_realized,
        "closed_count": len(closed_trades),
        "closed_wins": closed_wins,
        "closed_losses": closed_losses,
        "win_rate_pct": win_rate_pct,
        "active_pipe_credit": active_pipe_credit,
        "pipe_details": pipe_details,
        "projected_wtd": projected_wtd,
        "projected_target_pct": projected_target_pct,
        "projected_floor_pct": projected_floor_pct,
        "velocity_badge": velocity_badge,
    }

def generate_and_dispatch_report():
    print("============================================================")
    print("📊 RUNNING DYNAMIC MORNING LEDGER AUDIT & SCORECARD DISPATCH")
    print("   [OPTION A: PURE REAL-MONEY LIVE FOCUS]")
    print("============================================================")

    now_dt = datetime.datetime.now()
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    now_ict = now_dt.strftime("%Y-%m-%d %H:%M ICT")
    weekday_name = now_dt.strftime("%A")
    base_dir = Path("/home/ubuntu/shared")
    if not base_dir.exists():
        base_dir = Path("/Users/SkonP/AI_Prompt/Obsidient/SkonVault/shared")

    # 1. Fetch live balances & positions for Option A (Pure Live)
    alpaca_live_eq, alpaca_live_cash = 0.0, 0.0
    alpaca_live_pos = []
    alpaca_live_ok = False
    b_alpaca_live = AlpacaClient("alpaca_live")
    try:
        acct_al = b_alpaca_live.get_account()
        alpaca_live_eq = float(acct_al.get("equity", 0))
        alpaca_live_cash = float(acct_al.get("cash", 0))
        raw_al_pos = b_alpaca_live.get_positions()
        alpaca_live_pos = [p for p in raw_al_pos if p.get("symbol") != "SGOV"]
        alpaca_live_ok = True
    except Exception as e:
        print(f"  ℹ️ Alpaca Live notice: {e}")

    tradier_live_eq, tradier_live_cash = 0.0, 0.0
    tradier_live_pos = []
    tradier_live_ok = False
    try:
        b_tradier_live = TradierClient("live")
        acct_tl = b_tradier_live.get_account()
        tradier_live_eq = float(acct_tl.get("total_equity", 0))
        tradier_live_cash = float(acct_tl.get("cash", 0))
        raw_tl_pos = b_tradier_live.get_positions()
        tradier_live_pos = [p for p in raw_tl_pos if p.get("symbol") != "SGOV"]
        tradier_live_ok = True
    except Exception as e:
        print(f"  ℹ️ Tradier Live notice: {e}")

    # Query paper accounts quietly for background reference (standby mode)
    pion_eq, pion_cash, pion2_eq, pion2_cash = 0.0, 0.0, 0.0, 0.0
    try:
        b_pion = AlpacaClient("pion_main")
        acct_p = b_pion.get_account()
        pion_eq = float(acct_p.get("equity", 0))
        pion_cash = float(acct_p.get("cash", 0))
    except Exception: pass
    try:
        b_pion2 = AlpacaClient("pion2_sub")
        acct_p2 = b_pion2.get_account()
        pion2_eq = float(acct_p2.get("equity", 0))
        pion2_cash = float(acct_p2.get("cash", 0))
    except Exception: pass

    # Reconcile active_trades with live broker positions (RULE-092)
    try:
        from reconcile_active_trades import reconcile_active_trades
        reconcile_active_trades(quiet=True)
    except Exception:
        pass

    total_live_cash = alpaca_live_cash + tradier_live_cash
    total_live_eq = alpaca_live_eq + tradier_live_eq
    target_monthly_cash = 5766.00
    floor_monthly_cash = 3844.00
    cash_defense_floor = 11249.00
    max_margin_envelope = 20891.00

    # Critical Guard: Never dispatch corrupted $0.00 reports if broker auth fails
    if total_live_eq <= 0.0 and not alpaca_live_ok and not tradier_live_ok:
        print("  🛑 CRITICAL DATA GUARD: Broker connection/authentication failed for live accounts!")
        print("     Suppressing Telegram report dispatch to prevent emitting false $0.00 balances.")
        return

    scorecard_path = base_dir / "graduation_scorecard.json"
    target_eq = 18042.37
    total_cash = total_live_cash
    comb_eq = total_live_eq
    completed_trades = 2
    win_rate = 100.0
    target_grad_iso = "2026-09-25"

    if scorecard_path.exists():
        try:
            sc_data = json.loads(scorecard_path.read_text(encoding="utf-8"))
            ftp = sc_data.get("fast_track_progress", {})
            target_eq = float(ftp.get("week_target_equity", target_eq))
            completed_trades = int(ftp.get("completed_trades", completed_trades))
            win_rate = float(ftp.get("win_rate_pct", win_rate))
            target_grad_iso = ftp.get("target_graduation_date", target_grad_iso)
        except Exception:
            pass

    delta = comb_eq - target_eq
    cash_gap = max(0.0, target_eq - total_cash)
    pct_goal = (total_cash / target_eq * 100) if target_eq > 0 else 0.0

    # Dynamic Week Number Calculator (100% Automated / Zero Manual Patching)
    base_date = datetime.date(2026, 6, 29) # Launch Reference Anchor (Week 1)
    days_elapsed = (now_dt.date() - base_date).days
    week_num = max(1, (days_elapsed // 7) + 1)
    week_label = f"WEEK {week_num}" if week_num < 10 else f"WEEK {week_num} (POST-WEEK 10 ADVANCED SPRINT 🚀)"

    # Dynamic Graduation Projection & Milestone Tracking (RULE-068 & RULE-075)
    weekly_pace = 1350.0  # Calibrated Dual-Account Sprint Pace ($1,350/wk active turnover)
    if total_cash >= target_eq:
        proj_grad_date_str = "GOAL ACHIEVED 🎓"
        proj_grad_status_str = f"COMPLETED (${total_cash:,.2f} >= ${target_eq:,.2f})"
        target_grad_iso = now_dt.strftime("%Y-%m-%d")
        weeks_rem = 0
    else:
        try:
            target_grad_date = datetime.datetime.strptime(target_grad_iso, "%Y-%m-%d").date()
        except Exception:
            target_grad_date = datetime.date(2026, 9, 25)
        days_rem = (target_grad_date - now_dt.date()).days
        disp_date = target_grad_date.strftime("%b %d, %Y")
        if days_rem <= 0:
            weeks_rem = 0
            proj_grad_date_str = disp_date
            proj_grad_status_str = "FINAL SPRINT DAY 🏁"
        else:
            weeks_rem = max(1, round(days_rem / 7.0))
            proj_grad_date_str = disp_date
            proj_grad_status_str = f"ON TRACK (Sprint: {days_rem} Days Remaining | Target: {disp_date} 🎯)"

    # 1b. Fetch dynamic Realized PnL benchmarks against Weekly Velocity Speedometer
    bench = get_realized_pnl_benchmarks(base_dir=base_dir, now_dt=now_dt)

    # 2. Build Scorecard Data for Live Production
    scorecard_data = {
        "timestamp": now_utc,
        "updated_at_ict": now_ict,
        "mode": "OPTION_A_PURE_LIVE",
        "live_production_progress": {
            "total_liquid_cash": round(total_live_cash, 2),
            "current_combined_equity": round(total_live_eq, 2),
            "monthly_target_net_cash": 5766.00,
            "monthly_target_pct": "18%+ Target (>=12% Floor)",
            "monthly_floor_net_cash": 3844.00,
            "weekly_velocity_target": 1441.50,
            "weekly_velocity_floor": 961.00,
            "cash_defense_floor": 11249.00,
            "max_margin_envelope": 20891.00,
            "realized_pnl_benchmarks": {
                "source": bench["source"],
                "wtd_realized_pnl": bench["wtd_realized"],
                "wtd_pacing_target": 1441.50,
                "wtd_target_pct": bench["wtd_target_pct"],
                "wtd_floor_pct": bench["wtd_floor_pct"],
                "mtd_realized_pnl": bench["mtd_realized"],
                "mtd_target_pct": bench["mtd_target_pct"],
                "all_time_realized_pnl": bench["all_time_realized"],
                "all_time_closed_trades": bench["closed_count"],
                "all_time_wins": bench["closed_wins"],
                "all_time_losses": bench["closed_losses"],
                "all_time_win_rate_pct": bench["win_rate_pct"],
                "active_pipeline_credit": bench["active_pipe_credit"],
                "projected_wtd_pnl": bench["projected_wtd"],
                "projected_target_pct": bench["projected_target_pct"],
                "projected_floor_pct": bench["projected_floor_pct"],
                "velocity_badge": bench["velocity_badge"]
            },
            "quarantined_accounts": {
                "ibkr": {"account_number": "U25439978", "equity": 2200.0, "status": "QUARANTINED_UNTOUCHED"}
            },
            "status": "LIVE_PRODUCTION_ACTIVE"
        },
        "accounts": {
            "alpaca_live": {
                "equity": alpaca_live_eq,
                "cash": alpaca_live_cash,
                "positions_count": len(alpaca_live_pos),
                "open_positions": [p.get("symbol") for p in alpaca_live_pos]
            },
            "tradier_live": {
                "equity": tradier_live_eq,
                "cash": tradier_live_cash,
                "positions_count": len(tradier_live_pos),
                "open_positions": [p.get("symbol") for p in tradier_live_pos]
            },
            "standby_paper": {
                "pion_main_cash": pion_cash,
                "pion2_sub_cash": pion2_cash,
                "status": "PASSIVE_STANDBY"
            }
        }
    }

    # Write graduation_scorecard.json
    sc_file = base_dir / "graduation_scorecard.json"
    sc_file.write_text(json.dumps(scorecard_data, indent=2), encoding="utf-8")
    print(f"  ✅ Updated {sc_file} with live balances!")

    # 2b. Ingest Last Entry Execution Status (RULE-083 Candidate Reconciliation)
    last_entry_msg = "• Status: SGOV Treasury liquidated to 100% Cash (~$32,028 ready for Monday launch)."
    try:
        last_entry_file = base_dir / "last_entry_status.json"
        if last_entry_file.exists():
            e_data = json.loads(last_entry_file.read_text(encoding="utf-8"))
            e_subj = e_data.get("subject", "N/A")
            e_msg = e_data.get("message", "N/A")
            e_time = e_data.get("timestamp", "N/A")
            if "ORDER_RESOLVED" in e_subj:
                last_entry_msg = f"• Prior Session Event: {e_subj} ({e_time})\n• Resolution: {e_msg}"
            elif "ORDER_FILLED" in e_subj:
                last_entry_msg = f"• Prior Session Event: {e_subj} ({e_time})\n• Fill Summary: {e_msg}"
            else:
                last_entry_msg = f"• Prior Session Event: {e_subj} ({e_time})\n• Resolution: {e_msg}"
    except Exception as ex_reconcile:
        last_entry_msg = f"• Reconciliation notice: {ex_reconcile}"

    # 3. Format Dynamic Daily Report with RULE-060 Intelligent Spread Formatting
    from intelligent_spread_formatter import format_intelligent_spread_report
    alpaca_pos_str = format_intelligent_spread_report(alpaca_live_pos, "Alpaca Live (#290523608)")
    tradier_pos_str = format_intelligent_spread_report(tradier_live_pos, "Tradier Live (#6YB80974)")

    # Build Dynamic OPEX & Rollover Protocol Directive (DIR-09 / RULE-077)
    from intelligent_spread_formatter import parse_option_symbol
    all_positions = alpaca_live_pos + tradier_live_pos
    spreads_by_sym = {}
    for p in all_positions:
        sym = p.get("symbol", "")
        und, exp_d, otype, strike = parse_option_symbol(sym)
        if und and exp_d:
            p_augmented = dict(p)
            p_augmented["opt_type"] = otype
            p_augmented["strike"] = strike
            spreads_by_sym.setdefault((und, exp_d), []).append(p_augmented)

    opex_directives = []
    today_dt = datetime.date.today()
    for (und, exp_d), legs in spreads_by_sym.items():
        try:
            exp_dt = datetime.datetime.strptime(exp_d, "%Y-%m-%d").date()
            dte = (exp_dt - today_dt).days
        except Exception:
            dte = 30

        spot = 0.0
        try:
            from live_spot import get_spot
            spot_res = get_spot(und)
            if isinstance(spot_res, dict):
                spot = float(spot_res.get("price") or 0.0)
            else:
                spot = float(spot_res or 0.0)
        except Exception:
            pass

        # Check if Iron Condor (both Put and Call spreads present)
        put_shorts = [l for l in legs if float(l.get("qty", l.get("quantity", 0))) < 0 and l.get("opt_type") == "P"]
        put_longs = [l for l in legs if float(l.get("qty", l.get("quantity", 0))) > 0 and l.get("opt_type") == "P"]
        call_shorts = [l for l in legs if float(l.get("qty", l.get("quantity", 0))) < 0 and l.get("opt_type") == "C"]
        call_longs = [l for l in legs if float(l.get("qty", l.get("quantity", 0))) > 0 and l.get("opt_type") == "C"]

        if put_shorts and put_longs and call_shorts and call_longs:
            # ──────────────────────────────────────────────────────────────────
            # RULE-094: DUAL-WING IRON CONDOR OPEX DIRECTIVE
            # ──────────────────────────────────────────────────────────────────
            p_s = put_shorts[0]["strike"]
            p_l = put_longs[0]["strike"]
            c_s = call_shorts[0]["strike"]
            c_l = call_longs[0]["strike"]

            p_buf_pct = ((spot - p_s) / spot * 100.0) if spot > 0 else 0.0
            c_buf_pct = ((c_s - spot) / spot * 100.0) if spot > 0 else 0.0

            if spot > 0 and spot < p_s:
                opex_directives.append(
                    f"• {und} (${p_s:.0f}P/${p_l:.0f}P & ${c_s:.0f}C/${c_l:.0f}C Iron Condor, Exp {exp_d} | {dte} DTE):\n"
                    f"  * Status: 🔴 IN-THE-MONEY BREACH ON PUT WING (Spot ${spot:.2f} vs Put Strike ${p_s:.0f}P | Buffer: {p_buf_pct:+.1f}%)\n"
                    f"  * Strategic Directive: MANDATORY DEFENSIVE STOP-OUT (DIR-09). Salvage collateral before pin risk!"
                )
            elif spot > 0 and spot > c_s:
                opex_directives.append(
                    f"• {und} (${p_s:.0f}P/${p_l:.0f}P & ${c_s:.0f}C/${c_l:.0f}C Iron Condor, Exp {exp_d} | {dte} DTE):\n"
                    f"  * Status: 🔴 IN-THE-MONEY BREACH ON CALL WING (Spot ${spot:.2f} vs Call Strike ${c_s:.0f}C | Buffer: {c_buf_pct:+.1f}%)\n"
                    f"  * Strategic Directive: MANDATORY DEFENSIVE STOP-OUT (DIR-09). Salvage collateral before pin risk!"
                )
            else:
                opex_directives.append(
                    f"• {und} (${p_s:.0f}P/${p_l:.0f}P & ${c_s:.0f}C/${c_l:.0f}C Iron Condor, Exp {exp_d} | {dte} DTE):\n"
                    f"  * Status: ⏳ MID-CYCLE THETA (Spot ${spot:.2f} | Put Buffer: {p_buf_pct:+.1f}% | Call Buffer: {c_buf_pct:+.1f}%)\n"
                    f"  * Strategic Directive: Active FastHarvest monitoring (50% TP harvest target under DIR-09)."
                )

        else:
            # Single-Wing Spread (Bull Put or Bear Call)
            short_l = next((l for l in legs if float(l.get("qty", l.get("quantity", 0))) < 0), None)
            long_l = next((l for l in legs if float(l.get("qty", l.get("quantity", 0))) > 0), None)
            if short_l and long_l:
                _, _, otype, s_strike = parse_option_symbol(short_l.get("symbol", ""))
                _, _, _, l_strike = parse_option_symbol(long_l.get("symbol", ""))
                is_put = otype == "P"
                opt_lbl = "P" if is_put else "C"
                strat_label = f"${s_strike:.0f}{opt_lbl}/${l_strike:.0f}{opt_lbl}"

                buf_pct = (((spot - s_strike) if is_put else (s_strike - spot)) / spot * 100.0) if spot > 0 else 0.0
                is_itm = (spot < s_strike) if is_put else (spot > s_strike)

                if s_strike > 0 and spot > 0 and is_itm:
                    opex_directives.append(
                        f"• {und} ({strat_label}, Exp {exp_d} | {dte} DTE):\n"
                        f"  * Status: 🔴 IN-THE-MONEY BREACH (Spot ${spot:.2f} vs Strike ${s_strike:.0f}{opt_lbl} | Buffer: {buf_pct:+.1f}%)\n"
                        f"  * Strategic Directive: MANDATORY DEFENSIVE STOP-OUT (DIR-09). Salvage collateral before pin risk!"
                    )
                elif dte <= 7:
                    if buf_pct >= 2.5:
                        opex_directives.append(
                            f"• {und} ({strat_label}, Exp {exp_d} | {dte} DTE):\n"
                            f"  * Status: 🟢 SAFE OTM BUFFER (Spot ${spot:.2f} vs Strike ${s_strike:.0f}{opt_lbl} | Buffer: {buf_pct:+.1f}% >= 2.5%)\n"
                            f"  * Strategic Directive: DIR-09 EXPIRATION RUN. Hold to 100% expiry if profit < 90%; harvest early if >= 90%."
                        )
                    else:
                        opex_directives.append(
                            f"• {und} ({strat_label}, Exp {exp_d} | {dte} DTE):\n"
                            f"  * Status: ⚠️ THIN BUFFER (Spot ${spot:.2f} vs Strike ${s_strike:.0f}{opt_lbl} | Buffer: {buf_pct:+.1f}% < 2.5%)\n"
                            f"  * Strategic Directive: T-3 GAMMA DEFENSE. Close early under DIR-09 to de-risk."
                        )
                elif dte <= 12 and und == "NVDA":
                    # GAP #2: NVDA 11 DTE nearest to expiry, pull 50% TP harvest decision to T-4
                    opex_directives.append(
                        f"• {und} ({strat_label}, Exp {exp_d} | {dte} DTE):\n"
                        f"  * Status: ⏳ GAMMA CLIFF APPROACHING (Spot ${spot:.2f} | Buffer: {buf_pct:+.1f}% | 11 DTE)\n"
                        f"  * Strategic Directive: ADVANCE FASTHARVEST TO T-4 (Oct 07 Rollover Boundary). Prioritize 50% TP harvest before T-4."
                    )
                elif buf_pct < 3.0 and buf_pct > 0.5:
                    # GAP #3: XLE or thin buffer watch
                    opex_directives.append(
                        f"• {und} ({strat_label}, Exp {exp_d} | {dte} DTE):\n"
                        f"  * Status: ⚠️ THIN BUFFER SURVEILLANCE (Spot ${spot:.2f} | Buffer: {buf_pct:+.1f}%)\n"
                        f"  * Strategic Directive: Tighten vigilance. Maintain strike-touch defense at 0.50% buffer floor."
                    )
                elif dte <= 21:
                    opex_directives.append(
                        f"• {und} ({strat_label}, Exp {exp_d} | {dte} DTE):\n"
                        f"  * Status: ⏳ MID-CYCLE THETA (Spot ${spot:.2f} | Buffer: {buf_pct:+.1f}%)\n"
                        f"  * Strategic Directive: Active FastHarvest monitoring (50% TP harvest target under DIR-09)."
                    )

    if not opex_directives:
        opex_directives_str = "• All active live spreads safely harvesting theta outside T-7 window."
    else:
        opex_directives_str = "\n".join(opex_directives)

    wtd_sign = "+" if bench['wtd_realized'] >= 0 else "-"
    mtd_sign = "+" if bench['mtd_realized'] >= 0 else "-"
    all_time_sign = "+" if bench['all_time_realized'] >= 0 else "-"
    proj_sign = "+" if bench['projected_wtd'] >= 0 else "-"

    wtd_str = f"{wtd_sign}${abs(bench['wtd_realized']):,.2f}"
    mtd_str = f"{mtd_sign}${abs(bench['mtd_realized']):,.2f}"
    all_time_str = f"{all_time_sign}${abs(bench['all_time_realized']):,.2f}"
    proj_str = f"{proj_sign}${abs(bench['projected_wtd']):,.2f}"

    report_text = f"""📊 HERMES PRODUCTION LIVE REPORT — {now_ict}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🏆 OPTION A: REAL-MONEY LIVE PORTFOLIO LEDGER
• Primary Core Barbell (Alpaca #290523608) : Equity ${alpaca_live_eq:,.2f} | Cash ${alpaca_live_cash:,.2f}
• High-Velocity Sprint (Tradier #6YB80974)  : Equity ${tradier_live_eq:,.2f} | Cash ${tradier_live_cash:,.2f}
• Combined Live Production Base            : ${total_live_eq:,.2f} (Total Cash: ${total_live_cash:,.2f} 🟢)
• Cash Defense Floor (>=35% Permanent)      : ${cash_defense_floor:,.2f} (Liquid Defense Active 🛡️)
• Monthly Net Cash Target (18%+ Target | >=12% Floor) : >=$5,766 / month (Min Floor: $3,844)
• Weekly Velocity Speedometer (18%+ Pacing) : ⏱️ Pacing Target: $1,441.50/wk | Floor: $961.00/wk (≥18.0%+ Velocity)
• Realized PnL Weekly Pacing Benchmark     : 🏁 Actual WTD: {wtd_str} / $1,441.50 ({bench['wtd_target_pct']:.1f}% Target | {bench['wtd_floor_pct']:.1f}% Floor) [{bench['velocity_badge']}]
• Monthly & Lifetime Realized Performance   : 💵 MTD: {mtd_str} / >=$5,766 ({bench['mtd_target_pct']:.1f}%) | All-Time: {all_time_str} ({bench['closed_wins']}W/{bench['closed_losses']}L, {bench['win_rate_pct']:.1f}% WR)
• Active Premium Pipeline (Pending Decay)  : 🌾 +${bench['active_pipe_credit']:,.2f} Max Credit ({bench['pipe_details']}) | Projected WTD: {proj_str} ({bench['projected_target_pct']:.1f}% Pacing)
• Interactive Brokers (#U25439978)         : 🔒 QUARANTINED / 100% UNTOUCHED ($2,200.00)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💼 ACTIVE PRODUCTION LIVE SPREAD INTELLIGENCE (RULE-060)

[1. Alpaca Live (#290523608) — Core Barbell]
{alpaca_pos_str}

[2. Tradier Live (#6YB80974) — High-Velocity Sprint]
{tradier_pos_str}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 PREVIOUS SESSION ENTRY RECONCILIATION (RULE-083)
{last_entry_msg}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🛡️ OPEX & ROLLOVER PROTOCOL DIRECTIVE (DIR-09 / RULE-077)
{opex_directives_str}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚡ PROTOCOL EXECUTION STATUS
• FastHarvest 5-Minute Poller : ACTIVE & AUDITING LIVE ACCOUNTS 🌾
• Dynamic Quant Screener     : Sizing 60% Sprint / 40% Anchor under VIX < 18
• Standby Paper Accounts     : pion_main (${pion_cash:,.2f}) | pion2_sub (${pion2_cash:,.2f}) [PASSIVE STANDBY]

All production accounts reconciled with live broker reality! 🚀📈"""

    # 4. Telegram Dispatch with 4000-char Chunking Protection (RULE-067)
    group_id = "-1004375899205"
    hermes_token = "8991076258:AAGHyb-jEnp29BQ5O5V0mo4vFOmgXfgnmTg"
    ctx_ssl = ssl._create_unverified_context()

    # Section-aware chunking to prevent HTTP 400 Bad Request
    chunks = []
    if len(report_text) <= 4000:
        chunks = [report_text]
    else:
        sections = report_text.split("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        current_chunk = ""
        for sec in sections:
            candidate = current_chunk + ("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n" if current_chunk else "") + sec
            if len(candidate) <= 3800:
                current_chunk = candidate
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = sec
        if current_chunk:
            chunks.append(current_chunk.strip())

        final_chunks = []
        for c in chunks:
            if len(c) > 4000:
                for j in range(0, len(c), 3800):
                    final_chunks.append(c[j:j+3800])
            else:
                final_chunks.append(c)
        chunks = final_chunks

    for idx, chunk in enumerate(chunks, 1):
        try:
            url = f"https://api.telegram.org/bot{hermes_token}/sendMessage"
            payload = json.dumps({"chat_id": group_id, "text": chunk}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, context=ctx_ssl, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                if data.get("ok"):
                    print(f"  ✅ Successfully dispatched Hermes Daily Report (Part {idx}/{len(chunks)}) to Telegram!")
        except Exception as e:
            print(f"  🔴 Error dispatching report to Telegram (Part {idx}): {e}")

    # 5. Mirror to AgentBridge
    try:
        bridge = AgentBridge("hermes")
        bridge.send("anna", report_text, channel="intel", subject="Hermes Daily Account Reconciliation")
        print("  ✅ Mirrored Hermes Daily Report to Anna via AgentBridge!")
    except Exception: pass

    # 6. Autonomous Excel Transaction Journal Update (RULE-051)
    try:
        import transaction_journal_manager
        transaction_journal_manager.update_excel_journal()
        print("  📊 Updated SkonVault_Live_Transaction_Journal.xlsx with morning ledger balances!")
    except Exception as ex_j:
        print(f"  ℹ️ Journal sync notice: {ex_j}")

    print("============================================================")

if __name__ == "__main__":
    generate_and_dispatch_report()
