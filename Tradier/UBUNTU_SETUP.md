# Ubuntu Server Setup — Tradier Daily Scan
**Server:** `ubuntu@43.160.222.7`  
**Goal:** Run `daily_scan.py` directly from Ubuntu so it has unrestricted access to Tradier's APIs (no proxy blocking). Cron fires at 9:45 AM ET Mon–Fri automatically.

---

## Step 1 — Copy files from your Mac to the server

Run these on your **Mac terminal**:

```bash
# Create target directory on server
ssh ubuntu@43.160.222.7 "mkdir -p ~/trading-bot/logs"

# Copy the script, wrapper, and env template
scp ~/AI_Prompt/Obsidient/SkonVault/Tradier/daily_scan.py  ubuntu@43.160.222.7:~/trading-bot/
scp ~/AI_Prompt/Obsidient/SkonVault/Tradier/run_scan.sh    ubuntu@43.160.222.7:~/trading-bot/
scp ~/AI_Prompt/Obsidient/SkonVault/Tradier/.env.example   ubuntu@43.160.222.7:~/trading-bot/
```

> ⚠️ **Do NOT copy your `.env` file** — recreate it on the server directly (Step 3). This avoids credentials transiting scp logs.

---

## Step 2 — Install Python dependencies on the server

SSH in and set up a virtualenv:

```bash
ssh ubuntu@43.160.222.7

# On the Ubuntu server:
cd ~/trading-bot

# Install pip if not present
sudo apt-get update && sudo apt-get install -y python3-pip python3-venv

# Create virtualenv (keeps packages isolated)
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install requests python-dotenv

# Verify
python3 -c "import requests, dotenv; print('OK')"
```

---

## Step 3 — Create .env on the server

Still on the Ubuntu server (never paste credentials in chat):

```bash
cd ~/trading-bot
cp .env.example .env
nano .env   # or: vi .env
```

Fill in your actual values:
```
TRADIER_PROD_TOKEN=<your production API token>
TRADIER_SANDBOX_TOKEN=<your sandbox API token>
TRADIER_SANDBOX_ACCOUNT=<your sandbox account ID, e.g. 6YB80974>
STARTING_CAPITAL=2000

# Telegram notification — reuse the same bot OpenClaw already uses
TELEGRAM_BOT_TOKEN=<same bot token OpenClaw uses>
TELEGRAM_CHAT_ID=<same chat ID OpenClaw uses>
```

Secure the file so only your user can read it:
```bash
chmod 600 ~/trading-bot/.env
```

**Telegram credential source:**
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are already configured in OpenClaw's setup on this server.
- Check OpenClaw's config/env file and copy the values — no new bot needed.
- If unsure of your chat ID: message [@userinfobot](https://t.me/userinfobot) on Telegram and it replies with your ID.

---

## Step 4 — Make wrapper executable and test

```bash
cd ~/trading-bot
chmod +x run_scan.sh

# Dry-run with mock data first (no API calls)
bash run_scan.sh --test

# Live run (calls real Tradier APIs)
bash run_scan.sh
```

Expected output on success:
```
═══════════════════════════════════════════
  TRADIER SCAN — 2026-05-27 09:45:02 EDT
═══════════════════════════════════════════
  📊 MARKET SNAPSHOT
  ...
  ✅ ALL CRITERIA MET — PROCEED WITH TRADE SETUP
  ...
  📱 Telegram notification sent (chat: <your_chat_id>)
```

Log files are saved to `~/trading-bot/logs/YYYY-MM-DD.log`.

---

## Step 5 — Set up crontab

The cron expression uses `TZ=America/New_York` so it **auto-adjusts for EDT/EST** — no manual update needed in November or March.

```bash
crontab -e
```

Add this line (use nano if prompted for editor):
```
# Tradier daily scan — 9:45 AM ET, Monday–Friday
45 9 * * 1-5 TZ=America/New_York bash /home/ubuntu/trading-bot/run_scan.sh >> /home/ubuntu/trading-bot/logs/cron.log 2>&1
```

Verify it was saved:
```bash
crontab -l
```

---

## Step 6 — Verify cron is running

After the first automated run, check:

```bash
# View today's log
cat ~/trading-bot/logs/$(date +%Y-%m-%d).log

# Check cron output for errors
tail -50 ~/trading-bot/logs/cron.log

# Check if cron daemon is active
systemctl status cron
```

---

## Checking logs remotely from your Mac

You can tail the live log from your Mac terminal at any time:

```bash
# View today's scan result
ssh ubuntu@43.160.222.7 "cat ~/trading-bot/logs/$(date +%Y-%m-%d).log"

# Watch cron output live (when scan is running)
ssh ubuntu@43.160.222.7 "tail -f ~/trading-bot/logs/cron.log"

# List all saved logs
ssh ubuntu@43.160.222.7 "ls -lh ~/trading-bot/logs/"
```

---

## Updating the script

When `daily_scan.py` is updated on your Mac:

```bash
# From Mac:
scp ~/AI_Prompt/Obsidient/SkonVault/Tradier/daily_scan.py ubuntu@43.160.222.7:~/trading-bot/
```

---

## Architecture summary

```
Your Mac (viewing results)
    │
    │  SSH / Email
    ▼
Ubuntu Server 43.160.222.7
    │  cron: 9:45 AM ET Mon-Fri
    │  bash run_scan.sh
    ▼
daily_scan.py
    ├── GET api.tradier.com      ← Production API (real-time quotes + Greeks)
    └── POST sandbox.tradier.com ← Sandbox API (paper orders + P&L)
```

No proxy blocking — direct TCP to Tradier from the Ubuntu server.

---

## Cowork scheduled task (updated role)

The Cowork `tradier-daily-scan` task now serves as a **backup/oversight** layer:
- If you miss the email or SSH output, the Cowork brief fires at 8:53 PM ICT
- It reads the latest log via WebSearch fallback or provides a market-data brief
- All actual API execution now happens on the Ubuntu server

To view today's scan from Claude, just say: **"show me today's tradier log"** — Claude will SSH into the server and display it.

---

## Troubleshooting

| Issue | Fix |
|---|---|
| `ModuleNotFoundError: requests` | Run: `source ~/trading-bot/venv/bin/activate && pip install requests python-dotenv` |
| `401 Unauthorized` | Check `.env` — verify PROD_TOKEN vs SANDBOX_TOKEN are not swapped |
| Email not arriving | Check spam; verify `GMAIL_PASS` is App Password (not Gmail password); 2FA must be on |
| Cron not firing | `systemctl status cron` — if inactive: `sudo systemctl start cron && sudo systemctl enable cron` |
| Wrong timezone | Verify: `TZ=America/New_York date` should show ET time |
| Permission denied on .env | Run: `chmod 600 ~/trading-bot/.env` |
