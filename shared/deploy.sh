#!/usr/bin/env bash
# deploy.sh — Safe Deployment Script for Ubuntu VPS (43.156.9.185)
# Excludes active runtime databases (*.db, *.wal, *.shm) to prevent database corruption.

set -e

SERVER_IP="43.156.9.185"
SERVER_USER="ubuntu"
LOCAL_DIR="/Users/SkonP/AI_Prompt/Obsidient/SkonVault/shared/"
REMOTE_DIR="~/shared/"

echo "============================================================"
echo "🚀 SAFE SYSTEM DEPLOYMENT TO UBUNTU VPS ($SERVER_IP)"
echo "============================================================"

echo "[1/3] Syncing python scripts & configurations to VPS (protecting live journal & DBs)..."
rsync -avz \
  --exclude='*.db*' \
  --exclude='*.wal' \
  --exclude='*.shm' \
  --exclude='SkonVault_Transaction_Journal.xlsx' \
  --exclude='SkonVault_Live_Transaction_Journal.xlsx' \
  --exclude='__pycache__' \
  --exclude='.git' \
  "$LOCAL_DIR" "${SERVER_USER}@${SERVER_IP}:${REMOTE_DIR}"

rsync -avz /Users/SkonP/AI_Prompt/Obsidient/SkonVault/openclaw.json "${SERVER_USER}@${SERVER_IP}:~/.openclaw/openclaw.json"

echo "[2/3] Synchronizing OpenClaw memory & regenerating live Excel Transaction Journal on VPS..."
ssh "${SERVER_USER}@${SERVER_IP}" "cd ~/shared && python3 sync_openclaw_anna_memory.py && python3 transaction_journal_manager.py"

echo "[3/3] Pulling live SkonVault_Live_Transaction_Journal.xlsx from VPS to Mac..."
rsync -avz "${SERVER_USER}@${SERVER_IP}:${REMOTE_DIR}SkonVault_Live_Transaction_Journal.xlsx" "$LOCAL_DIR"
rsync -avz "${SERVER_USER}@${SERVER_IP}:${REMOTE_DIR}SkonVault_Transaction_Journal.xlsx" "$LOCAL_DIR"
cp -f "${LOCAL_DIR}SkonVault_Live_Transaction_Journal.xlsx" /Users/SkonP/AI_Prompt/Obsidient/SkonVault/
cp -f "${LOCAL_DIR}SkonVault_Transaction_Journal.xlsx" /Users/SkonP/AI_Prompt/Obsidient/SkonVault/

echo "============================================================"
echo "✅ DEPLOYMENT & JOURNAL SYNC COMPLETE: LOCAL EXCEL UPDATED! 📊"
echo "============================================================"
