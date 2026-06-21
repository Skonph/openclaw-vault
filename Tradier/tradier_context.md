# Tradier Auto-Trading System — Session Context

> Paste this file at the start of a new chat session to resume where we left off.
> The assistant will have full context on architecture, current state, and what to improve next.

---

## 1. System Overview

A fully autonomous paper options trading bot running on an Ubuntu server (`ubuntu@43.156.9.185`).
It scans the market each morning, selects the best-fit credit spread strategy,
auto-executes the trade on Tradier's sandbox API, monitors positions intraday
for exit triggers, and reports everything to Telegram.

**Goal:** Grow a $2,000 benchmark capital through defined-risk credit spread strategies.  
**Status:** Live and scanning. Risk size limit cut to 5% ($100 max risk per trade) in `daily_scan.py` to accelerate metrics convergence. `position_monitor.py --test` exit-rule diagnostics (6/6 scenarios) verified 2026-06-13. `active_trades.json` cleared of stale `TEST-AUTO-001` dev placeholders (confirmed via sandbox `/positions` -> null — no real open positions). Stale `pending_trade.json` (root cause of the prior `r.json()` crash) removed and error-handling hardened. Next real entry from `daily_scan.py` (Tue–Thu 21:15 ICT) + the following `position_monitor.py` cycle is the true end-to-end exit-test verification — still the open item.

**Zero-real-fills diagnosis + observability fix (2026-06-18):** Confirmed `order_status:"simulated"` is **purely the `--test` argv flag** (`TEST_MODE = "--test" in sys.argv`), NOT a stuck live/sim toggle — there is no live flag to flip. The live cron (`run_scan.sh`, no args) runs live; every `simulated`/`TEST-AUTO-001` row in `trade_log.jsonl` is a leftover from manual `--test` dev runs (May 31–Jun 6). No `executed_*.json` has ever been created (the success path that archives `pending_trade.json` → `executed_*.json` has never fired). Root cause of the "11+ cycles, no fill" appearance: **no-trade outcomes logged nothing**, so a healthy "declined today" run looked identical to a silent crash. (The proxy `403 Forbidden` in trade_log lines 6–7 was an environmental artifact from a proxy-locked sandbox, NOT the production box.) **Fix:** added `log_scan_heartbeat()` — every *live* run now appends one `{"type":"scan","result":"no_trade","reason":...}` line (reason ∈ calendar_skip | position_limit | cash | pass | no_qualifying_spread) with regime/SPY/VIX; suppressed in `--test`. `daily_summary.py` patched to ignore `type=="scan"` records (won't inflate entry counts). Verified by `test_scan_heartbeat.py` (10/10). **The next Tue–Thu cron (Jun 23–25) is now self-verifying:** it leaves either a real fill (`executed_*.json` + entry) or a logged decline reason. ✅ **VERIFIED LIVE 2026-06-18 night:** the 21:15 cron printed `📡 Shared market_context applied (regime moderate, VIX 17.14, SPY 0.77%)`, routed to Bull Put Spread, found no qualifying spread, and wrote a heartbeat (`{"type":"scan","date":"2026-06-18","reason":"no_qualifying_spread",...}`). Shared-context consumer + heartbeat both confirmed working end-to-end in the real cron.

**Server diagnostic findings + live-position fix (2026-06-18 PM):** Live server check confirmed the cron runs LIVE (real data, exit 0; the Jun-17 blank was a correct `CALENDAR SKIP` for FOMC), sandbox reachable (HTTP 200), crontab clean (no `--test`). **Discovered a real open position:** 3× SPY 695/700 bull put spread (exp 2026-06-26, short 700P/long 695P), unrealized **−$213**, origin manual/legacy (no `executed_*.json`, no trade_log record) — so a real multileg fill DID work, just was never archived. **Two bugs fixed:** (1) `active_trades.json` on the server was **malformed** (unquoted keys, from a hand-reconcile) → `position_monitor.py` crashed on `json.load()`, leaving the live position **UNMANAGED**. Rewrote it as valid JSON (values preserved). (2) Hardened `load_active()` to **fail loud** (Telegram alert + `SystemExit`) on unreadable JSON instead of crashing or silently reporting "no active trades". Verified the monitor now HOLDS the position (`$0.75 cost-to-close < $2.50 stop`, no false trigger) — `test_load_active_safety.py` (4/4). ⚠️ **Risk note:** this position's max loss ≈ $1,488 (3× $5-wide) vs the `MAX_RISK=$100` policy — it did NOT come from `daily_scan` (which only builds $1-wide). It's deep OTM / 8 DTE / likely to expire worthless, and now monitor-managed (stop $2.50, time-stop ≤2 DTE), but the operator should decide whether to let it ride or manually trim the oversized risk. `entry_credit:0.04` is nominal (cosmetic P&L only; no exit trigger depends on it).

---

## 2. Infrastructure

| Component | Detail |
|---|---|
| Ubuntu server | `ubuntu@43.156.9.185` |
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
| Profit Lock (21 DTE) | DTE ≤ 21 AND cost to close ≤ 75% of entry credit (≥25% captured), but above the 50% target | Limit |
| Threatened IC Wing Stop | Individual wing cost to close ≥ 2× entry wing credit | Market (Partial exit, unthreatened wing remains open) |
| Standard Stop Loss | Cost to close spread ≥ 2× entry credit | Market |
| Time Stop | DTE ≤ 2 | Market |

*Evaluator handles single-wing scenarios when one wing has already been stopped out.*

*Priority order: Time Stop > Stop Loss > Profit Target > Profit Lock. The 21 DTE
profit lock only fires for standard 2-leg spreads and Iron Condors with both
wings still open — it banks a decent partial win (≥25% of credit) before gamma
risk accelerates in the final weeks, even if the trade hasn't hit the full 50%
target yet. Tunable via `PROFIT_LOCK_DTE` (21) and `PROFIT_LOCK_MIN_CAPTURE`
(0.25) at the top of `position_monitor.py`.*

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
- [x] Profit-lock exit rule at 21 DTE (≥25% of credit captured) — `position_monitor.py`, local `--test` suite now 10/10 (2026-06-13)
- [x] No-trade scan heartbeat — `log_scan_heartbeat()` in `daily_scan.py` makes every live cron run observable in `trade_log.jsonl`; `daily_summary.py` filters `type=="scan"`; `test_scan_heartbeat.py` 10/10 (2026-06-18)

---

## 9. Pending Improvements (Priority Order)

### 🔴 Verify before next trade
1. **End-to-end exit test** — `python3 position_monitor.py --test` diagnostics pass (6/6 scenarios, 2026-06-13). `active_trades.json` cleared to `[]` (was holding 2 stale `TEST-AUTO-001` dev entries; sandbox `/positions` confirmed null). Remaining: once `daily_scan.py`'s next Tue–Thu run produces a real fill in `active_trades.json`, confirm `position_monitor.py` detects it on its next 21:30/00:00/02:30 ICT cycle and submits a real BTC on exit trigger.
   - **Verification mechanism (2026-06-18):** the scan heartbeat now records every live decline, so the diagnostic is: after a Tue–Thu cron, check `trade_log.jsonl` for either (a) a real entry + `executed_*.json` (→ proceed to monitor/exit verification), or (b) a `{"type":"scan",...}` line giving the decline reason (→ system healthy, just no setup). Before trusting any of this, run the server diagnostic once to confirm recent dated logs end in `PASS today` rather than a traceback/HTTP error on the order POST. Deploy: `scp daily_scan.py daily_summary.py test_scan_heartbeat.py ubuntu@43.156.9.185:~/trading-bot/` then run `test_scan_heartbeat.py` on the box.

### 🟠 Phase 2 — implement after first profitable trade
2. ~~**Profit lock at 21 DTE**~~ — ✅ Done 2026-06-13. Added a new `profit_lock_dte`
   exit rule: at DTE ≤ 21, if the position has captured ≥25% of entry credit but
   hasn't yet hit the full 50% profit target, close it at limit and free capital
   for redeployment rather than holding through the high-gamma final weeks.
   (Note: the original wording — "50% profit before 14 DTE" — already overlaps
   with the existing always-on Combined Profit Target rule, which fires at any
   DTE. The real gap was *partial* profits near 21 DTE that fell short of 50%;
   that's what this new rule covers.) Local `--test` suite expanded from 6 to
   10 scenarios (all passing) — see §8.

### 🟡 Phase 2 — operational improvements
3. **Email backup notification** — If Telegram fails to send, fall back to Gmail via SMTP. Add `GMAIL_USER` and `GMAIL_APP_PASSWORD` to `.env`.
4. **Weekly performance report** — Every Sunday 8:00 AM ICT: win rate, total P&L, best/worst trade, number of passes (VIX too low), Sharpe-like ratio.
5. **Log rotation** — Archive `trade_log.jsonl` entries older than 90 days to `trade_log_archive_YYYY.jsonl`. Run monthly via cron.
6. **IWM-specific mock data** — `MOCK_PUTS_IWM` and `MOCK_CALLS_IWM` with strikes at 280–300 range for accurate `--test` mode with IWM.

### 🟢 Phase 3 — strategy expansion
7. **Cash-secured put wheel** — After a bull put spread expires worthless, sell a naked OTM put at the same strike for the next expiry. Graduate to this once 10 spreads completed.
8. **Calendar spread on earnings** — Sell front-month ATM call, buy next-month same strike. Run on SPY around FOMC weeks when term structure is inverted.
9. **Backtesting module** — ✅ **DONE (2026-06-13)**. `Tradier/backtest.py` replays `daily_scan.py`'s entry filters (day-of-week, SMA20 trend, VIX-regime routing via a realized-vol proxy, 10–28 DTE, delta 0.10–0.35, widths $1–$10, MAX_RISK=$100, MAX_POSITIONS=2) and `position_monitor.py`'s exit rules (50% PT, 21-DTE profit lock, 2x stop, time stop, IC wing stop) against ~2 years of real SPY/QQQ/IWM daily closes (Black-Scholes pricing, IV = 20-day realized vol × 1.2). Results in `Tradier/backtest_results.json`.

   **Headline results (501 trading days, 2024-06-13 → 2026-06-12, $2,000 start):**
   - 126 trades, **73.0% win rate**, **+$405.75 P&L (+20.3%)**, max drawdown **$173.88 (8.7%)**.
   - By strategy: Bull Put Spread 84.2% WR / +$409 (82 trades, the workhorse). Iron Condor 44.0% WR / +$57 (25 trades, high variance — 14/25 hit the threatened-wing stop). Bear Call Spread 63.2% WR / **-$60** (19 trades, net losing despite a positive win rate — losses run larger than wins).
   - Exit mix: profit_lock 77, stop_loss 17, ic_wing_stop 14, profit_target 13, time_stop 3.

   **Credit-floor sweep (10% / 15% / 20% / 25% of width) — RESULT: IDENTICAL across all four thresholds.**
   Cause: under MAX_RISK=$100, every selected spread came back **$1-wide** (100% of 126 trades) — wider spreads almost always blow past the $100 max-loss cap. For a $1-wide spread, `max(0.30, width*pct%)` = **$0.30 for every pct in 10–25%** (since even 25% of $1 = $0.25 < $0.30). The absolute $0.30 floor dominates and the relative percentage never binds. **No threshold change is needed/possible without also raising MAX_RISK** (to allow $2+ wide spreads, where the relative floor would start to matter) or removing the $0.30 absolute floor.

   **Secondary finding (dynamic 2-contract sizing):** the "VIX>20 & score>0.30 → qty=2" rule in `construct_*_spread()` never fired in the backtest — $1-wide spread scores (credit/max_loss) cap out around 0.005–0.015, far below the 0.30 threshold. As written, this sizing rule was effectively dead code for the current $1-wide regime.

10. **Dynamic 2-contract sizing threshold fix** — ✅ **DONE (2026-06-13), deployed to server.** Replaced the dead `score > 0.30` / `combined_score > 0.30` checks in `construct_bull_put_spread`, `construct_bear_call_spread`, and `construct_iron_condor` with two new module-level constants:
    - `DYNAMIC_SIZING_SCORE_THRESHOLD = 0.010` (bull put / bear call)
    - `DYNAMIC_SIZING_SCORE_THRESHOLD_IC = 0.020` (iron condor, sum of put+call leg scores)

    The rule now fires on above-average-credit days (≈≥$0.50 net credit on a $1-wide spread, where `max_loss ≈ $50` and the existing `2×max_loss ≤ MAX_RISK` risk cap can still pass) when VIX > 20 — both the VIX gate and the risk-cap gate remain enforced exactly as before. Local test `Tradier/test_dynamic_sizing.py` (10/10 passing) covers: regression (old threshold never fires), new threshold firing/not-firing, VIX gate, and risk-cap gate. Deployed via `scp daily_scan.py ubuntu@43.156.9.185:~/trading-bot/daily_scan.py`; `--test --construct` smoke-tested clean on the server post-deploy.

   **Caveats:** IV is a 20-day realized-vol proxy (×1.2), not live VIX/chain IV (VIX history unavailable from the data feed); each of SPY/QQQ/IWM routes off its own momentum/SMA/vol rather than SPY driving all three as in production; Iron Condor "threatened wing" closes the whole position rather than partial-exiting one side. Treat absolute P&L as directional, not exact — the win-rate/exit-mix patterns and the credit-floor finding are the actionable takeaways.

11. **Tier-3 "high conviction" sizing (Improvement #5)** — ✅ **DONE (2026-06-13).** Added a qty=3 tier on top of the existing qty=2 rule in `construct_bull_put_spread`, `construct_bear_call_spread`, and `construct_iron_condor`:
    - `DYNAMIC_SIZING_SCORE_THRESHOLD_TIER3 = 0.018` (bull put / bear call) — fires when VIX>20 and `3 * max_loss <= MAX_RISK_TIER3`.
    - `DYNAMIC_SIZING_SCORE_THRESHOLD_IC_TIER3 = 0.032` (iron condor, sum of put+call leg scores).
    - `MAX_RISK_TIER3 = 150` — a raised risk ceiling used **only** for the tier-3 check (vs. `MAX_RISK = 100` for tier-2's `2*max_loss` check). Without this, 3x the standard $50 max_loss cap (=$150) would be unreachable under the old $100 ceiling.

    Score 0.018 corresponds to roughly $0.63 net credit on a $1-wide spread (max_loss≈$37) — an exceptionally rich-credit day, well above the tier-2 bar (0.010 ≈ $0.50 credit). The evaluation order is tier-3 → tier-2 → tier-1 (qty=1 default), so all three gates (VIX, score, risk cap) remain enforced at each tier. Local test `Tradier/test_dynamic_sizing.py` expanded to 19/19 passing (adds tier-3 firing/not-firing, VIX gate, and risk-cap-gate cases for both single-leg and IC). `--test --construct` smoke-tested clean post-edit.

    **Note:** in the Improvement #4 backtest, no $1-wide trade reached score≥0.018 (max observed was ~0.0122), so tier-3 is expected to fire rarely — by design, it's reserved for unusually high-IV/high-credit days.

---

## 10. Deploy Sequence

When making changes in the Mac workspace folder, deploy with:

```bash
# Core scan and monitoring scripts
scp ~/AI_Prompt/Obsidient/SkonVault/Tradier/daily_scan.py \
    ~/AI_Prompt/Obsidient/SkonVault/Tradier/telegram_bot.py \
    ~/AI_Prompt/Obsidient/SkonVault/Tradier/position_monitor.py \
    ~/AI_Prompt/Obsidient/SkonVault/Tradier/daily_summary.py \
    ubuntu@43.156.9.185:~/trading-bot/

# Restart bot after telegram_bot.py changes
ssh ubuntu@43.156.9.185 'sudo systemctl restart tradier-bot'
ssh ubuntu@43.156.9.185 'python3 ~/trading-bot/position_monitor.py --test'
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
ssh ubuntu@43.156.9.185 'sudo systemctl status tradier-bot --no-pager'

# View today's scan
ssh ubuntu@43.156.9.185 "cat ~/trading-bot/logs/$(date +%Y-%m-%d).log"

# Check active trades
ssh ubuntu@43.156.9.185 'cat ~/trading-bot/active_trades.json'

# View trade history
ssh ubuntu@43.156.9.185 'cat ~/trading-bot/trade_log.jsonl'

# Tail monitor log
ssh ubuntu@43.156.9.185 'tail -30 ~/trading-bot/logs/monitor.log'

# Run exit diagnostics test
ssh ubuntu@43.156.9.185 'python3 ~/trading-bot/position_monitor.py --test'
```

---

## 13. Current Market Context (as of Jun 13, 2026)

* SPY grinding higher: ~759, low volatility.
* VIX ~16.70 (moderate regime, credit-selling sweet spot).
* QQQ/IWM carry structurally higher IV (~18.7%).
* Next major economic event: CPI release on June 17, 2026.

---

**2026-06-19 — $15k PRIMARY-ACCOUNT SCALING (graduation: Tradier = lead live platform).** Risk sizing scaled from the $2k design to a $15k account at **2% per-trade**: `MAX_RISK $200→$300` (2% of $15k; allows $2–3-wide spreads), `MAX_RISK_TIER3 $150→$450` (qty-3 ceiling = 1.5× MAX_RISK — also fixes the earlier bug where TIER3 $150 < MAX_RISK $200 made qty-3 unreachable), `MAX_POSITIONS 2→5`, `STARTING_CAPITAL →$15000`. Portfolio risk is bounded by construction: 5 positions × $450 = **$2,250 = 15% of $15k** (no separate summation gate needed). Tests: `test_tradier_scaling.py` 7/7; `test_dynamic_sizing.py` updated to new constants 19/19. **⚠️ RE-VALIDATE THE BACKTEST:** the +20.3%/73%-WR backtest was run at `MAX_RISK=100`/all-$1-wide; at $300 the scanner can now pick $2–3-wide spreads (different credit-floor + score regime), so those numbers DO NOT carry over — re-run `backtest.py` at the new settings before trusting. **Server:** also set `STARTING_CAPITAL=15000` in `~/trading-bot/.env` (code default is 15000 but `.env` overrides). Deploy: `scp daily_scan.py test_tradier_scaling.py test_dynamic_sizing.py → ~/trading-bot/`. Graduation scorecard already points its primary capital at Tradier ($15k); OpenClaw/guardrail remain secondary/paper.

*Last updated: June 19, 2026 (heartbeat + position-monitor hardening + shared-context consumer, all deployed & verified live 2026-06-18 night; $15k/2% primary-account scaling 2026-06-19) | Built with Claude & Gemini via Cowork*
