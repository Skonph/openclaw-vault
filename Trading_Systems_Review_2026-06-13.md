# Autonomous Options Trading — Cross-System Review & Action Plan
**Date:** 2026-06-13 | **Scope:** OpenClaw (Alpaca), Tradier, IBKR Guardrail

---

## 1. Snapshot

| System | Broker | Capital | Strategies | Phase |
|---|---|---|---|---|
| OpenClaw | Alpaca paper | $2,898 | Bull call / bear put debit spreads, Iron Condor (credit) | Live autonomous, IC unfired |
| Tradier | Tradier sandbox | $2,000 | Bull put / bear call credit spreads, Iron Condor | Live autonomous, first exit unverified |
| IBKR Guardrail | IBKR paper (`DUQ548647`) | TBD (`STARTING_CAPITAL`) | Debit spreads (Level 2 code-ready); credit/IC/calendar gated behind `OPTIONS_LEVEL=3` | Shadow-only — **but you now have account-level Options Level 4**, which removes the blocker |

All three run on the same box (`ubuntu@43.156.9.185`, ICT/UTC+7) with overlapping evening cron windows.

---

## 2. Top-Line Concerns

**A. IBKR Level 4 is the single biggest unrealized lever right now.** The IBKR context still says "waiting on Level 2 (Level 3 rejected, 30-day lock)." If you now have Level 4 on the account, that constraint is gone — the P0 cutover (enable `guardrail-session`/`flatten`/`report`, disable `guardrail-shadow`, set `OPTIONS_LEVEL=3`) can happen immediately. This unlocks credit spreads/ICs on IBKR too, which is the strategy that's been sitting shadow-only the whole time. Note: the guardrail's **defined-risk-only invariant stays on** even at account Level 4 — that's correct and should not be relaxed just because the account *can* write naked options.

**B. Risk-per-trade is inconsistent across systems**, which makes it hard to compare "is this system smart" results apples-to-apples:
- IBKR: 2% per trade, −5%/−10% kill-switch (MODERATE policy)
- Tradier: up to $200 risk on $2,000 = **10%** per trade
- OpenClaw: no explicit %-of-equity cap stated in the rules block — sizing is via `_calc_qty()`, worth confirming it's capped similarly

A 10% per-trade risk on Tradier means 2-3 bad trades in a row materially changes the "is this profitable" verdict — too much variance for a clean read on whether the *system* (vs. variance) is working.

**C. Correctness gaps could quietly corrupt the proof-of-concept data** before you've drawn any conclusion:
- OpenClaw: `approval_manager.py approve` may not flip `events_status → clear`; Hermes has only a 10-minute window to resolve multiple UNCERTAIN tickers; IC live-fire path is still unverified end-to-end; possible double-fire of `position_monitor` at 21:20/21:30.
- Tradier: the #1 listed pending item is literally "verify exit actually works on first live trade" — until that's confirmed, you can't trust any P&L the bot reports, because a stuck position would silently distort win-rate.
- IBKR: shadow tracker only marks once/day (not fill-accurate), so its track record is indicative, not a true proxy for live execution.

**D. No real backtesting anywhere.** IBKR's backtest engine uses synthetic GBM paths (not real historical bars). OpenClaw and Tradier have no backtest at all. This means every system is relying purely on forward paper-trading to "prove intelligence" — which is the slowest possible way to get a statistically meaningful sample (you need ~30-50+ trades per system before win rate / profit factor numbers mean anything).

**E. "Intelligence" is underused.** Only IBKR uses an actual LLM (Haiku 4.5 via OpenRouter) to generate trade plans. OpenClaw's `conviction_scorer.py` has an `_score_anthropic()` path but is falling back to offline/rule-based scoring (no API key set). Tradier appears to be pure rule-based with no LLM scoring step at all. If the core question is "can an LLM-driven system find edge," two of three systems aren't really testing that yet.

**F. Server contention.** OpenClaw scanner (21:05), Hermes resolver (21:10), vault_updater (21:20), Tradier scan (21:15, Tue-Thu), Tradier monitor (21:30), and IBKR strategist/shadow (21:10/21:15 ICT) all fire within a ~25-minute evening window on one box that's also running a headless IB Gateway under Xvfb. Worth confirming none of these are starving each other for CPU/memory, especially once IBKR goes live and adds a real execution + flatten timer to that window.

---

## 3. Fee-Coverage Math — Does Each Trade Clear Its Costs?

Per-contract round-trip costs (open + close), based on current published schedules:

| Broker | Commission | Reg/clearing fees | Approx. round-trip cost, 1 contract |
|---|---|---|---|
| Alpaca | $0 | ORF ~$0.002-0.02/side + OCC $0.025/side | **~$0.10-0.20** (2-leg) / ~$0.20-0.35 (4-leg IC) |
| Tradier (live, standard) | $0.35/contract/side | minor exchange/reg fees | **~$1.40-1.60** (2-leg) / ~$2.80-3.00 (4-leg IC) |
| IBKR Pro | ~$0.65/contract, **$1.00 min/order** | OCC $0.025 + ORF ~$0.002/side | **~$2.60-2.80** (2-leg) / ~$5.20-5.60 (4-leg IC) |

Now compare to each system's actual profit-per-contract at the stated 50% profit target:

| System | Premium target | Notional (×100) | 50% profit target (gross) | Round-trip fee | Fee as % of profit |
|---|---|---|---|---|---|
| OpenClaw (Alpaca, debit spread, $0.30-0.60) | $0.30-0.60 | $30-60 | $15-30 | ~$0.10-0.20 | **~0.5-1.5% — negligible** |
| OpenClaw IC (4-leg, Alpaca) | similar | $30-60 | $15-30 | ~$0.20-0.35 | **~1-2% — fine** |
| Tradier credit spread, $1-wide ($0.30 credit floor) | $0.30 | $30 | $15 | ~$1.50 | **~10% — tight but workable** |
| Tradier IC / wider spreads ($0.75 credit on $5 width) | $0.75 | $75 | $37.50 | ~$3.00 | **~8% — workable** |
| IBKR debit spread (Level 2/3, similar premium $0.30-0.60), 1 contract | $0.30-0.60 | $30-60 | $15-30 | ~$2.60-2.80 | **~10-19% — significant drag** |
| IBKR 4-leg IC, 1 contract | $0.30-0.60 | $30-60 | $15-30 | ~$5.20-5.60 | **~19-37% — IBKR eats a large share of the edge at 1 contract** |

**Bottom line:** Alpaca's fee structure is essentially free for this strategy size — OpenClaw's economics are sound as-is. Tradier is workable but benefits from avoiding $1-wide spreads. **IBKR is the outlier**: at 1 contract and small premiums, IBKR's $1/order minimum can eat 10-37% of the gross profit target. Two fixes, both already partially supported by the existing code:

1. **Scale contract count when conviction is high** (Tradier already does this via dynamic sizing — port the same logic to IBKR). At 2 contracts, the fixed $1/order minimum is amortized over double the premium, roughly halving the fee drag.
2. **Raise the minimum acceptable premium on IBKR** — e.g., don't take IBKR trades below ~$0.50-0.75 credit/debit per contract, or require ≥2 contracts, so fees stay under ~10% of target profit. This is a one-line threshold change once `OPTIONS_LEVEL=3` is live.

API cost (OpenRouter Haiku 4.5, ~2 calls/day) is negligible — well under $1/month even across all three systems — so it's not a factor in per-trade economics.

---

## 4. Per-System Resolution Plan

### OpenClaw (Alpaca)
| Priority | Item | Fix |
|---|---|---|
| 🔴 High | `approval_manager.py approve` may not set `events_status → clear` | Add explicit assertion/log after Hermes approve; have `vault_updater.py` log a warning if it skips an order still marked `uncertain` after 21:10 |
| 🔴 High | IC 4-leg live-fire unverified | Wait for next `flat_elevated` regime (VIX 18-30, flat SPY) and manually confirm the mleg order + fills in `morning_report.py` |
| 🟡 Medium | Possible double-fire of `position_monitor` at 21:20/21:30 | SSH and confirm the 21:30 cron entry points at the Tradier copy, not `/home/ubuntu/openclaw/position_monitor.py` |
| 🟡 Medium | Conviction scorer running offline fallback | Set `ANTHROPIC_API_KEY` in `.env` to enable `_score_anthropic()` — directly addresses "is the system intelligent enough" |
| 🟢 Low | New candidates (CLF, SOFI, WBD, CHWY, DKNG) not OI-validated | Let `OI_MIN=500` filter naturally; revisit only if scans repeatedly skip them |

### Tradier
| Priority | Item | Fix |
|---|---|---|
| 🔴 High | End-to-end exit test unverified | On first live position, manually watch `position_monitor.py --test` and confirm it reads `active_trades.json` and submits BTC on trigger — this gates trusting *any* P&L number from this system |
| 🟡 Medium | 10% risk-per-trade ($200 / $2,000) | Consider tightening to ~5% ($100) to reduce variance and make win-rate/profit-factor readings converge faster with fewer trades |
| 🟡 Medium | No LLM scoring layer | Add a lightweight Haiku-based conviction check (reuse IBKR's `strategist_prompt.md` pattern) before auto-execution |
| 🟢 Low | No backtest | Lowest-effort path: feed Tradier's own historical chain data into IBKR's `backtest.py` engine once it's upgraded to real bars (see §5) |

### IBKR Guardrail
| Priority | Item | Fix |
|---|---|---|
| 🔴 Highest | **Options Level 4 now available** — P0 cutover was blocked on "waiting for Level 2"; that's resolved | Verify `managedAccounts()` returns `DUQ548647`; set `OPTIONS_LEVEL=3` (unlocks credit spreads/IC/calendar in `risk_policy.py` + strategist prompt); enable `guardrail-session`, `guardrail-flatten`, `guardrail-report`; disable `guardrail-shadow`; subscribe OPRA market data |
| 🔴 High | Fee drag at 1 contract (see §3) | Add contract-count scaling (mirror Tradier's VIX/score-based 1-2 contract logic) and/or raise minimum premium threshold to ~$0.50-0.75 in `risk_policy.py` |
| 🟡 Medium | Combo P&L marking is simplified; IV feed is a stub | P1 items — needed before trusting live exit triggers on invalidation rules |
| 🟡 Medium | Backtest uses synthetic GBM | Swap in real Tradier historical bars (already available via `tradier_feed.py`) + model commissions/slippage — this gives you a much faster statistical read than waiting on live paper trades |
| 🟢 Low | Marked-equity kill-switch missing (only realized equity triggers halt) | Add before going live, since open losses can exceed −10% before a realized-equity check would catch it |

---

## 5. Performance / "Win the Market" Improvement Plan

The honest framing from the IBKR context is the right one to apply to *all three*: the goal isn't "guarantee 80% win rate," it's **bounded risk + honest measurement of edge**. With that said, here's what actually moves the needle toward consistent, fee-covering profit:

1. **Make all three systems LLM-driven for trade selection/sizing**, not just IBKR. The IBKR `strategist_prompt.md` + Haiku 4.5 pattern is the most "intelligent" piece you have — port it (or at minimum, enable OpenClaw's `_score_anthropic()` and add an equivalent to Tradier). This is the most direct way to test the actual premise of the project.

2. **Real backtesting, one engine, three data sources.** Take IBKR's `backtest.py`/`bs.py` framework, feed it real historical bars from `tradier_feed.py` (already used by both OpenClaw and IBKR), and run each system's rule set against 1-2 years of history. This gives you a statistically meaningful read on expectancy *in days*, not months of paper trading — and tells you which thresholds (IV rank, delta range, DTE, credit floors) are actually load-bearing vs. arbitrary.

3. **Define a graduation scorecard before judging "is it intelligent enough."** Track per system: win rate, profit factor, average R-multiple, max drawdown, and number of trades — and don't draw conclusions below ~30 closed trades. Compare each system against a dumb baseline (e.g., sell the same structure on a fixed schedule regardless of signal) to isolate how much edge the "intelligence" is actually adding over a naive theta-harvest.

4. **Standardize risk-per-trade to ~2% (IBKR's MODERATE policy) across all three.** This makes the three systems' results directly comparable and reduces variance enough that 20-30 trades starts to mean something.

5. **Fix the fee-drag asymmetry (§3) before scaling IBKR up** — once Level 3 is live there, bias toward IC/credit structures with ≥$0.50 premium or 2-contract sizing so fees stay under ~10% of target profit, matching Tradier/Alpaca economics.

6. **Build a single cross-system dashboard.** All three already write structured logs (`trade_log.jsonl`, `pending_orders.json`, `shadow_ledger.json`). A single HTML/artifact dashboard pulling all three would let you see, at a glance, whether *any* of the three approaches is pulling ahead — useful for deciding where to concentrate capital and dev effort.

---

## 6. Prioritized Roadmap — Do This First

**This week:**
1. Confirm IBKR account Level 4 status reflects in `.env`/`OPTIONS_LEVEL`; run the P0 cutover checklist (managedAccounts check → enable session/flatten/report timers → disable shadow → set `OPTIONS_LEVEL=3`).
2. Verify Tradier's first live exit end-to-end — don't trust its P&L numbers until this passes.
3. Confirm OpenClaw's 21:20/21:30 monitor cron isn't double-firing, and verify Hermes' `events_status` flip actually happens on the next run.

**Next 1-2 weeks:**
4. Enable `ANTHROPIC_API_KEY` for OpenClaw's `_score_anthropic()`; add an equivalent LLM scoring step to Tradier.
5. Add contract-scaling / premium-floor adjustments to IBKR so fee drag stays under ~10% once Level 3 trades start flowing.
6. Watch the first IBKR IC/credit spread fire under Level 3 and confirm `_verify_fills` / exit monitor behave correctly.

**Ongoing:**
7. Stand up the real-data backtest engine and run all three rule sets against 1-2 years of history — this is your fastest path to a statistically credible answer to "is this system smart enough," independent of how many paper trades have accumulated.
8. Track the graduation scorecard (§5.3) weekly across all three systems; revisit risk-per-trade standardization once you have enough data to compare.

---

## 7. Other Notes

- **Capital fragmentation:** $2,898 + $2,000 + IBKR paper across three separate experiments is reasonable for a proof-of-concept (you're testing three architectures, not just three accounts), but once one system shows a credible edge in the backtest + scorecard, consider concentrating dev effort (and eventually capital) there rather than maintaining three in parallel indefinitely.
- **Overfitting risk:** between the three systems there are dozens of tunable thresholds (IV rank caps, delta ranges, DTE windows, credit floors, conviction minimums). With small live samples, it's easy to mistake a few lucky/unlucky trades for "the threshold is wrong." The backtest (item 7 above) is what lets you tune these against real data instead of paper-trade noise.
- **Telegram fragmentation:** three separate bots/tokens is fine technically, but worth a quick gut-check that you're not missing signals across three channels — the unified dashboard (§5.4) would reduce reliance on catching everything in Telegram.

---

## 8. Update (same day, post-changes)

Status after the latest round of edits:

- **IBKR**: Level 4 approved, `OPTIONS_LEVEL=3` live, P0 cutover complete, real-history backtest live, $0.50 premium floor + VIX-based 1-2 contract scaling in place (fixes the fee-drag math in §3). **This is now the live-paper system to watch closely.**
- **Tradier**: risk-per-trade cut to 5% ($100). End-to-end exit test is now the **single highest-priority open item across all three systems** — don't trust its P&L until verified.
- **OpenClaw**: Hermes `events_status` bug fixed. Conviction scorer still offline-fallback (no `ANTHROPIC_API_KEY`) — lowest-effort remaining "intelligence" gap. IC live-fire still pending a `flat_elevated` regime.

**New priority order:**
1. IBKR P1 (combo P&L marking, real IV feed, mid-price combo fills) — now urgent since real orders are firing.
2. IBKR P2 marked-equity kill-switch — open positions can now exceed -10% before a realized-equity check would catch it.
3. Tradier exit-test verification.
4. OpenClaw `ANTHROPIC_API_KEY` for `_score_anthropic()`.
5. Check server load/contention during 21:10-21:30 ICT now that IBKR's live `guardrail-session` overlaps Tradier's scan and OpenClaw's Hermes window.

---

*Sources for fee figures: [IBKR Options Commissions](https://www.interactivebrokers.com/en/pricing/commissions-options.php), [IBKR ORF](https://ibkr.info/node/1184), [Alpaca Options Fees](https://alpaca.markets/support/what-are-the-commission-fees-per-option-contract), [Tradier Pricing](https://tradier.com/individuals/pricing).*
