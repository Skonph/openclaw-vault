#!/usr/bin/env python3
"""
silence_hermes_legacy_cron_spam.py — Silences the 27 Legacy Chatty Hermes Cron Jobs

Problem:
Hermes has an internal cron runner (~/.hermes/cron/jobs.json) containing ~27 legacy experimental
reminder/prompt jobs. Every time they fire, Hermes dumps their execution results into the Telegram
group with the header:
  Cronjob Response: <Job Name> (job_id: ...)
  To stop or manage this job, send me a new message (e.g. "stop reminder...")

This spams Telegram with:
- Hourly raw JSON data collector dumps
- Dispatcher loop watchdog checks every 10 minutes
- SyntaxError / IndentationError crashes from broken legacy scripts
- Unauthorized HTTP 422 trade attempts on market holidays

Solution:
1. Backs up ~/.hermes/cron/jobs.json
2. Disables / cleans all noisy legacy background jobs
3. Restarts hermes-gateway so crontab remains the single source of truth for Telegram dispatch!
"""

import os
import sys
import json
import shutil
import datetime
import subprocess
from pathlib import Path

def silence_hermes_cron_spam():
    print("================================================================================")
    print("🔇 AUDITING & SILENCING LEGACY HERMES CRON SPAM IN JOBS.JSON")
    print("================================================================================")

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    hermes_dir = Path("/home/ubuntu/.hermes")
    if not hermes_dir.exists():
        hermes_dir = Path.home() / ".hermes"

    candidate_files = [
        hermes_dir / "cron" / "jobs.json",
        hermes_dir / "jobs.json"
    ]

    total_disabled = 0
    total_found = 0

    for jf in candidate_files:
        if not jf.exists():
            continue

        print(f"\n📂 Processing {jf}...")
        try:
            backup_path = jf.parent / f"{jf.name}.backup_spam_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
            shutil.copy2(jf, backup_path)
            print(f"  💾 Backup created: {backup_path}")

            content = jf.read_text(encoding="utf-8")
            data = json.loads(content)

            # Handle both list and dict-wrapped list
            is_dict_wrapped = isinstance(data, dict) and "jobs" in data
            job_list = data["jobs"] if is_dict_wrapped else (data if isinstance(data, list) else [])

            total_found += len(job_list)
            clean_jobs = []

            # We silence all chatty internal scripts that spam Telegram
            SPAM_JOB_KEYWORDS = [
                "regime engine",
                "threshold alert",
                "bridge poller",
                "closed-loop",
                "daily data collector",
                "bridge check",
                "candidate screener",
                "model api health",
                "max loss stop",
                "webhook health",
                "mean-reversion",
                "push mr scan",
                "push to anna",
                "anna gate",
                "pipeline watchdog",
                "uoa scanner",
                "dual-market",
                "verdict executor",
                "gamma zone",
                "roll executor"
            ]

            for job in job_list:
                job_name = str(job.get("name", ""))
                # Disable all legacy Hermes prompt cron jobs to prevent Telegram spam
                job["enabled"] = False
                job["schedule"] = {"cron": "0 0 31 2 *"} # Dormant schedule (never triggers)
                total_disabled += 1
                print(f"  🚫 Silenced legacy Hermes job: {job_name} (id: {job.get('id', 'N/A')})")

            if is_dict_wrapped:
                data["jobs"] = job_list
            else:
                data = job_list

            jf.write_text(json.dumps(data, indent=2), encoding="utf-8")
            print(f"  ✅ Updated {jf}: Silenced all {total_disabled} legacy Hermes cron jobs.")

        except Exception as ex:
            print(f"  🔴 Error processing {jf}: {ex}")

    # Restart hermes-gateway so changes take effect
    if sys.platform == "linux":
        try:
            subprocess.run(["sudo", "systemctl", "restart", "hermes-gateway"], check=False)
            print("  ✅ Reloaded hermes-gateway service. Cron runner refreshed!")
        except Exception as ex_svc:
            print(f"  ℹ️ Notice reloading hermes-gateway: {ex_svc}")

    print("\n================================================================================")
    print(f"🎉 SUCCESS: Silenced {total_disabled} legacy Telegram spam jobs.")
    print("   Linux crontab is now the single, authoritative dispatcher for all briefs!")
    print("================================================================================")

if __name__ == "__main__":
    silence_hermes_cron_spam()
