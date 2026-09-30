#!/usr/bin/env python3
"""
market_heartbeat_gate.py — Real-Time Market Clock & US Holiday Gatekeeper for OpenClaw Heartbeat

Enforces RULE-074 & US Exchange Market Hours Synchronization:
1. Verifies US Exchange Trading Days (Monday–Friday). Saturday & Sunday = 100% STAND DOWN.
2. Verifies US Exchange Holidays via Dual-Layer Detection (Alpaca /v2/clock + Fixed 2026 Holiday Calendar).
3. Verifies Regular Market Session Hours: 20:30 ICT to 03:30 ICT (09:30 AM to 04:00 PM US Eastern).
4. Dynamically Arms / Disarms OpenClaw HEARTBEAT.md:
   - When Market is CLOSED or HOLIDAY: Disarms HEARTBEAT.md (Zero actions, zero token waste).
   - When Market is OPEN: Arms HEARTBEAT.md with active portfolio audit instructions.
"""

import os
import sys
import json
import datetime
from pathlib import Path
from typing import Tuple, Dict, Any

SHARED_DIR = Path(__file__).resolve().parent
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

from alpaca_broker import AlpacaClient

def evaluate_market_status() -> Tuple[bool, str, Dict[str, Any]]:
    """
    Returns (is_active: bool, reason: str, metadata: dict).
    is_active is True ONLY when regular US options market is currently open.
    """
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_ict = now_utc + datetime.timedelta(hours=7)

    # US Eastern Time represents actual exchange location (America/New_York)
    try:
        from zoneinfo import ZoneInfo
        now_ny = datetime.datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now_ny = now_utc - datetime.timedelta(hours=4)

    ny_weekday = now_ny.weekday() # 0 = Monday, ..., 4 = Friday, 5 = Saturday, 6 = Sunday
    ny_time_minutes = now_ny.hour * 60 + now_ny.minute

    meta = {
        "timestamp_ict": now_ict.strftime("%Y-%m-%d %H:%M:%S ICT"),
        "timestamp_ny": now_ny.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "weekday_ny": now_ny.strftime("%A"),
        "is_weekend": False,
        "is_holiday": False,
        "holiday_name": "",
        "is_market_open": False
    }

    # 1. Weekend Check (Evaluated in US Eastern Time)
    if ny_weekday == 5: # Saturday in NY
        meta["is_weekend"] = True
        return False, "US Exchanges Closed for Weekend (Saturday in New York)", meta
    elif ny_weekday == 6: # Sunday in NY
        meta["is_weekend"] = True
        return False, "US Exchanges Closed for Weekend (Sunday in New York)", meta

    # 2. Time Window Check (US Regular Trading Hours: 09:30 to 16:00 ET)
    # 09:30 ET is 570 mins. 16:00 ET is 960 mins.
    is_in_hours = (9 * 60 + 30) <= ny_time_minutes < (16 * 60)
    if not is_in_hours:
        return False, f"Outside Regular US Market Trading Hours (Current ET: {now_ny.strftime('%H:%M %Z')} | Open: 09:30–16:00 ET / 20:30–03:00 ICT)", meta

    # 3. US Market Holiday Check (RULE-074)
    client = AlpacaClient("pion2_sub")
    is_holiday, holiday_name = client.is_market_holiday()
    if is_holiday:
        meta["is_holiday"] = True
        meta["holiday_name"] = holiday_name
        return False, f"US Market Holiday: {holiday_name} (All Exchanges 100% Closed)", meta

    # 4. Live Exchange Clock Verification via Alpaca /v2/clock
    is_live_open = client.is_market_open()
    meta["is_market_open"] = is_live_open
    if not is_live_open:
        return False, "Alpaca Live Clock confirms US exchange is currently closed", meta

    return True, "US Market is OPEN (Regular Trading Session Active)", meta

def sync_heartbeat_files(is_active: bool, reason: str, meta: Dict[str, Any]):
    """Synchronizes HEARTBEAT.md across all OpenClaw workspace paths."""
    now_str = meta.get("timestamp_ict", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT"))
    now_ny_str = meta.get("timestamp_ny", "")

    if is_active:
        heartbeat_content = f"""# 🟢 HEARTBEAT — ACTIVE US TRADING SESSION
**Status:** MARKET OPEN ✅
**Last Verified:** {now_str}
**Regime:** Live Market Active (09:30–16:00 ET / 20:30–03:30 ICT)

## Instructions:
1. FastHarvest Poller: Check active credit spreads for 50%-60% profit threshold.
2. Margin & Risk Guard: Confirm buying power reserve >= 35% (RULE-072).
3. If no spreads meet harvest criteria, reply: `HEARTBEAT_OK_MONITORING`.
"""
    else:
        heartbeat_content = f"""# 🛑 HEARTBEAT — STANDBY (US Market Closed)
**Status:** STAND DOWN (RULE-074 Market Gatekeeper Active)
**Last Verified:** {now_str}
**Reason:** {reason}

## Directives:
• US options exchanges are 100% CLOSED.
• ZERO trades, ZERO orders, and ZERO background tool evaluations authorized.
• Output strictly: `HEARTBEAT_OK_STAND_DOWN_MARKET_CLOSED`.
"""

    target_dirs = set()

    # 1. Base known paths
    base_candidates = [
        Path("/home/ubuntu/.openclaw"),
        Path("/home/ubuntu/.openclaw/workspace"),
        Path("/home/ubuntu/.openclaw/workspace/memory"),
        Path("/home/ubuntu/.openclaw/agents/main/agent"),
        Path("/home/ubuntu/.openclaw/agents/main"),
        Path("/home/ubuntu/openclaw"),
        Path("/home/ubuntu/openclaw/workspace"),
        Path("/home/ubuntu/openclaw-vault"),
        Path("/home/ubuntu/openclaw-vault/shared"),
        Path("/home/ubuntu/openclaw-vault/workspace"),
        Path("/home/ubuntu/shared"),
        SHARED_DIR
    ]
    for p in base_candidates:
        if p.exists() and p.is_dir():
            target_dirs.add(p.resolve())

    # 2. Dynamic discovery of all agent subdirectories under ~/.openclaw/agents
    agents_root = Path("/home/ubuntu/.openclaw/agents")
    if agents_root.exists():
        try:
            for agent_dir in agents_root.iterdir():
                if agent_dir.is_dir():
                    target_dirs.add(agent_dir.resolve())
                    for sub in ("agent", "workspace", "sessions", "memory"):
                        sub_p = agent_dir / sub
                        if sub_p.exists() and sub_p.is_dir():
                            target_dirs.add(sub_p.resolve())
        except Exception:
            pass

    # 3. Exhaustive filesystem search for ANY existing HEARTBEAT.md files
    search_roots = []
    if Path("/home/ubuntu").exists():
        search_roots.append(Path("/home/ubuntu"))
    if SHARED_DIR.parent.exists() and SHARED_DIR.parent != Path("/home/ubuntu"):
        search_roots.append(SHARED_DIR.parent)

    skip_dirs = {".git", "node_modules", ".cache", ".local", "venv", "__pycache__", ".npm", "Library", "Applications"}
    for sr in search_roots:
        try:
            for root, dirs, files in os.walk(sr):
                dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
                if "HEARTBEAT.md" in files:
                    target_dirs.add(Path(root).resolve())
        except Exception:
            pass

    updated_files = []
    for d in sorted(target_dirs, key=lambda x: str(x)):
        hb_file = d / "HEARTBEAT.md"
        try:
            hb_file.write_text(heartbeat_content, encoding="utf-8")
            updated_files.append(str(hb_file))
        except Exception:
            pass

    # 4. Synchronize OpenClaw v2026.9+ Cron Scratch (Database & CLI)
    scratch_updated = sync_openclaw_cron_scratch(heartbeat_content)

    status_icon = "🟢 ARMED (Market Open)" if is_active else "🛑 DISARMED (Market Closed)"
    print(f"[{now_str}] Gatekeeper: {status_icon} -> {reason} ({len(updated_files)} HEARTBEAT.md files updated, scratch: {'✅ updated' if scratch_updated else 'n/a'})")
    for uf in updated_files:
        print(f"  • {uf}")

def sync_openclaw_cron_scratch(heartbeat_content: str) -> bool:
    """Updates OpenClaw 2026.9+ database cron_job_scratch table and live CLI scratch."""
    import sqlite3
    import time
    import subprocess
    
    db_path = Path("/home/ubuntu/.openclaw/state/openclaw.sqlite")
    if not db_path.exists():
        return False

    job_id = "8e8859d2-b4f1-4719-af20-9d636a76a9e1"
    try:
        conn = sqlite3.connect(str(db_path), timeout=10.0)
        cur = conn.cursor()
        cur.execute("SELECT job_id FROM cron_jobs WHERE declaration_key = 'heartbeat:main' OR name = 'heartbeat-main'")
        row = cur.fetchone()
        if row and row[0]:
            job_id = row[0]
            
        now_ms = int(time.time() * 1000)
        cur.execute("""
            INSERT INTO cron_job_scratch (store_key, job_id, content, revision, updated_at_ms)
            VALUES ('/home/ubuntu/.openclaw/cron/jobs.json', ?, ?, 1, ?)
            ON CONFLICT(store_key, job_id) DO UPDATE SET
                content = excluded.content,
                revision = revision + 1,
                updated_at_ms = excluded.updated_at_ms
        """, (job_id, heartbeat_content, now_ms))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Warning: Failed to update cron_job_scratch directly in SQLite: {e}")

    # Also notify running OpenClaw gateway via CLI if available
    try:
        node_bin = "/home/ubuntu/.nvm/versions/node/v24.16.0/bin/node"
        openclaw_bin = "/home/ubuntu/.nvm/versions/node/v24.16.0/bin/openclaw"
        if os.path.exists(node_bin) and os.path.exists(openclaw_bin):
            subprocess.run(
                [node_bin, openclaw_bin, "cron", "scratch", job_id, "--set", heartbeat_content],
                check=False,
                timeout=15,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
    except Exception:
        pass

    return True

def main():
    is_active, reason, meta = evaluate_market_status()
    sync_heartbeat_files(is_active, reason, meta)

if __name__ == "__main__":
    main()
