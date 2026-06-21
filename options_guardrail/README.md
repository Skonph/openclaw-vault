# Options Guardrail Layer (paper-first)

The safety layer that sits between your **Opus strategist** and your **Haiku/IBKR executor**.
Nothing reaches a broker until it passes here. This is the piece that makes
running execution autonomously — while you sleep 11–12h ahead of the US open —
survivable.

**Profile: MODERATE** — 2% max loss per trade, −5% daily halt, −10% weekly halt.
**First backend: IBKR paper** (hard-gated against touching a live account).

## Flow

```
Opus strategist  ──emit TradePlan(JSON)──►  Guardrail.evaluate()  ──approved+sized──►  IBKR paper executor
                                                  │
                                                  ├─ kill-switch (day/week drawdown)
                                                  ├─ structure must be defined-risk
                                                  ├─ max_loss_usd required & positive
                                                  ├─ invalidation level required
                                                  ├─ size DOWN to the 2% per-trade cap
                                                  └─ concurrency + 25% deployed-capital caps
```

The guardrail never *widens* risk. The most it does to a plan is shrink size.
When anything is missing or ambiguous, it rejects.

## Files

| File | Role |
|------|------|
| `risk_policy.py` | The hard limits (MODERATE active). Frozen dataclass; CONSERVATIVE / AGGRESSIVE profiles included. |
| `schema.py` | `TradePlan` contract the strategist must emit. Mandatory risk fields. `from_dict` parses model JSON. |
| `state.py` | Account / P&L state. Day & week anchors, drawdown views, JSON persistence so halts survive restarts. |
| `guardrail.py` | The engine. `Guardrail.evaluate(plan, state) -> GuardrailResult`. |
| `ibkr_paper_executor.py` | Thin IBKR adapter (ib_async). Refuses any account not starting `DU`/`DF`. |
| `run_paper.py` | End-to-end demo: sample plans → decisions → optional paper submit. |
| `positions.py` | Open-position model + JSON store. Scales risk/target to the *approved* qty. |
| `market_data.py` | `MarketDataProvider` protocol + `MockMarketData` (tests) + thin IBKR provider. |
| `exit_monitor.py` | The unattended loop. Closes on invalidation / stop / take-profit; books realized P&L. |
| `demo_exit.py` | Offline demo: opens 2 positions, walks a price path, closes them. |
| `strategist_prompt.md` | The Opus **Strategist** system prompt. Emits the `TradePlan` JSON envelope. |
| `strategist_bridge.py` | Parses strategist JSON (tolerant), runs each plan through the guardrail. |
| `pipeline.py` | **Session orchestrator**: strategist JSON → bridge → open approved → exit-monitor loop. Dry + live-paper. |
| `sample_strategist_output.json` | Example strategist envelope for the pipeline CLI. |
| `config.py` | Env-driven ops config (Anthropic, Telegram, IBKR, mode, paths). |
| `strategist_run.py` | Evening cron: calls Opus → writes `strategist_output.json` (fail-safe to no-trade). |
| `telegram_notify.py` | Telegram notifications + per-trade approve/reject (fail-safe to reject). |
| `daily_report.py` | Daily trade-log report to Telegram at 08:30 ICT (after US close). |
| `context_builder.py` | Assembles strategist inputs (account, watchlist, pluggable flow/IV/calendar) → context.json. |
| `tradier_feed.py` | Tradier (sandbox/prod) feed: quotes, ATM IV from option chains, market clock. Read-only. Self-check CLI. |
| `preflight.py` | Connectivity check: Tradier + OpenRouter + Telegram → posts a summary to Telegram. |
| `shadow_report.py` | Interim (pre-IBKR) daily Telegram digest: market snapshot + what the system *would* trade. |
| `run_ops_session.py` | Live-paper entrypoint: config → Telegram → IBKR → orchestrator, with file lock. |
| `flatten_all.py` | Safety net: force-closes all open positions before the US close; books P&L. |
| `ops/` | systemd units + timers, IBC/Gateway setup, crontab, **RUNBOOK.md**. |
| `.env.example` | Template for server secrets/config. |
| `bs.py` | Black-Scholes pricer used to mark legs over a backtest path. |
| `backtest_data.py` | `BacktestMarketData` — same provider protocol as live, marks combos via BS. |
| `strategy.py` | Pluggable backtest strategies; default momentum vertical-spread strategy. |
| `backtest.py` | The harness: GBM/real feed → guardrail+exit replay → metrics + equity curve. |
| `backtest_run.py` | Runs a backtest, prints metrics, writes `backtest_report.md` + `backtest_trades.csv`. |
| `test_guardrail.py` | 14 tests covering kill-switch, sizing, rejections. |
| `test_exit_monitor.py` | 11 tests covering every exit trigger + kill-switch feedback. |
| `test_backtest.py` | 7 tests: BS parity/intrinsic, combo marking, metrics consistency. |
| `test_strategist_bridge.py` | 9 tests: tolerant parsing, dropped-plan handling, guardrail wiring. |
| `test_pipeline.py` | 4 tests: opens only approved, session exits, live-mode executor close. |
| `test_ops.py` | 11 tests: Telegram notify/approve, approver/notifier hooks, strategist fail-safe. |
| `test_daily_report.py` | 6 tests: report windowing, kill-switch flags, carried-open. |
| `test_flatten_context.py` | 7 tests: flatten books P&L / contains errors; context provider handling. |
| `test_tradier_feed.py` | 7 tests: quote/change parsing, ATM IV selection, provider safety. |

## Run it

```bash
cd options_guardrail
python3 -m pytest -q          # 25 tests, no network needed
python3 run_paper.py          # dry run: guardrail decisions on sample plans (entry)
python3 demo_exit.py          # dry run: exit monitor closing positions over a price path
python3 backtest_run.py       # replay a price path -> metrics + backtest_report.md
python3 pipeline.py sample_strategist_output.json          # dry: ingest -> guardrail -> open
python3 pipeline.py sample_strategist_output.json --live-paper   # live paper (needs IB Gateway)
```

Live paper execution (requires IB Gateway or TWS running in **paper** mode,
API enabled, default port 7497):

```bash
pip install -r requirements.txt
python3 run_paper.py --live-paper
```

## Paper-safety gates (executor)

1. `paper_only=True` by default.
2. Connected IBKR account id must start with `DU`/`DF` (paper). Live accounts
   start with `U` and are refused with `PaperSafetyError`.
3. Default port 7497 (paper), not 7496/4001 (live).

## Deploying it (Ubuntu, paper)

Everything autonomous runs on the always-on Ubuntu box; the MacBook is dev/review
only. Full instructions in **`ops/RUNBOOK.md`** (read its "Kill it now" section first).
In short:

- IB Gateway (paper) stays up under **IBC** (`ops/ibc-setup.md`), localhost:7497.
- `guardrail-strategist.timer` runs `strategist_run.py` in the evening → writes the plan.
- `guardrail-session.timer` runs `run_ops_session.py` at the open → opens approved
  plans, then manages exits until flat.
- `guardrail-report.timer` runs `daily_report.py` at **08:30 ICT (01:30 UTC)**,
  Tue–Sat, pushing the day's trade log to Telegram after the US close.
- Default mode is **`auto`** (fully autonomous): Telegram is notifications-only —
  opens, closes, halts, and the daily report. No approval gate. Set
  `GUARDRAIL_MODE=semi` if you ever want per-trade approval back.

Secrets live in `/opt/guardrail/.env` (see `.env.example`) — never in git. Fail-safe
by design: a strategist error writes a no-trade plan; approval silence rejects; the
executor refuses any non-paper account.

## The strategist (evening run)

`strategist_prompt.md` is the Opus system prompt. You run it once in the evening
(ICT), ~30 min before your window, with overnight flow / IV / econ-calendar /
account context as input. It emits **one JSON envelope** containing zero or more
`TradePlan`s — and emitting zero ("no edge today") is an explicitly correct output.

`strategist_bridge.py` connects that output to the rest of the system. It is
tolerant of model imperfection (strips fences, finds the JSON, drops individual
malformed plans) but strict on risk (a dropped plan never trades):

```python
from strategist_bridge import parse_strategist_output, evaluate_envelope, summarize

env = parse_strategist_output(opus_raw_text)          # tolerant parse
decisions = evaluate_envelope(env, account_state)     # run each plan past guardrail
print(summarize(env, decisions))
for d in decisions:
    if d.result.tradeable:
        executor.execute(d.plan, d.result)            # paper
        # then track the position + run ExitMonitor through the session
```

Full pipeline: **Opus strategist → bridge/guardrail → IBKR paper executor →
exit monitor**, with the **backtest harness** running that same guardrail + exit
logic over history so you can measure expectancy and drawdown before going live.

## The strategist's contract

Opus must emit JSON like this per idea (the bad fields below would be rejected):

```json
{
  "plan_id": "2026-06-01-SPY-1",
  "symbol": "SPY",
  "structure": "debit_call_spread",
  "regime": "trend",
  "legs": [
    {"symbol":"SPY","expiry":"2026-06-19","strike":535,"right":"C","side":"BUY"},
    {"symbol":"SPY","expiry":"2026-06-19","strike":540,"right":"C","side":"SELL"}
  ],
  "thesis": "ES held VWAP overnight; continuation to 540.",
  "net_price": 2.10,
  "max_loss_usd": 4200.0,
  "target_profit_usd": 5800.0,
  "requested_qty": 20,
  "invalidation": {"kind": "underlying_below", "value": 531.0}
}
```

Required: `plan_id, symbol, structure, legs, thesis, max_loss_usd, requested_qty`,
plus an `invalidation` block under MODERATE.

## What this layer deliberately does NOT promise

It does not target an 80% win rate or a 20% return. It targets *survival*: bounded
loss per trade, hard drawdown halts, defined-risk structures only, and a paper
phase before a single live dollar. Edge is the strategist's job; not blowing up is this layer's job.

## Exit monitor (the unattended loop)

`exit_monitor.py` watches every open position and closes it on the FIRST of:

1. **Invalidation** — underlying through the level, IV through the level, or a
   time stop reached. (Same `invalidation` block the strategist supplied.)
2. **Stop** — unrealized loss ≥ `stop_loss_fraction` × defined max loss (default 85%).
3. **Take-profit** — unrealized gain ≥ the plan's `target_profit_usd`.

On every close it books realized P&L into `AccountState.equity`, so the day/week
kill-switch reacts to actual paper losses in real time — a losing streak literally
arms the halt (see `test_killswitch_arms_after_losses`).

It's broker-agnostic: pass any `MarketDataProvider` and an optional `closer`
callable. `MockMarketData` + no closer = fully offline/testable. For live paper,
pass `IBKRMarketData(ib)` and a closer that calls
`executor.close_position(plan, qty)`.

```python
mon = ExitMonitor(store, market, state, "state.json",
                  config=ExitConfig(stop_loss_fraction=0.85, poll_seconds=30),
                  closer=lambda pos, pnl: executor.close_position(plan_by_id[pos.plan_id], pos.qty))
mon.run_forever()   # poll until flat
```

## Backtest harness

`backtest.py` replays a price path through the **same** `Guardrail` + `ExitMonitor`
that run live — so what you measure is what you'd trade. Positions are marked
leg-by-leg with Black-Scholes (moves + time decay + vol). Realized P&L flows into
equity and the kill-switch is active throughout.

```python
from backtest import Backtester
from strategy import default_momentum_strategy

res = Backtester(symbols=["SPY", "QQQ"], strategy=default_momentum_strategy,
                 days=180, starting_equity=100_000).run()
print(res.summary())
```

It reports the numbers that decide go/no-go: **win rate, expectancy per trade,
profit factor, total return, and max drawdown**, plus a breakdown of why trades
closed (invalidation / stop / take-profit).

Plug your Opus strategist in by passing a `strategy` callable that returns
`TradePlan`s (same schema) instead of `default_momentum_strategy`. Swap
`gbm_paths` for real historical closes for a real test.

**Two honest caveats this harness makes visible:**

1. The bundled `default_momentum_strategy` is ordinary on purpose — on synthetic
   GBM it *loses* (~−5%, ~25% win rate in the sample run). That's the point: the
   harness measures the system, it doesn't manufacture an edge. Don't read the
   sample numbers as a forecast.
2. **Max drawdown can exceed the −10% weekly halt.** The kill-switch fires on
   *realized* equity; drawdown is measured on *marked* equity including open
   positions. Open marked losses can dip below the halt line before positions
   close. If that gap matters to you, add a marked-equity (not just realized)
   kill-switch — a small addition to `ExitMonitor.run_once`.

Plus the usual backtest hazards: BS marks ≠ real fills, no slippage/commissions/
assignment modeled, and synthetic data has no real fat tails. Treat results as a
floor on how careful you must be, not a promise.

## Still ahead (when you're ready)

- **Live combo marking**: `IBKRMarketData.position_pnl` currently reads portfolio
  uPnL by symbol; for multi-leg precision, value each leg and net against entry.
- **IV feed**: `implied_vol` is a stub — wire it to your IV source for IV-based
  invalidations.
- **Real data**: replace `gbm_paths` with historical closes (and, ideally, a real
  options chain) so the backtest reflects actual tails, gaps, and vol surface.
- **Marked-equity kill-switch**: optionally halt on marked (not just realized)
  drawdown, closing the gap noted above.
- **Costs**: model commissions, slippage, and assignment in the marks.
```
