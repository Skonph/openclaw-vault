# Strategy Economics & Graduation Journey — Continuation Brief (2026-06-19)

> Resumes the paper-trading graduation work. Load with the 3 context files +
> `SESSION_SUMMARY_2026-06-18.md` + `SESSION_2026-06-19_RESUME.md` (infra) — THIS file
> covers the strategy-economics arc and the current real-money state.
> Server: `ubuntu@43.156.9.185` (ICT). Agent can't SSH; user runs commands.

## 1. Infrastructure done this session (deployed & verified)

- **Tradier**: scan heartbeat (observability), position-monitor `load_active()` hardening, shared-context consumer (`market_context.json`), recycle gate (14-day hold). All live.
- **OpenClaw**: shared-context consumer, multi-position portfolio policy (count/risk-budget/direction caps via live Alpaca + reconciled ledger), recycle gate. Live.
- **Guardrail (IBKR)**: fixed the `CHDIR` crash-loop (`User=guardrail`→`ubuntu`), fixed the session hang (`PositionStore.reload()` + `ExitMonitor.reconcile_from_store()` + `max_runtime_sec`), cleaned stale state. `deploy.sh` 146 tests green. Live & stable.
- **Shared**: `graduation_scorecard.py` (cost-aware, 32/32 tests) + backtest tooling.

## 2. The strategy-economics investigation (the core new work)

**Question:** can the Tradier credit-spread strategy graduate to a real account that pays for itself?

**Findings (all from `backtest.py` on real 2y SPY/QQQ/IWM + later 13 ETFs):**
1. **$ output does NOT scale with capital** — sizing is risk-capped (`MAX_RISK`/trade), so the strategy makes ~the same dollars on $2k or $16k; extra capital sits idle. Funding $16k doesn't increase output.
2. **Commissions are brutal** — at Tradier ($0.35/contract = $1.40 round-trip), commissions ate ~50% of gross. Broker choice is decisive at volume.
3. **Cut the losers** — Iron Condor (net-negative, high variance) and **Bear-Call (−$150 to −$206, confirmed loser)** both cut. **Only Bull-Put has edge: 83% WR, robust across all tests.**
4. **Fixed cost dominates at small scale** — $50/mo = $600/yr. The 3-ETF bull-put edge (~$169/yr net) is *less* than the overhead → running the automation was *worse* than just holding T-bills.
5. **🔑 BREAKTHROUGH — diversified concurrency scales the edge.** Wider 13-ETF universe **including uncorrelated GLD/TLT/USO** + raising `MAX_POSITIONS`: the gross/maxDD ratio *improved* (2.44→2.67→**2.79 @ MAX_POS=4**→2.74@5→**cliff 2.09@6**). Gross peaked at MAX_POS=5 ($1,213/2yr). Diversification (not correlated sector ETFs) is what broke the throughput ceiling.

**WINNING CONFIG (validated):** 13 diversified ETFs · **bull-put only** · **MAX_POSITIONS=5** · idle cash in T-bills.
- Edge @ MAX_POS=5: ~$610/yr gross, **net ~$574/yr on Alpaca** / ~$359 Tradier / ~$129 IBKR (179 trades/yr; broker = the big lever). maxDD 2.8% of $16k.
- **Breakeven overhead ~$48/mo on Alpaca** — viable at the $50/mo target *only on Alpaca*. Tradier's commissions make it ~$215/yr worse → **economics require Alpaca, not Tradier**, despite Tradier being the nominal lead.
- Caveat: backtest uses IV-*proxy* (realized-vol×1.2), 2y of one bull regime → treat ~$574/yr as the optimistic ceiling.

**SGOV yield verified = 3.55%** (June 2026, was assuming 5%). So T-bill carry on $16k ≈ $568/yr — slightly UNDER the $600/yr fixed cost. The "carry fully funds overhead" cushion is gone; margin is thinner. (Source: iShares SGOV page/fact sheet.)

## 3. Current real-money state (cash placement)

Real cash was sitting idle at ~0%. Parking it in **SGOV** (~3.55% risk-free) while validating:
- **Tradier real**: $2,000 → Buy **19 SGOV** @ limit ~$100.59–100.60 (~$1,911). [confirm fill]
- **IBKR real `U25439978`**: $2,200 → Buy **21 SGOV** @ limit $100.60 (~$2,113). [order reviewed; the "$528 margin" line = position requirement in a margin acct, NOT borrowing — cash-funded; "Frozen Data" = no live market-data sub, limit still protects]
- **$16k is FUTURE** — funded only after the foundation is clear.
- **Decision: do NOT run the $50/mo paid stack at ~$4,200 scale** (overhead = ~14% drag). Keep validating on free paper systems until $16k is funded AND config locked.

## 4. Live config status (NOT yet the winning config)

`daily_scan.py` currently has the **$15k/2% sizing** (`MAX_RISK=300`, `MAX_RISK_TIER3=450`, `MAX_POSITIONS=5`, `STARTING_CAPITAL=15000`) — but **still trades IC + bear-call and only SPY/QQQ/IWM**. The winning-config changes (bull-put-only, 13-ETF universe) exist only as backtest env-flags, **NOT applied to the live scanner**. Backtest tooling added: `BT_MAX_RISK/MAX_POSITIONS/CAPITAL/SYMBOLS/NO_IC/NO_BEAR/OUT/DATA_DIR` env flags, `fetch_backtest_data.py` (Tradier history→CSVs), `bt_compare.py`.

## 5. Deferred tasks (when $16k is ready)

1. **Apply the winning config to live**: cut IC + bear-call in `daily_scan.py`; expand candidate universe to the 13 diversified ETFs (SPY,QQQ,IWM,XLF,XLK,XLE,XLV,XLI,XLY,DIA,GLD,TLT,USO); `MAX_POSITIONS=5`.
2. **Tradier→Alpaca migration** — economics require it for the lead options platform.
3. **Re-validate** on the wider universe live; only turn on the $50/mo stack once $16k is funded.
4. Get infra to ≤~$40/mo (the dominant lever; $50/mo is right at breakeven).

## 6. The honest bottom line

The bull-put strategy is a **real, robust, diversifiable edge (~$574/yr ceiling)** — but small, and only barely clears its overhead, *and only on Alpaca with cheap infra*. It's a fine track-record engine, marginal as a standalone business. The disciplined plan: **park cash in T-bills now, validate on paper, and only commit the $16k + $50/mo once the Alpaca/cheap-infra/bull-put-only/13-ETF config is locked.**
