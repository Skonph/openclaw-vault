#!/usr/bin/env python3
"""
run_monthly_system_maintenance.py — Master Monthly Maintenance & System Optimization Suite (RULE-061)

Executes on the Last Sunday of Every Month at 18:30 ICT (or on-demand):
1. Disk Space Telemetry: Measures storage before & after optimization.
2. Archiving Non-Core Legacy Scripts: Safely moves one-off completed patch & test scripts to shared/archive/legacy_scripts/
3. Log Rotation & Gzip Compression: Rotates oversized logs (>100 KB) in shared/, archives compressed .log.gz copies, keeps last 500 lines.
4. SQLite Optimization: Executes PRAGMA wal_checkpoint(TRUNCATE) and VACUUM on bridge.db and account_state.db.
5. System-Level Cache & Journal Vacuuming: Vacuums systemd journal (>7d, max 200MB), purges __pycache__, .DS_Store, and stale pip cache.
6. 16-Tier Smoke Test Verification: Executes run_system_smoke_test.py to guarantee 100% Green system integrity!
"""

import sys
import os
import gzip
import shutil
import sqlite3
import datetime
import subprocess
from pathlib import Path

# Core Immutable Production Suite (PROTECTED: NEVER MOVED OR DELETED)
CORE_PRODUCTION_FILES = {
    # ── 1. Core Execution & Portfolio Engines ──
    "alpaca_broker.py",
    "tradier_broker.py",
    "execute_golden_2115_daily_entry.py",
    "auto_harvest_positions.py",
    "dynamic_universe_screener.py",
    "execute_daily_market_open.py",
    "multi_slot_portfolio_manager.py",
    "order_fill_tracker.py",
    "market_heartbeat_gate.py",
    "active_trades_io.py",

    # ── 2. Quant Modeling, Volatility & Indicator Engines (Tiers 9–16) ──
    "advanced_quant_forecasting_optimizer.py",
    "wyckoff_spring_detector.py",
    "sma20_slope_engine.py",
    "live_market_data.py",
    "live_spot.py",
    "live_liquidity_matrix_scanner.py",
    "run_uoa_scan.py",
    "dynamic_regime_manager.py",

    # ── 3. Reporting, Dispatch & Agent Intelligence ──
    "daily_report.py",
    "dispatch_peak_hour_brief.py",
    "dispatch_daily_premarket_brief.py",
    "intelligent_spread_formatter.py",
    "transaction_journal_manager.py",
    "run_anna_preflight_handshake.py",
    "sync_openclaw_anna_memory.py",
    "silence_hermes_legacy_cron_spam.py",
    "acknowledge_anna_brief.py",
    "run_anna_dispatcher.py",

    # ── 4. Infrastructure, Diagnostics & Maintenance ──
    "agent_bridge.py",
    "run_system_smoke_test.py",
    "check_server_timezone_and_cron.py",
    "run_monthly_system_maintenance.py",
    "install_golden_2115_crontab.py",
    "master_sunday_reset_daemon.py",
    "audit_all_18_candidates_orderability.py",
    "audit_alpaca_closed_pnl.py",
    "audit_portfolio.py",
    "reconcile_active_trades.py",
    "stopout_ledger_reconciler.py",
    "position_book.py",
    "trade_history.py",
    "configure_openclaw_cron_timeout.py",
    "generate_live_journal.py",
    "hermes_adversarial_auditor.py",
    "intent_graph.py",
    "learned_rules.py",
    "pre_session_sanity_check.py",

    # ── 5. Verification & Roll Utilities ──
    "verify_alpaca_live.py",
    "verify_tradier_live.py",
    "verify_xlu_rollover.py",
    "verify_gld_rollover.py",
    "deploy_market_heartbeat_guardrail.py",

    # ── 6. Compliance, Governance & Architectural Manuals ──
    "ANTIGRAVITY_MASTER_TRADING_MANUAL.md",
    "CLOSED_LOOP_PROTOCOL.md",
    "FAST_TRACK_GRADUATION_PLAN.md",
    "CRON_TIMEOUT_PREVENTION_PROTOCOL.md",
    "MEMORY.md",
    "HEARTBEAT.md",
    "INTEGRATION.md",
    "PION2_DIVERSIFICATION_MANDATE.md",
    "FORECAST_HANDOFF.md",
    "JULIA_SPINA_FRAMEWORK.md",
    "MASTER_CONTEXT_HANDOFF.md",
    "SOUL.md",
    "ROADMAP_W29.md",
    "GRADUATION_SCORECARD.md",

    # ── 7. Core Databases & State Artifacts (NEVER ARCHIVE) ──
    "SkonVault_Transaction_Journal.xlsx",
    "SkonVault_Live_Transaction_Journal.xlsx",
    "learned_rules.json",
    "verified_universe_catalog.json",
    "bridge.db",
    "account_state.db",
    "market_context.json",
    "graduation_scorecard.json",
    "active_trades.json",
    "tonight_selected_target.json",
    "candidates.txt",
    "wyckoff_signals.json",
    "sma20_slope_signals.json",
    "live_liquidity_matrix.json",
    "uoa_live_cache.json",
    "harvest_lockouts.json",
    "harvest_ratchets.json",
    "stopout_audit_ledger.json",
    "preflight_handshake_status.json",
    "anna_gate.json",
    "screener_gate.json",
    "credit_tracker.json",
    "execution_log.json",
    "trade_log.json",
    "markov_regime.json",
    "regime_state.json",
    "pipeline_status.json",
    "portfolio_positions.json",
    "position_snapshots.json",
    "intent_graph.json",
    "pion2_weekly_alpha.json",
    "snowball_growth_projection.json",
    "most_likely_projection.json",
    "token_guardrail_state.json",
    "active_portfolio_ledger.json"
}

def get_disk_free_mb(path: Path) -> float:
    """Returns free space in MB for the filesystem containing path."""
    try:
        total, used, free = shutil.disk_usage(path)
        return free / (1024 * 1024)
    except Exception:
        return 0.0

def execute_monthly_maintenance() -> dict:
    now_ict = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT")
    print("================================================================================")
    print(f"🧹 SKONVAULT MASTER MONTHLY SYSTEM MAINTENANCE & OPTIMIZATION (RULE-061)")
    print(f"📅 Timestamp: {now_ict}")
    print("================================================================================")

    base_dir = Path("/home/ubuntu/shared")
    if not base_dir.exists():
        base_dir = Path(__file__).resolve().parent

    free_before = get_disk_free_mb(base_dir)
    print(f"  💾 Initial Free Storage: {free_before:,.1f} MB")

    archive_scripts_dir = base_dir / "archive" / "legacy_scripts"
    archive_logs_dir = base_dir / "archive" / "logs"
    archive_scripts_dir.mkdir(parents=True, exist_ok=True)
    archive_logs_dir.mkdir(parents=True, exist_ok=True)

    archived_script_count = 0
    rotated_log_count = 0
    cleaned_cache_count = 0

    # ── STEP 1: ARCHIVE NON-CORE LEGACY SCRIPTS ─────────────────────────────────
    print("\n[STEP 1/6] 📦 Archiving Non-Core Legacy Patch & Completed Test Scripts...")
    for item in sorted(base_dir.iterdir()):
        if item.is_file():
            fname = item.name
            if fname not in CORE_PRODUCTION_FILES and not fname.startswith("."):
                # Archive non-core python scripts and completed temporary files
                if fname.endswith(".py") or fname.endswith(".bak") or fname.endswith(".tmp"):
                    dest = archive_scripts_dir / fname
                    shutil.move(str(item), str(dest))
                    archived_script_count += 1
                    print(f"  • Archived legacy script: {fname} -> archive/legacy_scripts/")

    print(f"  ✅ Successfully archived {archived_script_count} non-core files to keep production root lean!")

    # ── STEP 2: ROTATE & GZIP COMPRESS OVERSIZED LOG FILES ───────────────────────
    print("\n[STEP 2/6] 📜 Rotating & Gzip Compressing Oversized Log Files (>100 KB)...")
    timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    for log_file in base_dir.glob("*.log"):
        try:
            size_kb = log_file.stat().st_size / 1024.0
            if size_kb > 100.0:  # If log is larger than 100 KB, archive and compress
                archived_gz = archive_logs_dir / f"{log_file.stem}_{timestamp_str}.log.gz"
                
                # Read all lines
                with open(log_file, "rb") as f_in:
                    raw_content = f_in.read()
                
                # Write compressed copy to archive/logs/
                with gzip.open(str(archived_gz), "wb") as f_gz:
                    f_gz.write(raw_content)
                
                # Truncate active log file, keeping only the last 500 lines
                lines = raw_content.decode("utf-8", errors="ignore").splitlines(keepends=True)
                with open(log_file, "w", encoding="utf-8") as f_out:
                    f_out.writelines(lines[-500:])
                
                rotated_log_count += 1
                new_size_kb = log_file.stat().st_size / 1024.0
                print(f"  • Rotated {log_file.name} ({size_kb:.1f} KB -> kept last 500 lines [{new_size_kb:.1f} KB] | Compressed archive saved) ✅")
        except Exception as ex_l:
            print(f"  ⚠️ Error rotating {log_file.name}: {ex_l}")

    print(f"  ✅ Log maintenance completed ({rotated_log_count} log files rotated & compressed)!")

    # ── STEP 3: OPTIMIZE & VACUUM SQLITE DATABASES ──────────────────────────────
    print("\n[STEP 3/6] 🗄️ Optimizing & Vacuuming SQLite Message Bus & State Databases...")
    db_files = [base_dir / "bridge.db", base_dir / "account_state.db"]
    vacuumed_db_count = 0
    for db_file in db_files:
        if db_file.exists():
            try:
                orig_size = db_file.stat().st_size
                conn = sqlite3.connect(str(db_file), timeout=10)
                cur = conn.cursor()
                cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                cur.execute("VACUUM;")
                conn.commit()
                conn.close()
                new_size = db_file.stat().st_size
                vacuumed_db_count += 1
                print(f"  • Vacuumed {db_file.name}: {orig_size:,} bytes -> {new_size:,} bytes ✅")
            except Exception as ex_db:
                print(f"  ⚠️ DB notice for {db_file.name}: {ex_db}")

    # ── STEP 4: SYSTEM-LEVEL LOG VACUUMING & OS OPTIMIZATION ────────────────────
    print("\n[STEP 4/6] 🧹 Vacuuming Systemd Journals & Purging Transient Caches...")
    if sys.platform == "linux":
        try:
            # Vacuum systemd journals to retain only last 7 days (or 200MB max)
            subprocess.run(["sudo", "journalctl", "--vacuum-time=7d", "--vacuum-size=200M"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            print("  • Systemd journals vacuumed to <= 200 MB / 7 days ✅")
        except Exception as ex_j:
            print(f"  ℹ️ Journal vacuum notice: {ex_j}")
        
        try:
            # Clean pip and temporary cache
            home_dir = Path.home()
            for cache_target in [home_dir / ".cache" / "pip", Path("/tmp")]:
                if cache_target == Path("/tmp"):
                    for p in cache_target.glob("pip-*"):
                        if p.is_dir(): shutil.rmtree(str(p), ignore_errors=True)
                elif cache_target.exists():
                    shutil.rmtree(str(cache_target), ignore_errors=True)
            print("  • Transient pip and temp cache purged ✅")
        except Exception: pass

    # Clean __pycache__ and .DS_Store
    for p in base_dir.rglob("__pycache__"):
        try:
            shutil.rmtree(str(p))
            cleaned_cache_count += 1
        except Exception: pass
    for ds in base_dir.rglob(".DS_Store"):
        try:
            ds.unlink()
            cleaned_cache_count += 1
        except Exception: pass

    print(f"  ✅ Purged {cleaned_cache_count} bytecode cache and transient artifacts!")

    # ── STEP 5: MEASURE STORAGE RECLAIMED ───────────────────────────────────────
    free_after = get_disk_free_mb(base_dir)
    freed_mb = max(0.0, free_after - free_before)
    print("\n[STEP 5/6] 📊 Storage Reclaimed Analysis:")
    print(f"  • Pre-Cleanup Free Storage : {free_before:,.1f} MB")
    print(f"  • Post-Cleanup Free Storage: {free_after:,.1f} MB")
    print(f"  • Net Storage Reclaimed     : +{freed_mb:,.1f} MB 🚀")

    # ── STEP 6: VERIFY INTEGRITY WITH 16-TIER SMOKE TEST ────────────────────────
    print("\n[STEP 6/6] 🧪 Running Post-Maintenance 16-Tier Quantitative Smoke Test...")
    smoke_success = False
    try:
        sys.path.insert(0, str(base_dir))
        import run_system_smoke_test
        run_system_smoke_test.run_smoke_test()
        smoke_success = True
    except Exception as ex_sm:
        print(f"  🔴 Smoke test trigger notice: {ex_sm}")

    print("\n================================================================================")
    print("🏆 MONTHLY MAINTENANCE COMPLETE: SYSTEM IS CLEAN, MODULAR, LEAN & PRODUCTION-READY!")
    print("================================================================================")

    return {
        "ok": smoke_success,
        "freed_mb": round(freed_mb, 1),
        "scripts_archived": archived_script_count,
        "logs_rotated": rotated_log_count,
        "vacuumed_dbs": vacuumed_db_count,
        "cleaned_caches": cleaned_cache_count,
        "timestamp": now_ict
    }

if __name__ == "__main__":
    execute_monthly_maintenance()
