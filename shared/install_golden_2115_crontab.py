#!/usr/bin/env python3
"""
install_golden_2115_crontab.py — Configures the Cloud Server Crontab for the Master 21:15 ICT Execution Engine

Schedules (All times in ICT / UTC):
- 07:15 ICT (00:15 UTC): daily_report.py (Morning Ledger & Scorecard)
- 19:35 ICT (12:35 UTC): dynamic_universe_screener.py (5-Factor Quant Screener)
- 19:40 ICT (12:40 UTC): dispatch_daily_premarket_brief.py (Anna & Hermes Pre-Market Telegram Brief)
- 21:10 ICT (14:10 UTC): run_uoa_scan.py (Institutional UOA Sweep #1)
- 21:15 ICT (14:15 UTC): execute_golden_2115_daily_entry.py (Master 21:15 ICT Golden Entry Engine)
- 21:45 ICT (14:45 UTC): run_uoa_scan.py (Institutional UOA Sweep #2)
- 21:55 ICT (14:55 UTC): dispatch_peak_hour_brief.py (Anna Peak Hour Telemetry Brief)
- 20:30-03:30 ICT (13:30-20:30 UTC): auto_harvest_positions.py (5-Min FastHarvest Poller)
"""

import sys
import os
import subprocess

CRON_LINES = """# SkonVault Master 21:15 ICT Autonomous Institutional Trading Crontab
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/ubuntu/.local/bin
PYTHONPATH=/home/ubuntu/shared:/home/ubuntu/openclaw
CRON_TZ=Asia/Bangkok
TZ=Asia/Bangkok

# 1. 07:15 ICT: Morning Ledger Reconciliation & Scorecard (Tue-Sat)
15 7 * * 2-6 python3 /home/ubuntu/shared/daily_report.py >> /home/ubuntu/shared/daily_report.log 2>&1

# 2. 18:30 ICT: Master Autonomous Sunday Reset & Self-Healing Engine (Sun Only / Off-Peak)
30 18 * * 0 python3 /home/ubuntu/shared/master_sunday_reset_daemon.py >> /home/ubuntu/shared/sunday_reset.log 2>&1

# 3. 19:35 ICT: 5-Factor Dynamic Universe Quant Screener (Sun-Fri)
35 19 * * 0-5 python3 /home/ubuntu/shared/dynamic_universe_screener.py >> /home/ubuntu/shared/screener.log 2>&1

# 4. 19:40 ICT: Anna & Hermes Pre-Market Intelligence Brief (Sun-Fri)
40 19 * * 0-5 python3 /home/ubuntu/shared/dispatch_daily_premarket_brief.py >> /home/ubuntu/shared/premarket.log 2>&1

# 4b. 20:50 ICT: Live Liquidity Radar & Dynamic Options Matrix Sweep (Mon-Fri)
50 20 * * 1-5 python3 /home/ubuntu/shared/live_liquidity_matrix_scanner.py --notify >> /home/ubuntu/shared/liquidity_matrix.log 2>&1

# 5. 21:10 ICT: Institutional UOA Sweep #1 (Tumbler 4 Lock-In) (Mon-Fri)
10 21 * * 1-5 python3 /home/ubuntu/shared/run_uoa_scan.py >> /home/ubuntu/shared/uoa_sweep.log 2>&1

# 6. 21:15 ICT: Master 21:15 ICT Golden Entry Engine (35% Max Safe Sizing) (Mon-Fri)
15 21 * * 1-5 python3 /home/ubuntu/shared/execute_golden_2115_daily_entry.py >> /home/ubuntu/shared/execution.log 2>&1

# 6b. 21:16-21:59 ICT: Software OCO Listener & Fill Accelerator (Every 2 mins)
16-59/2 21 * * 1-5 python3 /home/ubuntu/shared/multi_slot_portfolio_manager.py --oco >> /home/ubuntu/shared/oco_listener.log 2>&1

# 7. 21:30 ICT: RULE-075 Stage 1 Order Fill Check & Micro-Nudge (Mon-Fri)
30 21 * * 1-5 python3 /home/ubuntu/shared/order_fill_tracker.py --stage1 >> /home/ubuntu/shared/order_tracker.log 2>&1

# 8. 21:45 ICT: Institutional UOA Sweep #2 (Mon-Fri)
45 21 * * 1-5 python3 /home/ubuntu/shared/run_uoa_scan.py >> /home/ubuntu/shared/uoa_sweep.log 2>&1

# 9. 21:46 ICT: RULE-075 Stage 2 UOA #2 Conviction Gate & Accelerator (Mon-Fri)
46 21 * * 1-5 python3 /home/ubuntu/shared/order_fill_tracker.py --stage2 >> /home/ubuntu/shared/order_tracker.log 2>&1

# 10. 21:55 ICT: Anna Peak Hour Intelligence Telemetry Brief (Mon-Fri)
55 21 * * 1-5 python3 /home/ubuntu/shared/dispatch_peak_hour_brief.py >> /home/ubuntu/shared/peakhour.log 2>&1

# 11. 22:00 ICT: RULE-075 Stage 3 Hard Order Standoff Sweep (Mon-Fri)
00 22 * * 1-5 python3 /home/ubuntu/shared/order_fill_tracker.py --stage3 >> /home/ubuntu/shared/order_tracker.log 2>&1

# 11b. 20:30 ICT & 03:30 ICT: Dynamic Market Heartbeat Gatekeeper (RULE-074)
30 20 * * 1-5 python3 /home/ubuntu/shared/market_heartbeat_gate.py >> /home/ubuntu/shared/heartbeat_gate.log 2>&1
30 3 * * 2-6 python3 /home/ubuntu/shared/market_heartbeat_gate.py >> /home/ubuntu/shared/heartbeat_gate.log 2>&1

# 12. 20:30–03:30 ICT: 5-Minute FastHarvest Profit Poller (US Market Session)
30-59/5 20 * * 1-5 python3 /home/ubuntu/shared/auto_harvest_positions.py >> /home/ubuntu/shared/harvest.log 2>&1
*/5 21-23 * * 1-5 python3 /home/ubuntu/shared/auto_harvest_positions.py >> /home/ubuntu/shared/harvest.log 2>&1
*/5 0-2 * * 2-6 python3 /home/ubuntu/shared/auto_harvest_positions.py >> /home/ubuntu/shared/harvest.log 2>&1
0-30/5 3 * * 2-6 python3 /home/ubuntu/shared/auto_harvest_positions.py >> /home/ubuntu/shared/harvest.log 2>&1
"""

def install():
    print("============================================================")
    print("⚙️ INSTALLING MASTER 21:15 ICT AUTONOMOUS CRONTAB ON SERVER")
    print("============================================================")
    
    tmp_path = "/tmp/skonvault_golden_cron"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(CRON_LINES)
    
    try:
        subprocess.run(["crontab", tmp_path], check=True)
        print("  ✅ Crontab successfully installed and active on VPS!")
        print("\n📅 Active Production Schedule (All times in ICT / Asia/Bangkok):")
        print("  • 07:15 ICT -> Morning Daily Report & Ledger Audit (Tue-Sat)")
        print("  • 18:30 ICT -> Master Sunday Reset & Self-Healing Daemon (Sun)")
        print("  • 19:35 ICT -> Dynamic Quant Screener (Sun-Fri)")
        print("  • 19:40 ICT -> Pre-Market Intelligence Telegram Brief (Sun-Fri)")
        print("  • 21:10 ICT -> Institutional UOA Sweep #1 (Mon-Fri)")
        print("  • 21:15 ICT -> ⚡ MASTER 21:15 ICT GOLDEN ENTRY (Mon-Fri)")
        print("  • 21:30 ICT -> RULE-075 Stage 1 Order Fill Check & Micro-Nudge (Mon-Fri)")
        print("  • 21:45 ICT -> Institutional UOA Sweep #2 (Mon-Fri)")
        print("  • 21:46 ICT -> RULE-075 Stage 2 UOA #2 Conviction Gate & Accelerator (Mon-Fri)")
        print("  • 21:55 ICT -> Anna Peak Hour Intelligence Telegram Brief (Mon-Fri)")
        print("  • 22:00 ICT -> RULE-075 Stage 3 Hard Order Standoff Sweep (Mon-Fri)")
        print("  • 20:30-03:30 ICT -> FastHarvest 5-Minute Poller (Mon-Fri session)")
    except Exception as e:
        print(f"  🔴 Crontab installation error: {e}")
    finally:
        if os.path.exists(tmp_path): os.remove(tmp_path)

    print("============================================================")

if __name__ == "__main__":
    install()
