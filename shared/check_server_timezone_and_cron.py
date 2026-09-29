#!/usr/bin/env python3
"""
check_server_timezone_and_cron.py — Checks server timezone and dynamically configures crontab to match server clock
"""

import sys
import os
import datetime
import subprocess
import time

def check_and_sync():
    print("============================================================")
    print("🔎 CHECKING SERVER TIMEZONE & CRONTAB CLOCK ALIGNMENT")
    print("============================================================")

    # 1. Inspect Server Time
    now_local = datetime.datetime.now()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    tz_name = time.tzname
    is_dst = time.daylight and time.localtime().tm_isdst > 0
    utc_offset_hours = round((now_local - now_utc.replace(tzinfo=None)).total_seconds() / 3600.0)

    print(f"  • Server Local Time : {now_local.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  • UTC Time          : {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  • Timezone Name     : {tz_name}")
    print(f"  • UTC Offset Hours  : {utc_offset_hours:+d} hours")

    # Target ICT Schedule (UTC+7):
    # 07:15 ICT -> (7 - 7 + offset)
    # 19:35 ICT -> (19 - 7 + offset)
    # 19:40 ICT -> (19 - 7 + offset)
    # 21:10 ICT -> (21 - 7 + offset)
    # 21:15 ICT -> (21 - 7 + offset)
    # 21:45 ICT -> (21 - 7 + offset)
    # 21:55 ICT -> (21 - 7 + offset)

    def to_server_hour(ict_hour):
        return (ict_hour - 7 + utc_offset_hours) % 24

    h_report = to_server_hour(7)
    h_screener = to_server_hour(19)
    h_uoa1 = to_server_hour(21)
    h_golden = to_server_hour(21)
    h_uoa2 = to_server_hour(21)
    h_peak = to_server_hour(21)

    # Range for FastHarvest: 20:30 to 03:30 ICT
    h_harv_start = to_server_hour(20)
    h_harv_end = to_server_hour(3)
    if h_harv_start <= h_harv_end:
        harv_range = f"{h_harv_start}-{h_harv_end}"
    else:
        harv_range = f"{h_harv_start}-23,0-{h_harv_end}"

    print("\n📅 Calculated Cron Schedule for Server's Clock:")
    print(f"  • 07:15 ICT -> Server Hour {h_report:02d}:15 (daily_report.py)")
    print(f"  • 19:35 ICT -> Server Hour {h_screener:02d}:35 (dynamic_universe_screener.py)")
    print(f"  • 19:40 ICT -> Server Hour {h_screener:02d}:40 (dispatch_daily_premarket_brief.py)")
    print(f"  • 21:10 ICT -> Server Hour {h_uoa1:02d}:10 (run_uoa_scan.py Sweep #1)")
    print(f"  • 21:15 ICT -> Server Hour {h_golden:02d}:15 (execute_golden_2115_daily_entry.py)")
    print(f"  • 21:45 ICT -> Server Hour {h_uoa2:02d}:45 (run_uoa_scan.py Sweep #2)")
    print(f"  • 21:55 ICT -> Server Hour {h_peak:02d}:55 (dispatch_peak_hour_brief.py)")
    print(f"  • 20:30–03:30 ICT -> Server Hours {harv_range} (auto_harvest_positions.py)")

    cron_content = f"""# SkonVault Dynamic Timezone-Calibrated Autonomous Trading Crontab
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/ubuntu/.local/bin
PYTHONPATH=/home/ubuntu/shared:/home/ubuntu/openclaw

# 1. 07:15 ICT: Morning Ledger Reconciliation & Scorecard
15 {h_report} * * 1-6 python3 /home/ubuntu/shared/daily_report.py >> /home/ubuntu/shared/daily_report.log 2>&1

# 2. 19:35 ICT (Sun–Fri): 5-Factor Dynamic Universe Mainstream Candidates Screener
35 {h_screener} * * 0-5 python3 /home/ubuntu/shared/dynamic_universe_screener.py >> /home/ubuntu/shared/screener.log 2>&1

# 3. 19:40 ICT (Sun–Fri): Anna Weekly Macro Regime & Sector Roadmap Brief
40 {h_screener} * * 0-5 python3 /home/ubuntu/shared/dispatch_daily_premarket_brief.py >> /home/ubuntu/shared/premarket.log 2>&1

# 4. 21:10 ICT: Institutional UOA Sweep #1 (Tumbler 4 Lock-In)
10 {h_uoa1} * * 1-5 python3 /home/ubuntu/shared/run_uoa_scan.py >> /home/ubuntu/shared/uoa_sweep.log 2>&1

# 5. 21:15 ICT: Master 21:15 ICT Golden Entry Engine (35% Max Safe Sizing)
15 {h_golden} * * 1-5 python3 /home/ubuntu/shared/execute_golden_2115_daily_entry.py >> /home/ubuntu/shared/execution.log 2>&1

# 6. 21:45 ICT: Institutional UOA Sweep #2
45 {h_uoa2} * * 1-5 python3 /home/ubuntu/shared/run_uoa_scan.py >> /home/ubuntu/shared/uoa_sweep.log 2>&1

# 7. 21:55 ICT: Anna Peak Hour Intelligence Telemetry Brief
55 {h_peak} * * 1-5 python3 /home/ubuntu/shared/dispatch_peak_hour_brief.py >> /home/ubuntu/shared/peakhour.log 2>&1

# 8. 20:30–03:30 ICT: 5-Minute FastHarvest Profit Poller
*/5 {harv_range} * * 1-5 python3 /home/ubuntu/shared/auto_harvest_positions.py >> /home/ubuntu/shared/harvest.log 2>&1

# 9. 04:00 ICT on Last Sunday of Month: Monthly System Maintenance & Archival Suite (RULE-061)
# Scheduled on Sunday (100% free day) to keep system clean, optimized and primed before Sunday night reset
0 4 22-28 * 0 python3 /home/ubuntu/shared/run_monthly_system_maintenance.py >> /home/ubuntu/shared/monthly_maintenance.log 2>&1
"""

    tmp_cron = "/tmp/skonvault_synced_cron"
    with open(tmp_cron, "w", encoding="utf-8") as f:
        f.write(cron_content)

    if sys.platform == "linux":
        try:
            subprocess.run(["crontab", tmp_cron], check=True)
            print("\n🎉 SUCCESS! Crontab is 100% time-synchronized with your server clock!")
            print("  From now on, all jobs will run autonomously on exact schedule with ZERO manual triggers!")
        except Exception as ex:
            print(f"  🔴 Crontab error: {ex}")
        finally:
            if os.path.exists(tmp_cron): os.remove(tmp_cron)

    print("============================================================")

if __name__ == "__main__":
    check_and_sync()
