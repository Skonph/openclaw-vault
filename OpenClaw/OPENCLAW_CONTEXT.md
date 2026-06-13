# OpenClaw — Project Context & Continuation Brief
**Last updated:** 2026-06-13 (BKK)  
**Status:** Autonomous pipeline live. TOST manual Iron Condor executed on Jun 8. Hermes resolver scheduled. IC executor patched and validated.  
**Purpose:** Load this file at the start of every new Cowork/Nova session to continue development with full context. Read all sections before suggesting or implementing anything.

---

## 1. What OpenClaw Is

Fully autonomous options spread trading system running on an Ubuntu server in Bangkok timezone (UTC+7). Scans 28 tickers nightly, scores conviction, auto-executes qualifying spreads to an Alpaca paper account, monitors open positions, and auto-exits based on rules. Sends Telegram notifications per action and a morning digest.

**Three spread strategies:**
- **Bull Call Spread** (debit) — regime: `bull` or `flat_low`
- **Bear Put Spread** (debit) — regime: `bear`
- **Iron Condor** (credit, 4-leg) — regime: `flat_elevated`

---

## 2. Environments & Access

### Server (primary runtime)
| Item | Value |
|------|-------|
| Host | `ubuntu@43.160.222.7` |
| Timezone | Asia/Bangkok (UTC+7) |
| Scripts | `/home/ubuntu/openclaw/` |
| Vault (git) | `/home/ubuntu/openclaw-vault/` |
| Snapshots | `/home/ubuntu/openclaw/logs/snapshots/` |
| Credentials | `/home/ubuntu/openclaw/.env` |
| Alpaca | `https://paper-api.alpaca.markets/v2` (paper) |

### Mac (Cowork/Nova editing)
| Item | Value |
|------|-------|
| Scripts | `/Users/SkonP/AI_Prompt/Obsidient/SkonVault/OpenClaw/` |
| Vault (git) | `/Users/SkonP/AI_Prompt/Obsidient/SkonVault/` |

> **How changes flow:** Edit files on Mac via Cowork → git push → server pulls. For server-only files (`.env`, logs, snapshots), SSH directly. Nova/Cowork edits Python scripts; Hermes implements changes on the server side.

---

## 3. Server Crontab (Bangkok / UTC+7)

### OpenClaw pipeline
| Time (BKK) | Days | Command | Log |
|------------|------|---------|-----|
| 21:05 | Mon–Fri | `openclaw_scanner.py` | `logs/scanner.log` |
| **21:10** | **Mon–Fri** | **Hermes Autonomous Resolver** | Telegram (Hermes Job ID: `7a60511046b3`) |
| 21:20 | Mon–Fri | `vault_updater.py` | `logs/vault.log` |
| 07:30 | Tue–Sat | `morning_report.py` | `logs/morning_report.log` |

> ⚠️ **Hermes at 21:10 is NOT in crontab** — it runs via the Hermes scheduler (Job `7a60511046b3`). First live run: Monday Jun 9.

### Other (Tradier / trading-bot — separate system, same server)
| Time (BKK) | Days | Command |
|------------|------|---------|
| 21:15 | Tue–Thu | `run_scan.sh` |
| 21:30 | Mon–Fri | `position_monitor.py` |
| 00:00 | Tue–Sat | `position_monitor.py` |
| 02:30 | Tue–Sat | `position_monitor.py` |
| 08:00 | Tue–Sat | `daily_summary.py` |

> ⚠️ **Known issue:** `vault_updater.py` already calls `position_monitor.run()` internally at ~21:20. The 21:30 cron entry may double-fire if it points to `/home/ubuntu/openclaw/position_monitor.py`. Confirm the 21:30 entry is for the **Tradier** monitor (different path) not the OpenClaw one.

---

## 4. File Map

### Server — `/home/ubuntu/openclaw/`
| File | Purpose | Key functions |
|------|---------|---------------|
| `openclaw_scanner.py` | Nightly scan pipeline | `run_daily_scan()`, `analyze_spread()`, `analyze_bear_put_spread()`, `analyze_iron_condor()`, `determine_regime()` |
| `vault_updater.py` | Auto-executor + vault logs + git push | `auto_execute_orders()`, `_check_paper_mode()`, `_has_open_options()`, `_build_ic_payload()`, `_calc_qty()` |
| `position_monitor.py` | Exit rules + auto-close | `analyse_and_exit()`, `auto_close_spread()`, `_add_cooling_off()`, `_record_pnl()` |
| `morning_report.py` | 07:30 Telegram digest | `build_report()`, `_verify_fills()`, `_pnl_summary()` |
| `conviction_scorer.py` | Score 0–100 per spread | `score_conviction()`, `_score_offline()`, `_score_anthropic()` |
| `events_checker.py` | Tradier earnings/dividend check | `check_events()` — falls back to `UNCERTAIN` on 404 |
| `approval_manager.py` | `pending_orders.json` queue manager | `write_pending()`, `expire_old_orders()`, `approve_trade()` |
| `candidates.txt` | Active scan ticker list | Refreshed 2026-06-06 — 28 tickers |

### Mac — `/Users/SkonP/AI_Prompt/Obsidient/SkonVault/OpenClaw/`
| File | Purpose |
|------|---------|
| `nova_executor.py` | Manual CLI approval/dry-run tool — patched 2026-06-06 for IC 4-leg |
| `candidates.txt` | Synced copy of candidates (source of truth for edits) |
| `OPENCLAW_CONTEXT.md` | This file |
| `pending_orders.json` | Synced order queue (read/write via git pull) |
| `10_Execution_Log.md` | Trade execution history |
| `07_Macro_Context.md` | Nightly macro snapshot |
| `08_Next_Actions.md` | Autonomous run output |
| `09_Daily_Briefing.md` | Daily briefing (auto-generated) |

---

## 5. Rules & Thresholds (v4.0)

```python
# Entry rules (openclaw_scanner.py)
PRICE_MIN        = 10.0
PRICE_MAX        = 40.0
IV_RANK_MAX      = 40.0
IV_LAST_MAX      = 45.0      # hard cap — directional debit spreads
PREMIUM_MIN      = 0.30
PREMIUM_MAX      = 0.60
SPREAD_WIDTH_MAX = 3.0
DTE_MIN          = 25
DTE_MAX          = 50
OI_MIN           = 500
BID_ASK_MAX      = 0.10      # per leg
DAILY_MOVE_MAX   = 5.0
VIX_HARD_STOP    = 30.0
VIX_IC_MIN       = 18.0      # Iron Condor entry floor
CONVICTION_MIN   = 75

# Exit rules (position_monitor.py)
PROFIT_TARGET_PCT = 0.50     # close at 50% max profit
STOP_LOSS_PCT     = 0.20     # close when value ≤ 20% of debit (80% loss)
DEAD_TRADE_DTE    = 21       # close if loss > 25% and DTE ≤ 21
EXPIRY_GATE_DTE   = 7        # force close at DTE ≤ 7 regardless
```

---

## 6. Regime Detection

| Regime | Condition | Strategy |
|--------|-----------|----------|
| `cash` | VIX > 30 | No trades |
| `flat_elevated` | SPY ±0.5% AND VIX ≥ 18 | Iron Condor (credit, 4-leg) |
| `flat_low` | SPY ±0.5% AND VIX < 18 | Bull Call (debit) |
| `bull` | SPY > +0.5% | Bull Call (debit) |
| `bear` | SPY < −0.5% | Bear Put (debit) |
| `unknown` | No VIX data | Scan both debit spreads |

---

## 7. Active Candidates (28 tickers) — Refreshed 2026-06-06

```
# Bull call screen (uptrend, IV Rank low)
SIRI, CZR, CLF, SOFI

# Bear put screen (downtrend, IV Rank low)
PBR, GME, BMNR, UUUU, WBD, CHWY

# Iron condor screen (flat, IV Rank 30–50%)
CMG, CPNG, S, TOST

# Previous screen (all strategies, scanner determines direction)
XPEV, LI, BEKE, GEN, OSCR, AMTM, SOC, SSRM, SM, BZ, GAP, HMC, MBLY, DKNG
```

### Removed 2026-06-06
| Ticker | Reason |
|--------|--------|
| GLXY | Galaxy Digital — beta 2.87, IV tracks BTC (~80%+), fails `IV_LAST_MAX` permanently |
| BTDR | Bitdeer — live chain IV 159–265%, permanently fails `IV_LAST_MAX = 45%` |
| BTU | Peabody coal — consistently low OI at target strikes, near price ceiling ($29–31) |

### Added 2026-06-06 — ⚠️ Needs IBKR OI validation before relying on in live scans
| Ticker | Price | Thesis | Screen |
|--------|-------|--------|--------|
| CLF | ~$14 | Cleveland-Cliffs — GM supplier award catalyst | Bull call |
| SOFI | ~$16 | SoFi fintech pullback, improving profitability | Bull call |
| WBD | ~$27 | Warner Bros — antitrust headwinds on Paramount deal | Bear put |
| CHWY | ~$21 | Chewy — price target cut by Mizuho ahead of earnings | Bear put |
| DKNG | ~$25 | DraftKings — range-bound, liquid options | Previous |

### Watchlist (IV currently too high — recheck when normalises)
```
# CCL   — IV too high, Iran deal catalyst needed
# NCLH  — IV too high, same thesis as CCL
# AAL   — IV too high, recheck when IV rank drops
# PR    — KNOWN_HOLD until Jun 17 (dividend Jun 16)
```

---

## 8. Hermes Co-Pilot Integration

Hermes runs as a persistent systemd service (`hermes-gateway.service`) on the server, communicating via a private Telegram bot channel.

### Capabilities
- **Terminal access:** Runs Python scripts, edits files, installs packages directly on server
- **Web search:** Resolves UNCERTAIN events via DuckDuckGo (earnings, dividends)
- **Autonomous approvals:** Reads `pending_orders.json`, researches unsafe tickers, calls `approval_manager.py approve <trade_id>`
- **Telegram delivery:** Sends research summaries and verdicts back to chat

### Resolver schedule
- **Job ID:** `7a60511046b3`
- **Schedule:** `10 21 * * 1-5` — Mon–Fri 21:10 Bangkok
- **First live run:** Monday 9 June 2026
- **What it does:** Reads latest snapshot → finds UNCERTAIN events → web-searches earnings/ex-div dates → cross-references 14-day safety window → approves or rejects → Telegram summary

---

## 9. Nightly Execution Flow

```
21:05  openclaw_scanner.py
         → scans 28 tickers
         → determines regime (VIX/SPY)
         → writes snapshot JSON to logs/snapshots/
         → writes pending orders to pending_orders.json (events_status may be UNCERTAIN)

21:10  Hermes Resolver (Job 7a60511046b3)
         → reads latest snapshot
         → for each UNCERTAIN order: web-search earnings + ex-div dates
         → if safe: approval_manager.py approve <trade_id>  [sets events_status → clear]
         → Telegram summary

21:20  vault_updater.py
         → reads pending_orders.json
         → Gate 1: cooling-off check
         → Gate 2: events_status == 'clear' required  ← Hermes must have cleared this
         → Gate 3: conviction_pass == True
         → builds OCC symbols + Alpaca mleg payload
         → submits order to Alpaca paper
         → calls position_monitor.run() internally
         → updates vault markdown + git push
         → Telegram nightly summary

07:30  morning_report.py
         → Telegram digest: account equity, P&L, fills, open positions
```

---

## 10. Current Account State (As of 2026-06-13)

- **Equity:** $2,898.17 (paper)
- **Open positions:** None
- **Pending scans:** Resolved via Hermes scheduling.

---

## 11. Recent Changes (2026-06-13 session)

1. **Events Status Clearing:** Updated `approval_manager.py` so that marking an order as approved explicitly sets its `events_status` to `'clear'`.
2. **Auto-Executor Updates:** Modified `vault_updater.py` to process both `'pending'` and `'approved'` orders, bypassing safety gates for explicitly `'approved'` orders, and printing a clear warning if a pending order remains `'uncertain'` at 21:10 BKK.
3. **LLM Conviction Scorer Integration:** Documented setup to enable `_score_anthropic()` via `ANTHROPIC_API_KEY` configuration.

---

## 12. Known Issues & Risks

| Issue | Severity | Status |
|-------|----------|--------|
| `approval_manager.py approve` sets `events_status → clear` | ✅ Resolved | Updated to set events_status to 'clear' when approved. |
| position_monitor double-fire at 21:20 (via vault_updater) + 21:30 (cron) | ✅ Resolved | Confirmed 21:30 cron is for Tradier's monitor, no overlap. |
| New candidates (CLF, SOFI, WBD, CHWY, DKNG) not yet OI-validated | 🟡 Medium | Scanner will naturally filter via OI_MIN = 500, but wasted scans if consistently failing |
| Hermes has 10 min (21:10–21:20) to resolve all UNCERTAIN tickers | 🟡 Medium | Fine for 1–2 tickers. If 4+ UNCERTAIN, vault_updater prints a warning on unresolved tick. |

---

## 13. Improvement Roadmap — Next Priorities

### Immediate
1. **Monday night Hermes debrief**
   - Check Telegram logs for Hermes' 21:10 run tomorrow to verify it runs properly and auto-resolves correctly.
   - Verify fills on TOST IC in the morning report (07:30 BKK).

### Short-term (this week)
2. **IBKR MultiSort validation for new candidates**
   - Open IBKR Option Chain for CLF, SOFI, WBD, CHWY, DKNG. Check OI at ATM and ±1 strike for DTE 25–50 days.

3. **Conviction Scorer Upgrade**
   - Consider enabling `_score_anthropic()` by setting `ANTHROPIC_API_KEY` in `.env` for higher quality convictions.

### Medium-term
5. **IC live-fire monitoring**
   - No regime change needed — IC executor is code-complete
   - When VIX hits 18–30 with flat SPY: monitor that vault_updater submits 4-leg mleg and fills are reported correctly in morning_report
   - Files involved: `vault_updater.py` (`_build_ic_payload`), `morning_report.py` (`_verify_fills`)

6. **Vault_updater timing buffer (if needed)**
   - If Hermes regularly runs close to 21:20 with multiple UNCERTAIN tickers: change vault_updater cron from `20 21` to `25 21`
   - File: server crontab (`crontab -e` on ubuntu user)

7. **Conviction scorer upgrade**
   - `conviction_scorer.py` has `_score_anthropic()` but falls back to offline scoring
   - If `ANTHROPIC_API_KEY` is set in `.env`, it upgrades automatically
   - Consider enabling for higher-quality conviction scores on borderline trades (65–80 range)
   - File to edit: `/home/ubuntu/openclaw/.env` — add `ANTHROPIC_API_KEY=sk-...`

---

## 14. How to Implement Changes in a New Session

When starting a new session, tell Claude/Nova:
> "Read `/Users/SkonP/AI_Prompt/Obsidient/SkonVault/OpenClaw/OPENCLAW_CONTEXT.md` first, then help me with [task]."

### For Mac-side file edits (Python scripts, candidates.txt):
- Direct file edits via Cowork tools
- Files live at `/Users/SkonP/AI_Prompt/Obsidient/SkonVault/OpenClaw/`
- After editing: `cd /Users/SkonP/AI_Prompt/Obsidient/SkonVault && git add . && git commit -m "..." && git push origin main`
- Server pulls: `ssh ubuntu@43.160.222.7 "cd /home/ubuntu/openclaw-vault && git pull origin main"`

### For server-side changes (crontab, .env, approval_manager.py):
- Delegate to Hermes via Telegram with specific instructions
- Or SSH directly: `ssh ubuntu@43.160.222.7`
- Hermes has terminal toolset and can edit files and restart services directly

### For testing/dry-runs:
- Use `nova_executor.py` on Mac: `python3 nova_executor.py dry-run <trade_id>`
- Use bash sandbox in Cowork for Python logic tests without touching live files
