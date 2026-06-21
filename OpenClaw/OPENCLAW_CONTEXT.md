# OpenClaw — Project Context & Continuation Brief
**Last updated:** 2026-06-19 (BKK)  
**2026-06-18 — Shared market-context consumer (DEPLOYED & VERIFIED LIVE):** `openclaw_scanner.py` has a pure helper `_macro_quote_from_context(sym, ctx_quotes)` and the Step-2 macro loop sources VIX/SPY from the shared `~/shared/market_context.json` (written nightly 20:55 ICT by `market_context_writer.py`) when fresh — skipping those 2 of 10 per-ticker fetches and matching Tradier/guardrail off the same snapshot. Sectors (XLE…XLV) still fetch live (used for EMA20). Falls back to live `get_quote` per ticker if the context is missing/stale (freshness-guarded `read_macro_signal.py`). Tests `OpenClaw/test_macro_consumer.py` 7/7 (passed on server post-deploy). **Deployed to `~/openclaw/` 2026-06-18 after Tradier's live `📡` confirmed.** ✅ **VERIFIED LIVE 2026-06-18 night** — the 21:05 scan printed `📡 Shared market_context fresh (regime moderate)` and sourced `VIX $17.14 📡 / SPY $744.72 📡` from the shared file (matching Tradier/guardrail's identical snapshot). See vault `SESSION_SUMMARY_2026-06-18.md` and `shared/INTEGRATION.md`.  
**2026-06-19 — Multi-position portfolio policy (replaces the binary max-1 gate):** `vault_updater.py` no longer hard-blocks at 1 open spread (`_has_open_options()` early-return removed). Concurrency is now governed by a **portfolio risk budget** enforced per-order in `auto_execute_orders`: (a) **count cap** `MAX_CONCURRENT_POSITIONS=2`, (b) **portfolio-risk cap** `PORTFOLIO_RISK_PCT=0.15` (total open defined-risk ≤ 15% of equity ≈ $435 now — the real governor), (c) **per-direction cap** `MAX_PER_DIRECTION=1` (≤1 bull / bear / neutral, prevents correlated stacking). All env-overridable (`OPENCLAW_MAX_POSITIONS`, `OPENCLAW_PORTFOLIO_RISK_PCT`, `OPENCLAW_MAX_PER_DIRECTION`). State is **live from Alpaca** (`_open_option_underlyings()` reads `/positions`) plus a crash-safe **reconciled risk ledger** `open_risk_ledger.json` (keyed by underlying; entries pruned when the position no longer shows open on Alpaca — same out-of-band-close pattern as the guardrail fix). Unledgered/legacy open positions (e.g. the TOST IC) are counted and charged a conservative fair-share so the budget never under-counts. Conviction ≥75 gate unchanged. Pure decision helpers (`_portfolio_admits`, `_order_max_loss`, `_reconcile_ledger`, `_occ_underlying`) tested in `OpenClaw/test_multiposition.py` (13/13). The hardcoded `ACTIVE_POSITIONS` list in `openclaw_scanner.py` is now cosmetic only (executor reads Alpaca live); scanner message updated accordingly. **Rationale:** accelerate track-record accumulation for graduation without stacking correlated leverage — judge graduation on risk-adjusted results + that the budget held, not raw win rate.  
**Status:** Autonomous pipeline live. TOST manual Iron Condor executed on Jun 8 — used as live-fire validation for the IC executor (4 legs filled correctly, `position_intent` auto-assigned). Found and fixed an inverted `limit_price` sign for net-credit mleg orders (IC open + IC/credit close) across `vault_updater.py`, `nova_executor.py`, `position_monitor.py` — local tests 12/12 (`test_mleg_pricing.py`), pending deploy + server verification. `vault_updater.py` gate-bypass scope fixed and locally tested (6/6) — 'approved' orders now bypass only the events-status gate, not cooling-off or conviction. `ANTHROPIC_API_KEY` configured — conviction scorer now running in `mode: api` (Claude Haiku) with verified offline fallback on error.  
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
| Host | `ubuntu@43.156.9.185` |
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

- **Equity:** $2,898.17 (paper) — includes the unrealized P&L on the open TOST IC below
- **Open positions:** 1 — TOST Iron Condor (4 legs, exp 2026-07-17), unrealized P&L **-$44** as of 2026-06-13. Confirmed open (not closed) via `check_tost_status.py` (read-only Alpaca check); no closing order exists. See `04_Trade_Journal.md` Trade 5.
- **Pending scans:** Resolved via Hermes scheduling.

---

## 11. Recent Changes (2026-06-13 session)

1. **Events Status Clearing:** Updated `approval_manager.py` so that marking an order as approved explicitly sets its `events_status` to `'clear'`.
2. **Auto-Executor Updates:** Modified `vault_updater.py` to process both `'pending'` and `'approved'` orders, bypassing safety gates for explicitly `'approved'` orders, and printing a clear warning if a pending order remains `'uncertain'` at 21:10 BKK.
3. **LLM Conviction Scorer Integration:** Documented setup to enable `_score_anthropic()` via `ANTHROPIC_API_KEY` configuration.
4. **Gate-bypass scope narrowed (fix + test):** Refactored the three safety gates in `vault_updater.auto_execute_orders()` into a standalone `_check_gates(order, cooling_off, today)` helper. `mark_approved()` (called by Hermes) only resolves Gate 2 (`events_status -> 'clear'`) — it never touches `conviction_pass` or cooling-off state. The old code let `status != 'approved'` skip *all three* gates, so an explicitly-approved order could execute even if it had failed conviction (Gate 3) or hit a recent stop-loss cooling-off window (Gate 1). Now:
   - **Gate 1 (cooling-off)** — always enforced, regardless of status.
   - **Gate 2 (events_status)** — bypassed only for `status == 'approved'` (Hermes' intended scope).
   - **Gate 3 (conviction_pass)** — always enforced, regardless of status.
   New local test: `test_vault_updater_gates.py` (6/6 passing — `python3 test_vault_updater_gates.py`).

5. **Conviction-weighted position sizing (Improvement #5):** `_calc_qty(spread_mid, conviction_score)` in `vault_updater.py` now scales the existing equity-based risk amount (`clamp(equity*5%, $200, $500)`) by a conviction tier multiplier:
   - Tier 1 (conviction 75–84): 1.0x → risk_amount in [$200, $500]
   - Tier 2 (conviction 85–94): 1.5x → risk_amount in [$300, $750]
   - Tier 3 (conviction 95–100): 2.0x → risk_amount in [$400, $1000]

   Since `conviction_pass` already gates trading at conviction ≥75, this only changes *how much* is risked, not *whether* a trade fires — higher-conviction setups (per the Claude-Haiku scorer, §11 item 3) get proportionally larger positions, up to a raised ceiling for the top tier ($1000 vs. the previous flat $500 cap). The call site passes `order.get('conviction_score', 75)`, defaulting to tier 1 if missing. New local test: `test_conviction_sizing.py` (10/10 passing — `python3 test_conviction_sizing.py`).

---

## 12. Known Issues & Risks

| Issue | Severity | Status |
|-------|----------|--------|
| `approval_manager.py approve` sets `events_status → clear` | ✅ Resolved | Updated to set events_status to 'clear' when approved. |
| position_monitor double-fire at 21:20 (via vault_updater) + 21:30 (cron) | ✅ Resolved | Confirmed 21:30 cron is for Tradier's monitor, no overlap. |
| `vault_updater` 'approved' orders bypassed conviction (Gate 3) and cooling-off (Gate 1), not just events (Gate 2) | ✅ Resolved (2026-06-13) | Refactored into `_check_gates()`; only Gate 2 is bypassable via approval. Verified by `test_vault_updater_gates.py` (6/6). |
| `ANTHROPIC_API_KEY` not set — conviction scorer runs offline-only | ✅ Resolved (2026-06-13) | Key added to `.env`/`.env.local` + `anthropic` package installed on server. `python3 conviction_scorer.py` now returns `mode: api` with Claude-Haiku reasoning (NCLH test: 78/100, "Solid bull call setup with excellent risk:reward..."). `_score_anthropic()` still falls back to `_score_offline()` on any API error (network/rate-limit/bad response), so a future outage degrades gracefully rather than blocking the scan. `openclaw_scanner.py` calls `score_conviction()` per qualifying alert (~0-5/night) — cost is negligible. |
| New candidates (CLF, SOFI, WBD, CHWY, DKNG) not yet OI-validated | 🟡 Medium | Scanner will naturally filter via OI_MIN = 500, but wasted scans if consistently failing |
| Hermes has 10 min (21:10–21:20) to resolve all UNCERTAIN tickers | 🟡 Medium | Fine for 1–2 tickers. If 4+ UNCERTAIN, vault_updater prints a warning on unresolved tick. |
| **mleg `limit_price` sign inverted for net-credit orders** (IC open in `vault_updater._build_ic_payload` and `nova_executor._build_payload`; IC/profitable-spread close in `position_monitor.auto_close_spread`) | ✅ Resolved (2026-06-13) | Verified live via `check_alpaca_order.py` against the TOST IC (`d0b87fc1-...`): order filled at net credit `-0.46`, but the submitted limit was `+0.54` — a positive limit is non-binding for a credit fill, so the 95% credit floor never applied. Alpaca convention: net DEBIT = positive `limit_price`, net CREDIT = negative. Fixed all three builders to emit the correct sign; `auto_close_spread`'s IC-close branch previously collapsed to a ~$0.01 debit limit (`max(negative * 0.95, 0.01)`), which would almost never fill — extracted as `_close_limit_price()` and fixed for both close directions. `nova_executor.cmd_execute` now also records `submitted_limit` for `_verify_fills()` slippage tracking. 12/12 local tests pass (`test_mleg_pricing.py`). `position_intent`/`position_effect` field-naming discrepancy checked and found harmless — Alpaca auto-assigns `position_intent` from `side` and ignores the unrecognized `position_effect` key. |

---

## 13. Improvement Roadmap — Next Priorities

### Immediate
1. **Monday night Hermes debrief**
   - Check Telegram logs for Hermes' 21:10 run tomorrow to verify it runs properly and auto-resolves correctly.
   - Verify fills on TOST IC in the morning report (07:30 BKK).

### Short-term (this week)
2. **IBKR MultiSort validation for new candidates**
   - Open IBKR Option Chain for CLF, SOFI, WBD, CHWY, DKNG. Check OI at ATM and ±1 strike for DTE 25–50 days.

2b. **Deploy shared market-context consumer** (STAGED 2026-06-18, see header). After Tradier's live `📡` confirms the writer→reader→consumer chain works in production, deploy `openclaw_scanner.py` + `read_macro_signal.py` + `test_macro_consumer.py` to `~/openclaw/` and confirm the `📡 Shared market_context fresh` line appears in the 21:05 scan log. This is the real call-dedup consumer (vs. Tradier's consistency-only benefit).

3. ~~**Conviction Scorer Upgrade**~~ ✅ Done 2026-06-13 — `ANTHROPIC_API_KEY` set, `_score_anthropic()` confirmed live (`mode: api`).

### Medium-term
5. ~~**IC live-fire monitoring**~~ ✅ Done 2026-06-13 — Used the already-filled TOST IC
   (`d0b87fc1-...`, 2026-06-08) as a live test case via `check_alpaca_order.py`. All
   4 legs filled correctly and `position_intent` was auto-assigned correctly by
   Alpaca. Found and fixed a real bug along the way: `_build_ic_payload`'s
   `limit_price` sign was inverted (positive instead of negative for a net-credit
   IC), making the 95% credit floor non-binding — the TOST IC filled at $0.46
   credit vs. an intended $0.54 floor. Fixed in `vault_updater.py`,
   `nova_executor.py`, and `position_monitor.py` (`auto_close_spread` /
   `_close_limit_price`). See §12 for full detail and `test_mleg_pricing.py`
   (12/12 passing).

6. **Vault_updater timing buffer (if needed)**
   - If Hermes regularly runs close to 21:20 with multiple UNCERTAIN tickers: change vault_updater cron from `20 21` to `25 21`
   - File: server crontab (`crontab -e` on ubuntu user)

7. ~~**Conviction scorer upgrade**~~ ✅ Done 2026-06-13 — see §12. Watch the first few live nightly scans to confirm `conviction_mode: "api"` shows up in `pending_orders.json` entries (not just the standalone test).

---

## 14. How to Implement Changes in a New Session

When starting a new session, tell Claude/Nova:
> "Read `/Users/SkonP/AI_Prompt/Obsidient/SkonVault/OpenClaw/OPENCLAW_CONTEXT.md` first, then help me with [task]."

### For Mac-side file edits (Python scripts, candidates.txt):
- Direct file edits via Cowork tools
- Files live at `/Users/SkonP/AI_Prompt/Obsidient/SkonVault/OpenClaw/`
- After editing: `cd /Users/SkonP/AI_Prompt/Obsidient/SkonVault && git add . && git commit -m "..." && git push origin main`
- Server pulls: `ssh ubuntu@43.156.9.185 "cd /home/ubuntu/openclaw-vault && git pull origin main"`

### Deploying the 2026-06-13 vault_updater gate fix
The fix lives in `~/openclaw-vault/OpenClaw/vault_updater.py` on the server (git-pull path), but `openclaw_scanner.py`/`vault_updater.py` actually execute from `/home/ubuntu/openclaw/`. Sync both:

```bash
scp ~/AI_Prompt/Obsidient/SkonVault/OpenClaw/vault_updater.py \
    ~/AI_Prompt/Obsidient/SkonVault/OpenClaw/test_vault_updater_gates.py \
    ubuntu@43.156.9.185:~/openclaw/

# Sanity check on server (no network calls, pure logic test)
ssh ubuntu@43.156.9.185 'cd ~/openclaw && python3 test_vault_updater_gates.py'
```

### Deploying the 2026-06-13 conviction-weighted sizing fix (Improvement #5)
`_calc_qty` in `vault_updater.py` now takes `conviction_score` and applies a tier multiplier (see §11 item 5). Same file, redeploy together with its new test:

```bash
scp ~/AI_Prompt/Obsidient/SkonVault/OpenClaw/vault_updater.py \
    ~/AI_Prompt/Obsidient/SkonVault/OpenClaw/test_conviction_sizing.py \
    ubuntu@43.156.9.185:~/openclaw/

# Sanity check on server (no network calls; Alpaca /account call fails gracefully
# and falls back to base_risk=$200, so the tier multiplier is fully testable)
ssh ubuntu@43.156.9.185 'cd ~/openclaw && python3 test_conviction_sizing.py'
```

### For server-side changes (crontab, .env, approval_manager.py):
- Delegate to Hermes via Telegram with specific instructions
- Or SSH directly: `ssh ubuntu@43.156.9.185`
- Hermes has terminal toolset and can edit files and restart services directly

### For testing/dry-runs:
- Use `nova_executor.py` on Mac: `python3 nova_executor.py dry-run <trade_id>`
- Use bash sandbox in Cowork for Python logic tests without touching live files
