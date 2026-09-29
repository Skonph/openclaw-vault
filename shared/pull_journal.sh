#!/usr/bin/env bash
# pull_journal.sh — Pulls the live updated SkonVault_Live_Transaction_Journal.xlsx from the Ubuntu VPS to the Mac

set -e

SERVER_IP="43.156.9.185"
SERVER_USER="ubuntu"
LOCAL_DIR="/Users/SkonP/AI_Prompt/Obsidient/SkonVault/shared/"
ROOT_DIR="/Users/SkonP/AI_Prompt/Obsidient/SkonVault/"
REMOTE_DIR="~/shared/"

echo "============================================================"
echo "📊 REFRESHING LIVE 3-SHEET EXCEL TRANSACTION JOURNAL FROM VPS"
echo "============================================================"

echo "[1/2] Generating latest journal from live Alpaca & Tradier brokers on VPS..."
ssh "${SERVER_USER}@${SERVER_IP}" "cd ~/shared && python3 transaction_journal_manager.py"

echo "[2/2] Downloading latest SkonVault_Live_Transaction_Journal.xlsx to Mac..."
rsync -avz "${SERVER_USER}@${SERVER_IP}:${REMOTE_DIR}SkonVault_Live_Transaction_Journal.xlsx" "$LOCAL_DIR"
rsync -avz "${SERVER_USER}@${SERVER_IP}:${REMOTE_DIR}SkonVault_Transaction_Journal.xlsx" "$LOCAL_DIR"
rsync -avz "${SERVER_USER}@${SERVER_IP}:${REMOTE_DIR}active_trades.json" "$LOCAL_DIR"
cp -f "${LOCAL_DIR}SkonVault_Live_Transaction_Journal.xlsx" "$ROOT_DIR"
cp -f "${LOCAL_DIR}SkonVault_Transaction_Journal.xlsx" "$ROOT_DIR"

echo "============================================================"
echo "✅ FRESH LIVE JOURNAL DOWNLOADED TO MAC! RE-OPEN IN EXCEL TO VIEW 📈"
echo "============================================================"
