# IBKR Options Autotrader — Project Context & Handoff

> **Purpose of this file.** Drop this into a new Cowork/Claude chat to continue the
> project. It captures what's built, where it runs, the conventions/gotchas, and a
> prioritized improvement backlog mapped to specific files. A fresh agent should be
> able to read this and keep improving the system without re-deriving everything.
>
> **Current version:** v13 · **Tests:** 142 passed.
> **Phase:** LIVE-PAPER & STABLE — strategist + reporting live; **execution restored 2026-06-18, session-hang fixed + deployed 2026-06-19** (see critical finding below). `guardrail-session` + `guardrail-flatten` on IBKR paper DUQ548647 via IB Gateway (port 7497). 146 tests green.
> **Last updated:** 2026-06-19 (BKK)
>
> **🔴 CRITICAL FINDING + FIX (2026-06-18): execution had NEVER run since the 06-16 "go-live."** `guardrail-session.service` was crash-looping (`status=200/CHDIR`, 5,450+ failed restarts every 10s) and `guardrail-flatten.service` shared the bug. Root cause: both units were set to `User=guardrail`, but their `WorkingDirectory`, venv, `run_ops_session.py`, `.env`, and logs all live under `/home/ubuntu/guardrail` (mode `700`, owned by **ubuntu**) — so the `guardrail` user couldn't `chdir` in. The brain worked (`guardrail-strategist`/`guardrail-report` exit 0, run as ubuntu) so plans were generated nightly, but **nothing ever opened or flattened them** — "fully autonomous live" was on paper only. **Fix:** drop-in overrides `/etc/systemd/system/guardrail-{session,flatten}.service.d/override.conf` setting `User=ubuntu` (matching the working siblings) + `daemon-reload`. `Restart=on-failure` left as-is (intentional per unit comment; the loop was only the CHDIR failure).
**✅ VERIFIED LIVE 2026-06-18 night:** the 21:15 session ran for real — connected to IB Gateway, monitored 1 open position, polled equity ($100,000 paper, day +0.00%) every ~34s; all three units (`session`/`flatten`/`strategist`) exit `0`. Execution is genuinely live for the first time.
**✅ First real trade (06-18 night):** strategist→session opened a **SPY 735/725 debit put spread** (1 lot, $1,300 max loss = 1.3%, within the 2% policy); invalidation (underlying<723) never hit; the **02:50 flatten force-closed it** (`session_positions.json` → `status:CLOSED`, `close_reason:EOD_FLATTEN`, realized **−$130**). The open→hold→EOD-flatten pipeline works.

**🟢 BUG FIXED 2026-06-19 (in vault source; pending deploy) — session process hung + state desync.** Root cause: `run_ops_session.py` (via `ExitMonitor.run_forever`) holds an **in-memory `PositionStore`** that `open_positions()` never re-reads from disk, while `flatten_all.py` closes the position in a **separate process** by rewriting `session_positions.json`. So the session never saw the book go flat → `run_forever` looped forever (no EOD/max-runtime guard either) → hung ~14h holding `session.lock`, and kept re-saving stale `session_state.json` (`open_positions:1`, equity not debited the −$130). **Fix applied (complete — hang + state consistency):** (1) `positions.py` — added `PositionStore.reload()` (crash-safe re-read; keeps in-memory copy if the file is mid-write). (2) `exit_monitor.py` — `run_forever` now `reload()`s + calls new `ExitMonitor.reconcile_from_store()` each tick, and added `ExitConfig.max_runtime_sec=23400` (~6.5h) safety stop. (3) `reconcile_from_store()` makes `AccountState` a faithful projection of the store: it **books the realized P&L of any out-of-band close (e.g. by flatten_all.py) into equity exactly once** (tracked via `self._booked_closed`, seeded with already-CLOSED plans at construction so historical closes are never re-booked), then recomputes `open_positions`/`deployed_usd` from what's actually open. Because both the session and flatten compute equity as base + Σ(realized in store), they **converge to the same value regardless of write order** — eliminating the cross-process drift (no more stale equity / `open_positions:1`). (4) `test_session_reload.py` — 4 tests (reload sees out-of-band close; corrupt-file tolerance; equity booked once + idempotent; pre-existing closes not re-booked). Validated locally: 21 passed (lone failure is `ib_async` missing in sandbox — passes on server) + py_compile clean. **✅ DEPLOYED 2026-06-19** — `deploy.sh` ran **146 tests green** + readiness OK (openrouter/haiku, telegram OK, ibkr 127.0.0.1:7497 paper_only, auto / $100k). One-time `session_state.json` cleanup applied (equity → **$99,870** booking the −$130 the hung session never recorded; `open_positions`/`deployed_usd`/`unrealized_pnl` → flat; day/week anchors $100k → day drawdown −0.13%). Next 21:15 session runs the fixed code, lets flatten close at 02:50, sees the flat book, and **self-exits cleanly with correct equity — stable autonomous operation, no manual cleanup.** (The session-owns-flatten single-owner refactor remains an optional future simplification, no longer needed for correctness.)
>
> **2026-06-18 — Shared market-context layer (guardrail = pending 3rd consumer):** A cross-system shared context already exists — `~/shared/market_context.json`, written nightly 20:55 ICT by `market_context_writer.py` (VIX/SPY/QQQ/IWM quotes + per-symbol trend/SMA/ATR + regime + calendar_skip + portfolio snapshot). Until 2026-06-18 nothing consumed it. Tradier now consumes it (deployed); OpenClaw consumer is staged. A freshness-guarded reader `read_macro_signal.py` (returns None if missing/stale → caller falls back to its own fetch) is the safe consumption pattern. **Guardrail is the designated 3rd consumer — wire LAST and LIGHTEST:** use it only as a redundancy/cross-check for the VIX/quote block of `context.json`; keep `tradier_feed` trend signals + `econ_calendar` authoritative, and NEVER route order logic through it (per §1 invariants). See vault `SESSION_SUMMARY_2026-06-18.md` + `shared/INTEGRATION.md`.


---

## 1. Mission & honest guardrails (read first)

The system is an **autonomous options strategist + risk guardrail** for US index
ETF options (SPY/QQQ/IWM), running paper-first on IBKR.

It does **NOT** promise an 80% win rate or 20% returns — that target is unattainable
and any change request implying it should be pushed back on. What it actually targets:
**bounded, defined risk per trade + honest measurement of edge.** Discipline (declining
on no-edge days) is a feature, not a bug. Keep this framing in all future work.

Hard invariants that must never be weakened:
- **Defined-risk only.** Naked/undefined-risk structures are always rejected.
- **2% max loss per trade, −5% daily / −10% weekly kill-switch** (MODERATE policy).
- **Paper-account gate:** the IBKR executor refuses any account not starting `DU`/`DF`.
- **Permission-aware:** the guardrail only allows structures IBKR has approved
  (`OPTIONS_LEVEL` env; set to 3 = allows credit spreads, Iron Condors, etc., thanks to account upgrade to Level 4).

---

## 2. Architecture & data flow

```
Evening (08:15 UTC / 15:15 ICT)              Pre-session (08:30 UTC / 15:30 ICT)
  context_builder.py                           shadow_report.py
   ├─ Tradier quotes + ATM IV                    ├─ parse strategist_output.json
   ├─ Tradier daily history -> trend signals     ├─ guardrail-evaluate each plan
   ├─ econ calendar (manual FOMC -> FRED)         ├─ open shadow positions (tracker)
   └─ writes data/context.json                    └─ Telegram: would-trade + record
  strategist_run.py
   └─ OpenRouter (Haiku 4.5) -> data/strategist_output.json

Morning (01:30 UTC / 08:30 ICT)              [WHEN EXECUTION IS LIVE]
  daily_report.py                              run_ops_session.py (session timer)
   ├─ mark+close shadow positions (BS)           ├─ open approved plans on IBKR paper
   └─ Telegram: recap + shadow track record      ├─ exit monitor until flat
                                                 ├─ exit_monitor marked-equity check
                                                 flatten_all.py (before close)
                                                 daily_report.py (real trade recap)
```

Core pipeline (broker-agnostic): **strategist → strategist_bridge → guardrail →
(executor) → exit_monitor**. The backtest harness and shadow tracker reuse the same
guardrail + exit rules, so what's measured equals what would trade.

---

## 3. Repository layout (`options_guardrail/`)

**Risk & contract core**
- `risk_policy.py` — MODERATE limits; `OPTIONS_LEVEL` gates allowed structures
  (LEVEL2_STRUCTURES = long/debit only; LEVEL3 = +credit/condor/calendar). `ACTIVE_POLICY`.
- `schema.py` — `TradePlan`/`OptionLeg`/`Invalidation` contract + `from_dict` parser.
- `guardrail.py` — `Guardrail.evaluate(plan, state) -> GuardrailResult` (kill-switch,
  structure check, defined-risk, invalidation, sizing to 2% cap, portfolio caps).
- `state.py` — `AccountState` (equity, day/week anchors, drawdown, JSON persistence).
- `positions.py` — open-position model + JSON store.

**Execution & exits**
- `ibkr_paper_executor.py` — thin IBKR adapter (ib_async). Paper-gated. `execute`/`close_position`.
- `market_data.py` — `MarketDataProvider` protocol; `MockMarketData`; `IBKRMarketData`; `YahooMarketData` (REST quotes from Yahoo Finance); `FallbackMarketData` (chains Tradier ➡️ IBKR ➡️ Yahoo).
- `exit_monitor.py` — closes on invalidation / stop (85% max loss) / target; books P&L into state.

- `pipeline.py` — `SessionOrchestrator` (open approved + run exit loop; notifier/approver hooks).
- `run_ops_session.py` — live-paper entrypoint (config → Telegram → IBKR → orchestrator, file lock).
- `flatten_all.py` — EOD safety net: force-close everything before US close.

**Strategist & data feeds**
- `strategist_prompt.md` — the Opus/Haiku system prompt (emits TradePlan JSON; reads
  regime from `daily_signals`; honors ALLOWED STRUCTURES injected at runtime).
- `strategist_run.py` — builds prompt+context, calls model (OpenRouter or Anthropic),
  writes `strategist_output.json` (fail-safe to no_trade).
- `strategist_bridge.py` — tolerant JSON parse + `evaluate_envelope` through guardrail.
- `context_builder.py` — assembles `context.json` (account + Tradier flow/IV/trend + econ).
- `tradier_feed.py` — Tradier quotes, ATM IV, market clock, **daily history → trend signals**
  (momentum 5/10d, %vs SMA20, ATR, trend label). Self-check CLI. DATA-ONLY (never trades).
- `econ_calendar.py` — economic calendar: **Manual (data/manual_calendar.json) → Finnhub →
  FRED** fallback. Manual is per-date authoritative. Self-check CLI.

**Reporting & tracking**
- `telegram_notify.py` — notify + (semi-mode) approve. Markdown→plaintext fallback. NullTelegram.
- `shadow_report.py` — interim "would-trade" digest; opens shadow positions.
- `daily_report.py` — recap; marks+closes shadow positions; shows shadow track record.
- `shadow_tracker.py` — **shadow-performance tracker**: open plans, mark daily via BS,
  close on same exit rules, running hypothetical P&L (`data/shadow_ledger.json`).
- `preflight.py` — connectivity check (Tradier + OpenRouter + Telegram) → Telegram.

**Backtest**
- `bs.py` — Black-Scholes pricer. `backtest_data.py` — BS combo marking. `strategy.py` —
  default momentum strategy. `backtest.py` — engine + metrics (win rate, expectancy, PF,
  max DD). `backtest_run.py` — report writer.

**Ops & config**
- `config.py` — env-driven config (model provider, Telegram, IBKR, Tradier, econ keys,
  OPTIONS_LEVEL, equity from STARTING_CAPITAL, data_dir).
- `run.sh` — cron/systemd wrapper: sources `.env` (bash, strips inline comments) then runs
  a script in the venv. **All systemd services use this** (avoids EnvironmentFile pitfalls).
- `deploy.sh` — venv + deps + `pytest` + readiness summary.
- `.env.example` — template (copy to `.env`, chmod 600).
- `ops/` — systemd units + timers, `ibc-setup.md` (IB Gateway+IBC headless), `crontab.txt`,
  `RUNBOOK.md` (operator guide; has a "Kill it now" section).
- `manual_calendar.example.json`, `manual_calendar.2026_fomc.json` — econ-calendar seeds.
- `test_*.py` — 142 tests across all components (including `test_fallback_market_data.py`, `test_guardrail.py`, and `test_ibkr_paper_executor.py`).
- `HERMES.md` — Briefing and backlog for peer agent collaboration (e.g. hermes).

---

## 4. Deployment topology (the live server)

- **Host:** `ubuntu@43.156.9.185` (Tencent Cloud, **timezone ICT/UTC+7**).
- **Install dir:** `/home/ubuntu/guardrail` (NOT `/opt` — that vanished once; home is durable).
- **Secrets:** `/home/ubuntu/guardrail/.env` (chmod 600). Data: `data/`. Logs: `logs/`.
- **Systemd units run as `User=ubuntu`** and use `run.sh` for ExecStart (and ExecStartPre
  for context build). Paths point at `/home/ubuntu/guardrail`.
- **Timers are pinned to UTC** (`OnCalendar=... UTC`) so ICT system clock doesn't shift them.
- **Headless IB Gateway:** Configured via `ibc-gateway.service` running `/opt/ibc/scripts/ibcstart.sh` in the foreground under `xvfb-run` using bundled Zulu OpenJDK 17. Port 7497 (paper).


**Timer schedule (UTC → ICT):**

| Unit | UTC | ICT | Status |
|---|---|---|---|
| guardrail-strategist (context+strategist) | 08:30 & 14:10 Mon-Fri | 15:30 & 21:10 | **ENABLED (Twice Daily)** |
| guardrail-shadow (shadow report) | 08:45 & 14:15 Mon-Fri | 15:45 & 21:15 | **DISABLED (2026-06-16 — live mode)** |
| guardrail-report (recap + shadow track record) | 01:30 Tue-Sat | 08:30 | **ENABLED** |
| guardrail-session (live execution) | 14:15 Mon-Fri | 21:15 | **ENABLED (2026-06-16)** |
| guardrail-flatten (EOD flatten) | 19:50 Mon-Fri | 02:50+1 | **ENABLED (2026-06-16)** |

---

## 5. Services & credentials (in `.env`)

- **Model:** OpenRouter (`OPENROUTER_API_KEY`), `STRATEGIST_MODEL=anthropic/claude-haiku-4.5`.
  (Provider auto-detects OpenRouter when its key is present.)
- **Telegram:** dedicated bot token, `TELEGRAM_CHAT_ID=8069530075`.
- **Tradier:** `TRADIER_ENV=prod`, `TRADIER_PROD_TOKEN` (real-time quotes/IV/history). DATA ONLY.
- **Econ calendar:** `FRED_API_KEY` (works, used), `FINNHUB_API_KEY` (calendar endpoint
  gated on free tier → falls back to FRED). Manual FOMC dates in `data/manual_calendar.json`.
- **IBKR:** account `U25439978` (live, options **Level 4** approved!). Paper account `DUQ548647`. `OPTIONS_LEVEL=3` set to enable credit/IC/debit spreads.
- **Equity basis:** `STARTING_CAPITAL` (or `GUARDRAIL_EQUITY`).

---

## 6. Operational status & daily cadence (what's happening now)

LIVE-PAPER FULLY AUTONOMOUS phase (from 2026-06-16) — shadow mode retired, all execution timers live:
- Nightly: context built from real Tradier data (incl. multi-day trend) + econ calendar;
  Haiku strategist produces Level-3 plans (credit spreads, Iron Condors, calendars, debit spreads).
- 21:15 ICT: `guardrail-session` opens approved plans on IBKR paper account `DUQ548647` via IB Gateway (port 7497, confirmed connected). Runs exit monitor until flat.
- 02:50+1 ICT: `guardrail-flatten` force-closes any remaining positions before US close.
- 08:30 ICT: `guardrail-report` posts daily recap to Telegram.

IB Gateway connectivity confirmed 2026-06-16: `ib_async` installed, port 7497 listening, `Connected OK: True`.

Known-good behaviors observed: strategist correctly reads SPY/QQQ/IWM uptrend and produces debit call spreads; correctly returns no_trade on directionless low-IV days.

---

## 7. Conventions & gotchas (hard-won — respect these)

1. **Deploy via full sync, preserve server edits.** Build a zip of the project, scp it,
   then `rsync -a --exclude 'ops/' ~/stage/options_guardrail/ ~/guardrail/`. This keeps all
   `.py`/tests in lockstep (avoids stale-test failures) while preserving the server's edited
   `ops/*.service|timer` and untouched `.env`/`data/`.
2. **systemd EnvironmentFile ≠ bash source.** systemd keeps inline `# comments` in values and broke `OPENROUTER_API_KEY`. Fix: **all services ExecStart go through `run.sh`**.
3. **Timezone:** server is ICT; timers are pinned with `... UTC`. Tests derive date keys from `datetime.now(timezone.utc)`.
4. **Telegram Markdown:** dynamic content breaks Telegram's Markdown parser. `telegram_notify.notify()` retries as plain text.
5. **`/opt` is not reliable on this box; use `/home/ubuntu/guardrail`.**
6. **Pre-open quotes look flat (0.00%).** Read the regime from `overnight_flow.daily_signals`, NOT the spot quote.
7. **Tradier prod token can trade the live account.** Never add an order method to `tradier_feed.py`.
8. **Shadow tracker marks once/day** (daily granularity).
9. **The IBKR adapter is paper-gated** (`DU`/`DF` only).
10. **Headless Java / systemd service gotcha:** Run `/opt/ibc/scripts/ibcstart.sh` directly in the foreground, using the bundled Zulu OpenJDK JRE instead of system's headless Java runtime.
11. **IB Gateway API socket won't open without `SocketPort` in `jts.ini`.** `AcceptIncomingConnectionAction=accept` in `/opt/ibc/config.ini` is necessary but not sufficient — IB Gateway also needs `SocketPort=7497` (paper) explicitly set in `/home/guardrail/Jts/jts.ini`. Without it the Gateway runs but port 7497 never binds. Fixed 2026-06-16 via `sudo sed -i '/^ApiOnly=true/a SocketPort=7497' /home/guardrail/Jts/jts.ini` + service restart.
12. **`ib_async` must be installed in the ubuntu user's env.** The venv ships it but the server's system Python doesn't include it. Install once with `pip install ib_async --break-system-packages` when setting up a new machine.

---

## 8. Test & deploy workflow

```bash
# local edit → build zip → deploy on server
scp options_guardrail_vX.zip ubuntu@43.156.9.185:~/
ssh ubuntu@43.156.9.185
unzip -o ~/options_guardrail_vX.zip -d ~/stage
rsync -a --exclude 'ops/' ~/stage/options_guardrail/ ~/guardrail/ && rm -rf ~/stage
cd ~/guardrail && ./deploy.sh           # expect: 142 passed + readiness OK
```

---

## 9. Improvement backlog (prioritized; file → change)

**P0 — Execution cutover & Level 3 Expansion (Completed ✅)**
- Verified `managedAccounts()` returns `DUQ548647` and configured services (`guardrail-session`, `guardrail-flatten`, `guardrail-report`).
- Enabled credit/IC/calendar structures with `OPTIONS_LEVEL=3` set.
- Enforced `$0.50` premium floor + VIX-based 1-2 contract scaling (resolving fee-drag concerns).
- Replaced synthetic GBM paths with real historical bars in the backtester.

**P1 — Make live execution trustworthy (Completed ✅)**
- `market_data.py`: `IBKRMarketData.position_pnl` rewritten to per-leg valuation (qualify each leg, snapshot bid/ask/last/close, net against `entry_net_price`), with a stderr diagnostic cross-check against IBKR portfolio `unrealizedPNL`.
- `market_data.py`: `IBKRMarketData.implied_vol` now tries Tradier ATM IV first, falling back to IBKR generic tick 106 on the underlying.
- `ibkr_paper_executor.py`: `_get_combo_mid` prices combos at the bid/ask mid ("split the spread"). **v13 fix:** the mid calc previously required `bid > 0 and ask > 0`, which silently rejected the negative bid/ask that net-credit combos (credit spreads, Iron Condors) legitimately quote on the BAG — exactly the structures `OPTIONS_LEVEL=3` unlocked — and fell back to `plan.net_price`/MKT. Now only NaN and the `(0, 0)` "no data" sentinel are rejected; negative mids compute correctly. Covered by new tests `test_execute_uses_negative_mid_for_credit_combo`, `test_get_combo_mid_handles_negative_bid_ask_for_credit_combo`, `test_get_combo_mid_treats_zero_zero_as_no_data` (142 tests total).

**P2 — Risk completeness (marked-equity kill-switch) (Completed ✅)**
- Added `use_marked_drawdown=True` support to check both realized + unrealized P&L against daily/weekly anchors inside `guardrail.py` and `exit_monitor.py`. Configurable via `.env`.

**P3 — Tradier exit-test verification**
- `position_monitor.py` (Tradier): End-to-end exit test verification is the single highest-priority open item across all three systems. P&L reported by the bot must not be trusted until confirmed working.

**P4 — OpenClaw Intelligence Gap**
- `conviction_scorer.py` (OpenClaw): Enable `_score_anthropic()` by configuring `ANTHROPIC_API_KEY` in `.env` to resolve the offline-fallback intelligence gap.

**P5 — Server load / contention check** ⚠️ NOW ACTIVE
- Check CPU/memory contention on the box during the 21:10–21:30 ICT window — `guardrail-session` (live, enabled 2026-06-16) now overlaps Tradier's scan and OpenClaw's Hermes resolver window. Box has 581.9 MB RSS just from IB Gateway; peak was 1.0 GB. Monitor on first live session night.

**P6 — Reporting polish**
- `shadow_tracker.py`/reports: show **unrealized** P&L of OPEN shadow positions (currently only realized in the summary).

**P7 — Shared market-context consumer (LOWEST priority, lightest touch)**
- `context_builder.py`: optionally cross-check the VIX/quote block against `~/shared/market_context.json` (via `read_macro_signal.load_macro_signal()`), keeping `tradier_feed`/`econ_calendar` authoritative. Redundancy only — never a decision/order dependency. Wire after Tradier + OpenClaw consumers are live-proven. (P4 OpenClaw Anthropic key is already done per OPENCLAW_CONTEXT.md §12.)

---

## 10. How a new chat should continue

1. **Treat this repo as the source of truth.** The code lives in `options_guardrail/`; the live copy is `/home/ubuntu/guardrail` on the server.
2. **Always keep tests green** (`./deploy.sh` runs `pytest`). Add a test for any new behavior.
3. **Respect the invariants in §1 and the gotchas in §7.**
4. **Deploy via the full-sync method in §8** (zip → rsync `--exclude 'ops/'`), never cherry-pick.
5. **Pick from the backlog in §9**.
6. **Current immediate context (2026-06-16):** Shadow mode fully retired. `guardrail-session` + `guardrail-flatten` enabled and running. IB Gateway connectivity verified (port 7497, `Connected OK: True`). First live autonomous session fires tonight at 21:15 ICT. Watch P5 (server load) on first run.
