#!/usr/bin/env bash
# pull_from_vps.sh — Pulls live audited scripts & ground truth from Ubuntu VPS to Mac
# Protects the live fixes made by Hermes on the VPS from being overwritten by local Mac files.

set -e

SERVER_IP="43.156.9.185"
SERVER_USER="ubuntu"
LOCAL_DIR="/Users/SkonP/AI_Prompt/Obsidient/SkonVault/shared/"
REMOTE_DIR="~/shared/"

echo "============================================================"
echo "📥 PULLING LIVE AUDITED SUITE & FIXES FROM VPS ($SERVER_IP)"
echo "============================================================"

rsync -avz \
  --exclude='*.db*' \
  --exclude='*.wal' \
  --exclude='*.shm' \
  --exclude='__pycache__' \
  --exclude='.git' \
  "${SERVER_USER}@${SERVER_IP}:${REMOTE_DIR}" "$LOCAL_DIR"

echo "============================================================"
echo "✅ LOCAL REPOSITORY SYNCHRONIZED WITH VPS LIVE FIXES! 🛡️"
echo "============================================================"
