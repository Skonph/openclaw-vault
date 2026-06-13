# Tradier Auto-Trading System — Session Context

> Paste this file at the start of a new chat session to resume where we left off.
> The assistant will have full context on architecture, current state, and what to improve next.

---

## 1. System Overview

A fully autonomous paper options trading bot running on an Ubuntu server (`ubuntu@43.160.222.7`).
It scans the market each morning, selects the best-fit credit spread strategy,
auto-executes the trade on Tradier's sandbox API, monitors positions intraday
for exit triggers, and reports everything to Telegram.

**Goal:** Grow a $2,000 benchmark capital through defined-risk credit spread strategies.  
**Status:** Live and scanning. Risk size limit cut to 5% ($100 max risk per trade) in `daily_scan.py` to accelerate metrics convergence. End-to-end exit test remains open.

---

## 2. Infrastructure

| Component | Detail |
|---|---|
| Ubuntu server | `ubuntu@43.160.222.7` |
| Working directory | `~/trading-bot/` |
| Python venv | `~/trading-bot/venv/bin/python3` |
| systemd service | `tradier-bot.service` (runs `telegram_bot.py` always-on) |
| Telegram bot | TradierBot (separate token from other active vault bots) |
| Mac workspace | `~/AI_Prompt/Obsidient/SkonVault/Tradier/` (Cowork folder) |

**Dual-API architecture:**
- `api.tradier.com` (PROD token) → live market data, options chains, Greeks, technical history
- `sandbox.tradier.com` (SANDBOX token) → paper order execution, positions, balances

---

## 3. File Reference

### Server files (`~/trading-bot/`)

| File | Purpose |
|---|---|
| `daily_scan.py` | Core engine: scans index -> filters trend/calendar -> checks contract size -> constructs spread -> executes order |
| `telegram_bot.py` | Always-on bot: /scan /health /log /reconcile /positions /account /test /status /help |
| `position_monitor.py` | Exit manager: runs 3× daily, evaluates profit target, stop loss, time stop, or partial wing stops |
| `daily_summary.py` | 8:00 AM ICT report: account, trades entered/closed, win rate, performance metrics |
| `run_scan.sh` | Cron wrapper: runs daily_scan.py, logs output to dated log file |
| `.env` | Credentials (never commit) — sandbox and production tokens |
| `active_trades.json` | Live trade tracker (updated by daily_scan.py, managed by position_monitor.py) |
| `trade_log.jsonl` | Append-only trade history: entry records + exit records (type="exit") |
| `last_heartbeat.json` | Tracks daily heartbeat send date |
| `logs/cron.log` | Daily scan stdout/stderr log |
| `logs/monitor.log` | Position monitor stdout/stderr log |
| `logs/summary.log` | Daily summary stdout/stderr log |

### Mac workspace (`~/AI_Prompt/Obsidient/SkonVault/Tradier/`)

Same files as above — edit locally on Mac, test with `--test` flag, then `scp` to Ubuntu server.

---

## 4. Environment Variables (`.env`)

```
TRADIER_PROD_TOKEN=...       # api.tradier.com — real-time market data + history
TRADIER_SANDBOX_TOKEN=...    # sandbox.tradier.com — paper trading execution
TRADIER_SANDBOX_ACCOUNT=...  # sandbox account ID (e.g. VA39433735)
STARTING_CAPITAL=2000        # POC benchmark for P&L tracking
TELEGRAM_BOT_TOKEN=...       # TradierBot token (separate from other bots)
TELEGRAM_CHAT_ID=8069530775  # Telegram user ID for alert destination
```

---

## 5. Crontab (Ubuntu server, active and verified)

Schedules are configured in ICT (Bangkok Time, UTC+7), which corresponds to New York regular market hours:

```bash
# OpenClaw v3 — Bangkok time (UTC+7)
30 7  * * 2-6  cd /home/ubuntu && python3 ~/openclaw/morning_report.py >> ~/openclaw/logs/morning_report.log 2>&1
05 21 * * 1-5  cd /home/ubuntu && python3 ~/openclaw/openclaw_scanner.py >> ~/openclaw/logs/scanner.log 2>&1
20 21 * * 1-5  cd /home/ubuntu && python3 ~/openclaw/vault_updater.py >> ~/openclaw/logs/vault.log 2>&1

# Tradier — scan at 10:15 AM ET = 21:15 ICT (Tue–Thu only)
15 21 * * 2-4  bash /home/ubuntu/trading-bot/run_scan.sh >> /home/ubuntu/trading-bot/logs/cron.log 2>&1

# Tradier — position monitor at 10:30 AM / 1:00 PM / 3:30 PM ET = 21:30 / 00:00 / 02:30 ICT
30 21 * * 1-5  /home/ubuntu/trading-bot/venv/bin/python3 /home/ubuntu/trading-bot/position_monitor.py >> /home/ubuntu/trading-bot/logs/monitor.log 2>&1
0  0  * * 2-6  /home/ubuntu/trading-bot/venv/bin/python3 /home/ubuntu/trading-bot/position_monitor.py >> /home/ubuntu/trading-bot/logs/monitor.log 2>&1
30 2  * * 2-6  /home/ubuntu/trading-bot/venv/bin/python3 /home/ubuntu/trading-bot/position_monitor.py >> /home/ubuntu/trading-bot/logs/monitor.log 2>&1

# Tradier — daily summary report at 8:00 AM ICT (Tue-Sat)
0  8  * * 2-6  /home/ubuntu/trading-bot/daily_summary.py >> /home/ubuntu/trading-bot/logs/summary.log 2>&1
```

---

## 6. Strategy Logic Summary

### Entry Checks & Filters (`daily_scan.py`)

1. **Economic Calendar Check (`check_calendar_skip()`):** Skips trade entry if an FOMC meeting or CPI release occurs within the next 2 days (event day + 2 days prior).
2. **Trend Filter (`get_sma_20()`):** Calculates the 20-day Simple Moving Average (SMA) of daily closes. Puts are only sold if price > SMA-20; calls are only sold if price < SMA-20.
3. **VIX Regime Filter:**
   * VIX > 30: Cash only (extreme volatility).
   * VIX < 12: Pass (low IV, credit is too thin).
   * 12 <= VIX < 15: `low_vix_secondary` regime (scans higher-beta QQQ/IWM).
   * 15 <= VIX <= 30: Normal SPY scan first, falls back to QQQ.
4. **Day-of-Week filter:** Tuesday–Thursday entry only (skips Monday/Friday for gap/weekend risk).
5. **Position Limit:** Max 2 concurrent positions.

### Spread Construction Parameters

| Parameter | Value |
|---|---|
| Delta range (short strike) | 0.10 – 0.35 |
| DTE window | 10 – 28 days (widened to prevent holiday gaps) |
| Spread widths tried | $1, $2, $3, $4, $5, $7, $10 |
| Credit floor | max($0.30, 15% of width) |
| Max risk per trade | $100 (5% of $2,000 capital) |
| Dynamic Contract Sizing | Scaled to **2 contracts** when VIX > 20, candidate score > 0.30, and total risk <= $100. Otherwise, **1 contract**. |

### Exit Rules (`position_monitor.py`)

| Exit Rule | Trigger | Order type |
|---|---|---|
| Combined Profit Target | Cost to close combined spreads ≤ 50% of entry credit | Limit |
| Threatened IC Wing Stop | Individual wing cost to close ≥ 2× entry wing credit | Market (Partial exit, unthreatened wing remains open) |
| Standard Stop Loss | Cost to close spread ≥ 2× entry credit | Market |
| Time Stop | DTE ≤ 2 | Market |

*Evaluator handles single-wing scenarios when one wing has already been stopped out.*

---

## 7. Telegram Commands

| Command | Action |
|---|---|
| `/scan` | Run scan + auto-execute trade |
| `/positions` | Open positions + unrealized P&L |
| `/account` | Account balance |
| `/log` | Today's entries + exits + realized P&L |
| `/reconcile` | Sync active_trades.json vs Tradier positions |
| `/health` | System status: last scan, active trades, log sizes |
| `/test` | Mock data scan (no execution) |
| `/status` | Bot uptime + last scan time |
| `/help` | Full command menu |

---

## 8. Completed Work (Phase 1 & 2)

- [x] Dual-API architecture (prod data + sandbox execution)
- [x] Bull Put Spread, Bear Call Spread, and Iron Condor construction
- [x] Autonomous sandbox execution (no manual approval needed)
- [x] QQQ/IWM secondary scan fallback for VIX 12–15
- [x] Best-of-all-expirations scoring (10–28 DTE range, handles holiday weeks)
- [x] Position limit enforcement (max 2 concurrent positions)
- [x] Verified position monitor crontab schedules on Ubuntu server
- [x] Local unit test suite in `position_monitor.py` (`--test` mode)
- [x] Partial Iron Condor exit management (split wing stop loss closure)
- [x] SMA-20 trend filter (via `/markets/history`)
- [x] Economic calendar skip filter (FOMC / CPI 2-day windows)
- [x] Dynamic contract quantity sizing based on VIX and spread score
- [x] trade_log.jsonl and active_trades.json integration for full data loops
- [x] Resolved multileg order type parameter mismatch (using credit/debit instead of limit)
- [x] Implemented robust JSON parse error handling for API endpoints
- [x] Reduced risk size limit (`MAX_RISK = 100`) in `daily_scan.py` to target 5% risk per trade on $2k capital.

---

## 9. Pending Improvements (Priority Order)

### 🔴 Verify before next trade
1. **End-to-end exit test** — Once first trade is live, verify position_monitor.py correctly detects it in active_trades.json and submits BTC on exit trigger. Run `python3 position_monitor.py --test` to diagnostic check.

### 🟠 Phase 2 — implement after first profitable trade
2. **Profit lock at 21 DTE** — If a trade entered at 28 DTE reaches 50% profit before 14 DTE, close it and look for a fresh entry at current IV.

### 🟡 Phase 2 — operational improvements
3. **Email backup notification** — If Telegram fails to send, fall back to Gmail via SMTP. Add `GMAIL_USER` and `GMAIL_APP_PASSWORD` to `.env`.
4. **Weekly performance report** — Every Sunday 8:00 AM ICT: win rate, total P&L, best/worst trade, number of passes (VIX too low), Sharpe-like ratio.
5. **Log rotation** — Archive `trade_log.jsonl` entries older than 90 days to `trade_log_archive_YYYY.jsonl`. Run monthly via cron.
6. **IWM-specific mock data** — `MOCK_PUTS_IWM` and `MOCK_CALLS_IWM` with strikes at 280–300 range for accurate `--test` mode with IWM.

### 🟢 Phase 3 — strategy expansion
7. **Cash-secured put wheel** — After a bull put spread expires worthless, sell a naked OTM put at the same strike for the next expiry. Graduate to this once 10 spreads completed.
8. **Calendar spread on earnings** — Sell front-month ATM call, buy next-month same strike. Run on SPY around FOMC weeks when term structure is inverted.
9. **Backtesting module** — Replay daily_scan.py against historical data to measure expected win rate and optimal credit floor threshold.

---

## 10. Deploy Sequence

When making changes in the Mac workspace folder, deploy with:

```bash
# Core scan and monitoring scripts
scp ~/AI_Prompt/Obsidient/SkonVault/Tradier/daily_scan.py \
    ~/AI_Prompt/Obsidient/SkonVault/Tradier/telegram_bot.py \
    ~/AI_Prompt/Obsidient/SkonVault/Tradier/position_monitor.py \
    ~/AI_Prompt/Obsidient/SkonVault/Tradier/daily_summary.py \
    ubuntu@43.160.222.7:~/trading-bot/

# Restart bot after telegram_bot.py changes
ssh ubuntu@43.160.222.7 'sudo systemctl restart tradier-bot'
ssh ubuntu@43.160.222.7 'python3 ~/trading-bot/position_monitor.py --test'
```

---

## 11. Key Architectural Decisions

| Decision | Rationale |
|---|---|
| Separate Telegram token | Sharing token caused bots to collide and drops updates |
| `active_trades.json` vs `trade_log.jsonl` | Monitor needs fast random-access read/write; log is append-only audit trail |
| `--no-notify` flag in daily_scan.py | Prevents double Telegram notifications when bot invokes scan as a subprocess |
| ICT explicit crontabs | Ubuntu cron ignores CRON_TZ env var in standard user setups |
| 10:15 AM ET entry scan | First 45 mins of market has wide spreads; 10:15 AM has settled direction |
| Widened DTE window (28 days) | Handles holiday closures (e.g. Juneteenth on June 19) where weekly chains return null, allowing QQQ/IWM fallback or next-week options. |
| Hardcoded Econ calendar | CPI/FOMC dates are public and static for 2026. Hardcoding is robust and prevents web scraping failures. |
| Single-wing IC exits | Closing both wings when only one is threatened gives up the unthreatened side's theta decay; partial exits capture more premium. |

---

## 12. Quick Diagnostic Commands

```bash
# Check what's running
ssh ubuntu@43.160.222.7 'sudo systemctl status tradier-bot --no-pager'

# View today's scan
ssh ubuntu@43.160.222.7 "cat ~/trading-bot/logs/$(date +%Y-%m-%d).log"

# Check active trades
ssh ubuntu@43.160.222.7 'cat ~/trading-bot/active_trades.json'

# View trade history
ssh ubuntu@43.160.222.7 'cat ~/trading-bot/trade_log.jsonl'

# Tail monitor log
ssh ubuntu@43.160.222.7 'tail -30 ~/trading-bot/logs/monitor.log'

# Run exit diagnostics test
ssh ubuntu@43.160.222.7 'python3 ~/trading-bot/position_monitor.py --test'
```

---

## 13. Current Market Context (as of Jun 13, 2026)

* SPY grinding higher: ~759, low volatility.
* VIX ~16.70 (moderate regime, credit-selling sweet spot).
* QQQ/IWM carry structurally higher IV (~18.7%).
* Next major economic event: CPI release on June 17, 2026.

---

*Last updated: June 13, 2026 | Built with Claude & Gemini via Cowork*
