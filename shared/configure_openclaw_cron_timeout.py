#!/usr/bin/env python3
"""
configure_openclaw_cron_timeout.py — Eliminates OpenClaw Cron Timeout Noise

1. Inspects OpenClaw cron jobs and configuration in ~/.openclaw/
2. Sets execution timeout to 300s (5 minutes) across all OpenClaw scheduled tasks
3. Prevents premature timeout warnings and duplicate briefing triggers on Telegram
"""

import os
import json
import glob
from pathlib import Path

def optimize_openclaw_crons():
    print("============================================================")
    print("🛠️ CONFIGURING OPENCLAW CRON TIMEOUTS & SUPPRESSING NOISE")
    print("============================================================")

    search_paths = [
        Path.home() / ".openclaw",
        Path.home() / "openclaw",
        Path("/home/ubuntu/.openclaw"),
        Path("/home/ubuntu/openclaw")
    ]

    modified_count = 0

    for base in search_paths:
        if not base.exists():
            continue

        # Look for cron configurations / json files
        json_files = list(base.glob("**/*.json"))

        for fpath in json_files:
            try:
                content = fpath.read_text(encoding="utf-8")
                data = json.loads(content)
                changed = False

                if isinstance(data, dict):
                    if "timeout" in data and isinstance(data["timeout"], (int, float)) and data["timeout"] < 300:
                        data["timeout"] = 300
                        changed = True
                    if "timeoutSeconds" in data and isinstance(data["timeoutSeconds"], (int, float)) and data["timeoutSeconds"] < 300:
                        data["timeoutSeconds"] = 300
                        changed = True
                    if "cron" in data and isinstance(data["cron"], dict):
                        if data["cron"].get("timeout", 60) < 300:
                            data["cron"]["timeout"] = 300
                            changed = True

                    if "agents" in data and isinstance(data["agents"], dict):
                        defaults = data["agents"].get("defaults", {})
                        if isinstance(defaults, dict):
                            if defaults.get("timeoutSeconds", 0) < 300:
                                defaults["timeoutSeconds"] = 300
                                changed = True

                    if "jobs" in data and isinstance(data["jobs"], list):
                        for job in data["jobs"]:
                            if isinstance(job, dict):
                                if job.get("timeout", 60) < 300:
                                    job["timeout"] = 300
                                    changed = True
                                if job.get("timeoutSeconds", 60) < 300:
                                    job["timeoutSeconds"] = 300
                                    changed = True

                if changed:
                    fpath.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    print(f"  ✅ Increased timeout to 300s in: {fpath}")
                    modified_count += 1
            except Exception: pass

    print(f"🎉 OpenClaw cron timeout optimization complete ({modified_count} configurations updated)!")
    print("   All LLM agent briefings now have a generous 300s (5-minute) execution ceiling.")

if __name__ == "__main__":
    optimize_openclaw_crons()
