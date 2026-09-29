#!/usr/bin/env python3
"""
daily_state_backup.py — Automated Daily Vault State Snapshot & Disaster Recovery Daemon
Runs at 03:35 ICT (Post-Market Close) to capture a compact, immutable snapshot of:
- SQLite Message Bus (bridge.db)
- Active Trades Ledger (active_trades.json)
- Market Context (market_context.json)
- Excel Transaction Journals (*.xlsx)
- Environment Credentials (.env)

Retention: Automatically preserves rolling 7-day backups; prunes older snapshots.
"""

import os
import sys
import tarfile
import datetime
import glob
from pathlib import Path

BACKUP_DIR = Path("/home/ubuntu/backups")
SHARED_DIR = Path("/home/ubuntu/shared")
OPENCLAW_DIR = Path("/home/ubuntu/openclaw")
RETENTION_DAYS = 7

def create_daily_snapshot():
    # If running locally on Mac, adapt paths
    global BACKUP_DIR, SHARED_DIR, OPENCLAW_DIR
    if not BACKUP_DIR.parent.exists():
        BACKUP_DIR = Path(__file__).parent.parent / "backups"
        SHARED_DIR = Path(__file__).parent
        OPENCLAW_DIR = Path(__file__).parent

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    archive_name = BACKUP_DIR / f"skonvault_state_snapshot_{today_str}.tar.gz"

    files_to_backup = [
        SHARED_DIR / "bridge.db",
        SHARED_DIR / "active_trades.json",
        SHARED_DIR / "market_context.json",
        SHARED_DIR / "wyckoff_signals.json",
        SHARED_DIR / "sma20_slope_signals.json",
        SHARED_DIR / "SkonVault_Live_Transaction_Journal.xlsx",
        SHARED_DIR / "SkonVault_Transaction_Journal.xlsx",
        SHARED_DIR / ".env",
        OPENCLAW_DIR / ".env",
    ]

    existing_files = [f for f in files_to_backup if f.exists()]

    if not existing_files:
        print("  ⚠️ No state files found to backup.")
        return False

    with tarfile.open(archive_name, "w:gz") as tar:
        for f in existing_files:
            arcname = f.name
            if f.parent == OPENCLAW_DIR and f.name == ".env":
                arcname = "openclaw.env"
            tar.add(f, arcname=arcname)

    archive_size_kb = archive_name.stat().st_size / 1024.0
    print(f"  ✅ Daily Vault Snapshot created: {archive_name.name} ({archive_size_kb:.1f} KB, {len(existing_files)} files)")

    # Rolling Prune: Remove snapshots older than RETENTION_DAYS
    cutoff_date = datetime.date.today() - datetime.timedelta(days=RETENTION_DAYS)
    pruned_count = 0
    for p in glob.glob(str(BACKUP_DIR / "skonvault_state_snapshot_*.tar.gz")):
        path = Path(p)
        try:
            date_part = path.name.replace("skonvault_state_snapshot_", "").replace(".tar.gz", "")
            f_date = datetime.datetime.strptime(date_part, "%Y-%m-%d").date()
            if f_date < cutoff_date:
                path.unlink()
                pruned_count += 1
                print(f"  🧹 Pruned expired snapshot: {path.name}")
        except Exception:
            pass

    print(f"  📦 Rolling Retention Active: Kept {len(list(BACKUP_DIR.glob('*.tar.gz')))} snapshots (Pruned {pruned_count} expired).")
    return True

if __name__ == "__main__":
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 💾 RUNNING DAILY STATE SNAPSHOT & PRUNING DAEMON")
    create_daily_snapshot()
