#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# run_scan.sh — Tradier Daily Scan Cron Wrapper
# Runs daily_scan.py, saves dated log, sends Telegram summary via OpenClaw bot
#
# Cron usage (9:45 AM ET, Mon-Fri — TZ prefix handles EDT/EST automatically):
#   45 9 * * 1-5 TZ=America/New_York bash /home/ubuntu/tradier/run_scan.sh
#
# Manual usage:
#   bash run_scan.sh          # Full live scan
#   bash run_scan.sh --test   # Dry-run with mock data
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ─── CONFIG ──────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
DATE="$(date +%Y-%m-%d)"
LOGFILE="$LOG_DIR/${DATE}.log"
PYTHON="${SCRIPT_DIR}/venv/bin/python3"   # virtualenv python (fallback: python3)

# ─── SETUP ───────────────────────────────────────────────────────────────────
mkdir -p "$LOG_DIR"

# Use system python3 if virtualenv doesn't exist yet
if [ ! -f "$PYTHON" ]; then
    PYTHON="$(command -v python3)"
fi

# Load .env into shell (picks up TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, etc.)
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

# ─── RUN SCAN ────────────────────────────────────────────────────────────────
echo "═══════════════════════════════════════════" | tee "$LOGFILE"
echo "  TRADIER SCAN — $(date '+%Y-%m-%d %H:%M:%S %Z')" | tee -a "$LOGFILE"
echo "═══════════════════════════════════════════" | tee -a "$LOGFILE"

# Run the scan — output goes to both terminal and log file
"$PYTHON" "$SCRIPT_DIR/daily_scan.py" "$@" 2>&1 | tee -a "$LOGFILE"
EXIT_CODE=${PIPESTATUS[0]}

echo "" | tee -a "$LOGFILE"
echo "  [Scan completed at $(date '+%H:%M:%S %Z') — exit code: $EXIT_CODE]" | tee -a "$LOGFILE"

# ─── TELEGRAM NOTIFICATION ───────────────────────────────────────────────────
# Handled directly by daily_scan.py via notify_telegram() at the end of each
# full scan. It sends a rich summary with trade details for all strategy types
# (Bull Put, Bear Call, Iron Condor). No separate curl send needed here.
# ─────────────────────────────────────────────────────────────────────────────

exit $EXIT_CODE
