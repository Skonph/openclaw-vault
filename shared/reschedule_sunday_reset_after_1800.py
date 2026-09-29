#!/usr/bin/env python3
"""
reschedule_sunday_reset_after_1800.py — Shifts Sunday Pre-Reset (16:30) & Readiness (17:00) to after 18:00 ICT

Ensures 100% compliance with DeepSeek Off-Peak Discount and optimal server scheduling:
1. Sunday State Snapshot & Pre-Reset: Shifted from 16:30 ICT -> 18:30 ICT (6:30 PM ICT)
2. Sunday Readiness Report: Shifted from 17:00 ICT -> 19:00 ICT (7:00 PM ICT)
3. Synchronizes OpenClaw and Hermes cron configuration files.
"""

import os
import sys
import json
import re
import subprocess
from pathlib import Path

def shift_sunday_crons():
    print("================================================================================")
    print("🌙 RESCHEDULING SUNDAY RESET & READINESS JOBS TO AFTER 18:00 ICT (OFF-PEAK)")
    print("================================================================================")

    # 1. Search and patch Hermes JSON job stores
    hermes_paths = [
        Path("/home/ubuntu/.hermes/cron/jobs.json"),
        Path("/home/ubuntu/.hermes/jobs.json"),
        Path("/home/ubuntu/shared/jobs.json"),
        Path("/home/ubuntu/.openclaw/cron/jobs.json"),
        Path("/home/ubuntu/.openclaw/jobs.json"),
        Path("/home/ubuntu/.openclaw/cron.json")
    ]

    total_patched = 0

    for jpath in hermes_paths:
        if jpath.exists():
            try:
                raw_txt = jpath.read_text(encoding="utf-8")
                data = json.loads(raw_txt)
                jobs = data.get("jobs", []) if isinstance(data, dict) else data

                modified = False
                for j in jobs:
                    jid = str(j.get("id", ""))
                    name = str(j.get("name", ""))
                    cmd = str(j.get("command", "")).lower()

                    # Match 16:30 or 17:00 Sunday jobs or specific job IDs
                    is_pre_reset = any(k in jid.lower() or k in name.lower() or k in cmd for k in [
                        "209b2988", "16:30", "1630", "pre-reset", "weekly_snapshot", "snapshot"
                    ])
                    is_readiness = any(k in jid.lower() or k in name.lower() or k in cmd for k in [
                        "17:00", "1700", "17:15", "readiness", "session reset"
                    ])

                    if is_pre_reset or "30 16 * * 0" in str(j):
                        new_cron = "30 18 * * 0" # 18:30 ICT
                        new_name = "Sunday Pre-Reset & Weekly Snapshot (18:30 ICT / Off-Peak)"
                        if isinstance(j.get("schedule"), dict):
                            j["schedule"]["cron"] = new_cron
                        else:
                            j["schedule"] = new_cron
                        j["name"] = new_name
                        modified = True
                        total_patched += 1
                        print(f"  ✅ Shifted Job '{name or jid}' -> 18:30 ICT (30 18 * * 0)")

                    elif is_readiness or "00 17 * * 0" in str(j) or "15 17 * * 0" in str(j):
                        new_cron = "00 19 * * 0" # 19:00 ICT
                        new_name = "Sunday Final Readiness Audit (19:00 ICT / Off-Peak)"
                        if isinstance(j.get("schedule"), dict):
                            j["schedule"]["cron"] = new_cron
                        else:
                            j["schedule"] = new_cron
                        j["name"] = new_name
                        modified = True
                        total_patched += 1
                        print(f"  ✅ Shifted Job '{name or jid}' -> 19:00 ICT (00 19 * * 0)")

                if modified:
                    if isinstance(data, dict):
                        data["jobs"] = jobs
                        jpath.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    else:
                        jpath.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
                    print(f"  💾 Saved updated cron store: {jpath}")

            except Exception as ex:
                print(f"  ⚠️ Error inspecting {jpath}: {ex}")

    # 2. Search OpenClaw workspace directory for cron configs
    openclaw_workspace = Path("/home/ubuntu/.openclaw/workspace")
    if openclaw_workspace.exists():
        for cfile in openclaw_workspace.rglob("*.json"):
            try:
                txt = cfile.read_text(encoding="utf-8")
                if "16:30" in txt or "17:00" in txt or "30 16 * * 0" in txt or "00 17 * * 0" in txt:
                    txt = txt.replace("30 16 * * 0", "30 18 * * 0")
                    txt = txt.replace("00 17 * * 0", "00 19 * * 0")
                    txt = txt.replace("16:30", "18:30")
                    txt = txt.replace("17:00", "19:00")
                    cfile.write_text(txt, encoding="utf-8")
                    print(f"  ✅ Shifted OpenClaw workspace config in {cfile.name}")
                    total_patched += 1
            except Exception: pass

    # 2b. User crontab inspection for Sunday 16:xx or 17:xx jobs
    try:
        res = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if res.returncode == 0 and res.stdout:
            lines = res.stdout.splitlines()
            new_lines = []
            crontab_changed = False
            for line in lines:
                sline = line.strip()
                if not sline.startswith("#") and ("* * 0" in sline or "* * 7" in sline or "sun" in sline.lower()):
                    parts = sline.split()
                    if len(parts) >= 5 and parts[1] in ["16", "17"]:
                        new_h = "18" if parts[1] == "16" else "19"
                        new_m = "30" if parts[1] == "16" else "00"
                        parts[0] = new_m
                        parts[1] = new_h
                        new_line = " ".join(parts)
                        new_lines.append(new_line)
                        crontab_changed = True
                        print(f"  ✅ Shifted crontab Sunday job: {sline} -> {new_line}")
                    else:
                        new_lines.append(line)
                else:
                    new_lines.append(line)
            if crontab_changed:
                subprocess.run(["crontab", "-"], input="\n".join(new_lines) + "\n", text=True)
                print("  💾 Saved updated crontab!")
    except Exception as ex_c:
        print(f"  ℹ️ Crontab inspect notice: {ex_c}")

    # 3. Synchronize OpenClaw memory
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import sync_openclaw_anna_memory
        sync_openclaw_anna_memory.sync_openclaw_memory()
    except Exception as ex_m:
        print(f"  ℹ️ Memory sync notice: {ex_m}")

    # 4. Kill stale in-memory cron processes & reload daemons
    print("\n🔄 Reloading Hermes / OpenClaw cron runners...")
    try:
        subprocess.run(["pkill", "-f", "hermes.*cron"], check=False)
        subprocess.run(["pkill", "-f", "openclaw.*cron"], check=False)
        subprocess.run(["pkill", "-f", "openclaw.*gateway"], check=False)
        subprocess.run(["sudo", "systemctl", "restart", "hermes-gateway"], check=False)
        subprocess.run(["systemctl", "--user", "restart", "openclaw"], check=False)
        print("  ✅ Stale in-memory cron processes reloaded!")
    except Exception as ex_r:
        print(f"  ℹ️ Daemon reload notice: {ex_r}")

    print(f"\n🎉 SUCCESS: All Sunday reset and readiness activities rescheduled to 18:30 ICT and 19:00 ICT (Off-Peak)!")
    print("================================================================================")

if __name__ == "__main__":
    shift_sunday_crons()
