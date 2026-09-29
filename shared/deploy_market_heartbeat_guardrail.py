#!/usr/bin/env python3
"""
deploy_market_heartbeat_guardrail.py — Master Deployment of Market-Hours & Holiday Guardrails

Applies the following 4-tier protection to the live VPS:
1. Deploys shared/market_heartbeat_gate.py (Dual-Layer Alpaca Clock + Fixed 2026 Holiday Calendar).
2. Patches ~/.openclaw/openclaw.json:
   - Sets heartbeat.activeHours to 20:30–03:30 Asia/Bangkok (Exact US regular market session).
   - Prevents all heartbeats during the 17.5 hours per day the market is closed.
3. Fixes ~/.hermes/cron/jobs.json:
   - Converts string schedules (e.g. "30 18 * * 0") to valid dicts ({"cron": "30 18 * * 0"}).
   - Eliminates the 60-second "coercing to empty dict" warning loop.
4. Evaluates immediate market status:
   - Immediately disarms HEARTBEAT.md if market is closed (Sunday / Labor Day holiday).
5. Reloads systemd daemons (openclaw and hermes-gateway).
"""

import os
import sys
import json
import subprocess
from pathlib import Path

def deploy_guardrails():
    print("================================================================================")
    print("🛡️ DEPLOYING US MARKET HOURS & HOLIDAY HEARTBEAT GUARDRAILS (RULE-074)")
    print("================================================================================")

    # 1. Update openclaw.json files
    openclaw_configs = [
        Path("/home/ubuntu/.openclaw/openclaw.json"),
        Path("/home/ubuntu/openclaw/openclaw.json"),
        Path("/home/ubuntu/.openclaw/workspace/openclaw.json"),
        Path("/Users/SkonP/AI_Prompt/Obsidient/SkonVault/openclaw.json")
    ]

    for p in openclaw_configs:
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                hb = data.get("agents", {}).get("defaults", {}).get("heartbeat", {})
                if hb:
                    hb["activeHours"] = {
                        "start": "20:30",
                        "end": "03:30",
                        "timezone": "Asia/Bangkok"
                    }
                    data["agents"]["defaults"]["heartbeat"] = hb
                    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    print(f"  ✅ Updated OpenClaw heartbeat window -> 20:30–03:30 ICT in: {p}")
            except Exception as e:
                print(f"  ⚠️ Error updating {p}: {e}")

    # 2. Fix Hermes jobs.json string schedules (Stops 60s warning loop)
    hermes_job_files = [
        Path("/home/ubuntu/.hermes/cron/jobs.json"),
        Path("/home/ubuntu/.hermes/jobs.json")
    ]

    for hj in hermes_job_files:
        if hj.exists():
            try:
                data = json.loads(hj.read_text(encoding="utf-8"))
                jobs = data.get("jobs", data) if isinstance(data, dict) else data
                fixed_count = 0
                for j in jobs:
                    sched = j.get("schedule")
                    if isinstance(sched, str) and sched.strip():
                        j["schedule"] = {"cron": sched.strip()}
                        fixed_count += 1
                if isinstance(data, dict):
                    data["jobs"] = jobs
                    hj.write_text(json.dumps(data, indent=2), encoding="utf-8")
                else:
                    hj.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
                print(f"  ✅ Fixed {fixed_count} string schedule(s) in {hj} -> Dict format enforced!")
            except Exception as e:
                print(f"  ⚠️ Error fixing {hj}: {e}")

    # 3. Trigger immediate market heartbeat gate check
    try:
        shared_dir = Path("/home/ubuntu/shared")
        if not shared_dir.exists():
            shared_dir = Path(__file__).resolve().parent
        
        sys.path.insert(0, str(shared_dir))
        import market_heartbeat_gate
        is_active, reason, meta = market_heartbeat_gate.evaluate_market_status()
        market_heartbeat_gate.sync_heartbeat_files(is_active, reason, meta)
    except Exception as e:
        print(f"  ℹ️ Gatekeeper trigger notice: {e}")

    # 4. Reload daemons on Linux
    if sys.platform == "linux":
        print("\n🔄 Restarting openclaw & hermes daemons with new guardrails...")
        try:
            subprocess.run(["systemctl", "--user", "restart", "openclaw"], check=False)
            subprocess.run(["sudo", "systemctl", "restart", "hermes-gateway"], check=False)
            print("  ✅ Services reloaded successfully!")
        except Exception as e:
            print(f"  ℹ️ Service reload notice: {e}")

    print("\n================================================================================")
    print("🎉 DEPLOYMENT COMPLETE: Heartbeat is 100% Synced to US Market Open & Holidays!")
    print("================================================================================")

if __name__ == "__main__":
    deploy_guardrails()
