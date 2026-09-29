# System Status & Handoff — 2026-06-24 (authoritative)

> Current state of the autonomous options-trading stack after the "autonomous
> hardening" session AND the "5-Agent Framework Graduation" session. Hand this to any agent as the source of truth.
>
> **Server:** `ubuntu@43.156.9.185` (path `~/tradier/`, shared `~/shared/`).
> **Constraint:** the Cowork agent CANNOT SSH from its sandbox — the USER runs all
> rsync/ssh commands. Never change live trading behavior without explicit confirmation.

---

## 1. System Architecture: The 5-Agent Framework

Three parallel systems, all on **paper** until the $16k graduation:
- **Tradier** (`~/tradier/daily_scan.py`) — the lead scanner. Tradier PROD API for
  data/chains; **execution migrated to Alpaca** (paper). Autonomous, cron-driven.
- **OpenClaw** (Alpaca) — second scanner, its own approval queue (`pending_orders.json`).
- **Guardrail (IBKR)** — risk daemon, strictest invariants. Stable.

The trading system has been refactored from isolated procedural scripts into a unified **5-Agent Framework**. All sub-systems now route through centralized, shared modules located in the `shared/` directory:

1. **Execution Layer (`Tradier/daily_scan.py` & `OpenClaw/openclaw_scanner.py`)**: 
   - Responsible for scraping options chains, evaluating deltas/IV, and constructing the mathematical spreads (e.g., $2-wide Bull Put Spreads).
2. **Research Layer (`shared/sentiment_agent.py` & `shared/uoa_detector.py`)**:
   - Vetoes mathematically viable trades if they fight institutional flow or severe news sentiment. Uses `Polygon.io` for headline sentiment and `Tradier` data for Unusual Options Activity (volume > 3x OI).
3. **Portfolio Risk Layer (`shared/portfolio_auditor.py`)**:
   - Dynamically sweeps local state JSON files across all bots (`active_trades.json`, `pending_orders.json`) to calculate global max loss (hard cap: $5,000) and prevent cross-bot directional duplication (e.g., blocking OpenClaw from longing SPY if Tradier is already long SPY).
4. **Conviction Layer (`shared/conviction_scorer.py` & `shared/iv_calibrator.py`)**:
   - Generates a 0-100 score via LLM (Anthropic) based on a detailed structural rubric.
   - The score is then passed through the `IVCalibrator`, which adjusts the raw score up or down based on the historical empirical win-rate of the current IV percentile.
5. **Post-Mortem Layer (`shared/post_mortem_agent.py` & `shared/conviction_accuracy_tracker.py`)**:
   - Analyzes closed trades from `trade_log.jsonl` using LLM categorization (`thesis_wrong`, `timing_wrong`, `black_swan`, etc.) to generate feedback loops and track if high conviction scores actually result in higher win rates.

A nightly publisher (`~/shared/market_context_writer.py`, 20:55 ICT) writes `market_context.json`; scanners consume it via `read_macro_signal.py` (stale-guarded).

## 2. Live config (current, authoritative)

In `daily_scan.py`:
- `MAX_RISK = 320` (2% of $16k) · `MAX_RISK_TIER3 = 480` · `MAX_POSITIONS = 5`
  (5 × 480 = $2,400 = 15% portfolio risk cap) · `STARTING_CAPITAL = 16000`.
- **Universe = 13 diversified ETFs:** SPY, QQQ, IWM, XLF, XLK, XLE, XLV, XLI, XLY,
  DIA, GLD, TLT, USO.
- **`BULL_PUT_ONLY = True`** — Bull-Put is the only edge (~83% WR in 2y backtest).
  Bear-Call (−$150…−$206) and Iron Condor (net-negative) are CUT. The regime router
  routes their conditions to no-trade AND both constructors hard-return `None` when
  the flag is set (defense-in-depth). Flip to False only with fresh backtest evidence.
- **Execution: fully autonomous** on Alpaca. No manual approval step. Telegram is
  notify-only (`/scan`, `/positions`, `/account`, `/log`, `/health`, `/reconcile`,
  `/status`). `/approve` does not exist; the old Tradier-sandbox executor is
  deprecated/disabled.

## 3. What changed THIS session (2026-06-24) — all deployed together

### A. Phase 3 & 4: Research Layer & Global Portfolio Auditor
- Implemented `get_sentiment()` and `check_uoa()` to actively intercept and veto trades right before execution if institutional flow or news is terrible.
- Implemented the `PortfolioAuditor` globally to ensure Tradier and OpenClaw never double-up on the same directional exposure on the same ticker, effectively treating them as one master portfolio.

### B. Live-data DRY-RUN mode (`--dry-run`)
Real Tradier data + real chains, but NO Alpaca order and NO writes to
`trade_log.jsonl`/`active_trades.json`. Removes `pending_trade.json` after so a later
real run can't pick it up. Use to validate strikes/pipeline safely:
`cd ~/tradier && python3 daily_scan.py --dry-run` (clear `active_trades.json` first if
the position gate is maxed). Flags: `TEST_MODE` (mock), `DRY_RUN` (real data, no order),
`--no-notify`.

### C. Three scanner bug fixes
1. **Clear-pending-on-veto** — if any gate vetoes, `pending_trade.json` is removed so
   it can't be fired later.
2. **Strike sanity guard** — rejects a short strike >50% from spot (bad/mock chain),
   runs before paid research calls.
3. **Per-symbol labels** — trade summary now prints the real symbol (was hardcoded "SPY").

### D. Conviction layer — fixed properly & made Advisory
- `shared/conviction_scorer.py` now treats **bull_put / bear_call as CREDIT** (reward
  high IV, accept low R:R, direction-aware market alignment). Debit logic (bull_call /
  bear_put) unchanged. Anthropic API path made credit-aware + KeyError-safe.
- `daily_scan.py` `construct_bull_put_spread` now **exports real fields** (OI, per-leg
  bid/ask, short_iv, dte, width, credit R:R `rr_credit`, `etf_above_ema20`); the
  conviction call site feeds these (no more hardcoded `long_oi:1000`, `dte:30`, and the
  old `bull_call` mislabel for a credit spread).
- **Accuracy-gated veto:** `_conviction_may_veto()` keeps conviction **ADVISORY ONLY**
  (scores/prints/logs, never blocks) until ≥20 resolved outcomes AND no calibration
  warnings. Prints `[advisory]` vs `[VETO-ENABLED]`. So an unproven model can't kill
  good trades, but it accrues the data to earn veto power.
- **Outcome loop linked:** every executed trade logs conviction↔order_id to
  `shared/conviction_log.jsonl`; `position_monitor.log_exit` now writes the **entry**
  order id as the join key (`close_order_id` kept separately). Fixed the literal `\n`
  bug in `log_conviction`. Verified end-to-end (outcome resolves; old behavior resolved 0).
- Tests: `shared/test_conviction_credit.py` (6/6).

### E. Approval-mode cleanup (system is autonomous)
- Removed misleading "reply /approve" prints in all 3 constructors → now "system
  auto-executes autonomously; Telegram is notify-only".
- Neutralized orphaned `telegram_bot.execute_pending_trade()` (pointed at retired
  Tradier sandbox) with an early deprecation return.
- Confirmed real Alpaca success already archives `pending_trade.json` → `executed_*.json`.

## 4. How the conviction model graduates to veto (for the next agent)
1. It runs live now in **advisory** mode on every bull-put candidate.
2. Each execution writes `{order_id, score, factors}` to `shared/conviction_log.jsonl`.
3. Each exit (`position_monitor`) writes `type:"exit"` + `realized_pnl` keyed on the
   entry order id.
4. `conviction_accuracy_tracker.resolve_outcomes` matches them; after **≥20 resolved
   outcomes with no calibration warning**, `_conviction_may_veto` flips to True and
   conviction begins blocking sub-75 trades automatically.
5. Run `python3 shared/conviction_accuracy_tracker.py` anytime for the accuracy digest.

## 5. Known cross-system behavior (NOT bugs)
- **Portfolio Auditor** (`shared/portfolio_auditor.py`) reads 5 stores across Tradier,
  `trading-bot`, and OpenClaw and vetoes duplicate directional exposure on the same
  ticker. `trading-bot` and Tradier run **different Alpaca accounts**, so the auditor
  is the only cross-account guard — its `max_total_risk = $5,000` aggregates BOTH
  accounts (confirm that's the intended combined ceiling).
- A 5/5 "position limit" can come from a stale `active_trades.json`; reconcile against
  the real Alpaca account if counts drift.

## 6. Open items / next steps
- **Efficiency:** replace paid Polygon with free Massive Basic + Tradier data
  (`sentiment_agent.py` / `uoa_detector.py` currently use Polygon).
- **De-dup:** `conviction_scorer.py` is duplicated in `Tradier/` and `shared/` — the
  live import path is `shared/`; the `Tradier/` copy is stale, consider removing.
- **Infra cost** ≤ ~$40/mo before turning on the paid stack (breakeven lever).
- **Graduation gate:** keep validating on paper; only fund $16k + paid stack once the
  Alpaca/cheap-infra/bull-put-only/13-ETF config has a real track record (and ideally
  conviction has earned veto power).
- SGOV cash placement: Tradier ~19 sh, IBKR ~21 sh (~$4,200 total) parked at ~3.55%.

## 7. Economics one-liner
Bull-put-only across 13 diversified ETFs at MAX_POS=5 ≈ **$574/yr net on Alpaca**
(optimistic ceiling; IV-proxy backtest, one bull regime). Barely clears a ~$48/mo
overhead — viable only on Alpaca with cheap infra. A solid track-record engine,
marginal as a standalone business.
