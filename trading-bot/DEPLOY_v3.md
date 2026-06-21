# OpenClaw v3 — Deployment Guide

## Files to scp to server

```bash
SERVER=ubuntu@43.156.9.185
REMOTE=~/trading-bot/

scp trading-bot/events_checker.py     $SERVER:$REMOTE
scp trading-bot/conviction_scorer.py  $SERVER:$REMOTE
scp trading-bot/approval_manager.py   $SERVER:$REMOTE
scp trading-bot/executor.py           $SERVER:$REMOTE
scp trading-bot/position_monitor.py   $SERVER:$REMOTE
scp trading-bot/openclaw_scanner.py   $SERVER:$REMOTE
scp trading-bot/vault_updater.py      $SERVER:$REMOTE
```

## Updated crontab (server)

```bash
ssh ubuntu@43.156.9.185
crontab -e
```

Replace existing OpenClaw lines with:

```cron
# OpenClaw — Bangkok time (UTC+7)
# Scanner at 21:05 (US market 09:35 EDT — first 5 min settled)
05 21 * * 1-5 cd /home/ubuntu && python3 ~/trading-bot/openclaw_scanner.py >> ~/trading-bot/logs/scanner.log 2>&1

# Vault updater at 21:20 (scanner finishes ~21:15)
20 21 * * 1-5 cd /home/ubuntu && python3 ~/trading-bot/vault_updater.py >> ~/trading-bot/logs/vault.log 2>&1
```

> Note: position_monitor.py is called automatically inside openclaw_scanner.py at end of run.
> No separate cron needed unless you want mid-day position checks.

## Verify .env has required keys

```bash
ssh ubuntu@43.156.9.185 'grep -E "TRADIER|ALPACA|ANTHROPIC" ~/trading-bot/.env'
```

Expected output:
```
TRADIER_TOKEN=...          ← required (market data + events calendar)
ALPACA_API_KEY=...         ← required (account + execution)
ALPACA_SECRET_KEY=...      ← required
ALPACA_BASE_URL=https://paper-api.alpaca.markets/v2
ANTHROPIC_API_KEY=...      ← optional (upgrades conviction scorer to Claude Haiku)
```

## Test each module standalone

```bash
ssh ubuntu@43.156.9.185

# Test events checker (Tradier fundamentals endpoint)
python3 ~/trading-bot/events_checker.py NCLH CCL AAL VALE

# Test conviction scorer
python3 ~/trading-bot/conviction_scorer.py

# Test approval manager
python3 ~/trading-bot/approval_manager.py list

# Test position monitor
python3 ~/trading-bot/position_monitor.py --stdout

# Test full scan (run during US market hours: 21:05–23:00 Bangkok)
python3 ~/trading-bot/openclaw_scanner.py
```

## Pull vault changes to Mac

```bash
cd /Users/SkonP/AI_Prompt/Obsidient/SkonVault
git pull origin main
```

## Approval workflow (after first nightly run)

1. Server scanner runs at 21:05 → finds qualifying spread → checks events → scores conviction
2. Server vault_updater runs at 21:20 → pushes pending_orders.json to GitHub
3. On Mac: `git pull origin main` → pending_orders.json arrives in vault
4. Open `trading-bot/approval_dashboard.html` in browser to review
5. Click **Approve & Execute** → order goes to Alpaca paper account
6. Click **Reject** → order marked rejected, no trade

## Manual approval via CLI (alternative to dashboard)

```bash
# On server:
python3 ~/trading-bot/approval_manager.py list
python3 ~/trading-bot/approval_manager.py approve A1B2C3D4
python3 ~/trading-bot/approval_manager.py reject  A1B2C3D4 "events uncertain, checked IBKR, earnings Jun 15"

# Then execute approved order:
python3 ~/trading-bot/executor.py execute A1B2C3D4
```

## Tradier fundamentals endpoint note

The `events_checker.py` uses `GET /v1/markets/fundamentals/calendars`.
- If this returns 403: your Tradier plan doesn't include fundamentals.
- Events check will return `uncertain` for all tickers.
- `uncertain` orders still go to pending queue but dashboard shows ⚠️ warning.
- You must verify IBKR manually before approving uncertain orders.
- To check: `curl -H "Authorization: Bearer $TOKEN" "https://api.tradier.com/v1/markets/fundamentals/calendars?symbols=AAPL"`

## ANTHROPIC_API_KEY (optional upgrade)

Without it: conviction_scorer.py uses offline rule-based scoring (consistent, fast, free).
With it: scorer calls Claude Haiku for richer reasoning per alert.

To add:
```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> ~/trading-bot/.env
```
