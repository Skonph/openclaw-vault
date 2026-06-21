# Action Plan — Ready-to-Execute Prompts
**Based on:** `Trading_Systems_Review_2026-06-13.md` (§8 update)
**Target executor:** Hermes (terminal access on `ubuntu@43.156.9.185`) via Telegram, or a fresh Claude/Cowork session with SSH access to the server. Each prompt is self-contained — paste as-is.

Order matches the updated priority list. Do them roughly in order; #1-2 (IBKR) are now the most consequential since that system is live-paper with real orders firing.

---

## 1. IBKR P1 — Make live execution trustworthy (combo P&L, real IV, mid pricing)

**Why now:** IBKR guardrail is LIVE-PAPER on `DUQ548647` with `OPTIONS_LEVEL=3`. These were "nice to have" in shadow mode — now they affect whether exit triggers and reported P&L on real orders are correct.

```
You're working on the options_guardrail IBKR autotrader (options strategist +
risk guardrail), now in LIVE-PAPER phase on IBKR paper account DUQ548647 with
OPTIONS_LEVEL=3 (credit spreads / IC / debit spreads all allowed). Live copy is
on ubuntu@43.156.9.185 at /home/ubuntu/guardrail. Read HERMES.md and the project
context first for full conventions.

Three P1 fixes needed, in this order:

1. market_data.py — IBKRMarketData.position_pnl currently reads portfolio uPnL
   by symbol (simplified/aggregate). Replace with per-leg valuation: for each
   open position, get the current mark for each leg (reqMktData/snapshot via
   ib_async), compute (current_value - entry_value) per leg, sum across legs,
   net against entry cost basis. Cross-check the new number against IBKR
   TWS/Gateway's own uPnL for a couple of real open positions and confirm they
   match within a reasonable tolerance.

2. market_data.py — IBKRMarketData.implied_vol is currently a stub. Wire a real
   IV source: reuse tradier_feed.py's existing ATM IV calculation (DATA-ONLY,
   already used elsewhere in this pipeline) as the IV input for
   exit_monitor.py's IV-based invalidation checks. If Tradier IV isn't a good
   match for the specific contract, fall back to IBKR's own option Greeks via
   reqMktData generic tick 106.

3. ibkr_paper_executor.py — limit pricing for multi-leg combos currently
   assumes a full-spread fill. Change to price near the mid of the combined
   bid/ask ("split the spread") instead.

Constraints — do not violate:
- Defined-risk only; never weaken this.
- 2% max loss per trade, -5% daily / -10% weekly kill-switch (MODERATE policy).
- Paper-account gate stays DU/DF only.
- Keep ./deploy.sh green (116+ tests passing); add tests for each of the three
  changes above.
- Deploy via the full-sync method: zip the project, scp, unzip to ~/stage,
  rsync -a --exclude 'ops/' ~/stage/options_guardrail/ ~/guardrail/, then
  ./deploy.sh. Never cherry-pick individual files.

When done, send a Telegram summary: what changed in each of the 3 files, test
results, and (if possible) a before/after comparison of position_pnl on a real
open position.
```

---

## 2. IBKR P2 — Marked-equity kill-switch

**Why now:** Current halt only triggers on *realized* equity. With live positions open, an intraday move could breach -10% before any closed trade reflects it.

```
Continuing on options_guardrail (live-paper, IBKR DUQ548647, OPTIONS_LEVEL=3,
/home/ubuntu/guardrail on ubuntu@43.156.9.185).

Add an optional marked-equity kill-switch:

- Currently guardrail.py / exit_monitor.py / state.py halt new entries based on
  REALIZED equity only, tracked via AccountState's day/week anchors.
- Add a check that also factors in UNREALIZED P&L of all currently-open
  positions: if (realized_equity + sum of unrealized_pnl across open positions)
  breaches -5% vs the day anchor or -10% vs the week anchor, trigger the same
  kill-switch behavior as the existing realized check (halt new entries; do not
  auto-flatten unless that's already the existing kill-switch behavior — match
  whatever the realized kill-switch currently does).
- Make this configurable via .env (e.g. MARKED_EQUITY_KILLSWITCH=true/false),
  default ON.
- Add tests mirroring the existing kill-switch tests, but with open positions
  carrying unrealized losses that push the marked total past -5%/-10% while
  realized equity alone would not trigger.

Respect all existing invariants (§1 of the project context — defined-risk only,
paper-gate, etc.). Keep ./deploy.sh green. Deploy via the full-sync method
(zip -> scp -> rsync --exclude 'ops/' -> ./deploy.sh).

Report via Telegram: summary of the change, new config flag, and test results.
```

---

## 3. Tradier — End-to-end exit verification (highest-priority unresolved item)

**Why now:** This is the last major "don't trust the P&L" item across all three systems.

```
On ubuntu@43.156.9.185, ~/trading-bot/ (Tradier autonomous credit-spread bot,
sandbox account). This is the #1 unresolved verification item: confirm
position_monitor.py correctly detects an open position in active_trades.json
and submits a BTC (buy-to-close) order when an exit trigger fires (profit
target, standard stop loss, threatened-wing stop, or time stop at DTE<=2).

Steps:
1. Check active_trades.json for a currently open position. If none, either wait
   for the next Tue-Thu scan (run_scan.sh, 21:15 ICT) or trigger /scan manually
   via telegram_bot to open one.
2. Once a position exists, run `python3 position_monitor.py --test` and confirm:
   (a) it reads the position correctly from active_trades.json,
   (b) it computes cost-to-close vs entry credit correctly,
   (c) it identifies the correct exit order type (limit for profit target,
       market for stop/time stop) if any trigger condition is currently met.
3. If no trigger is currently met, pull a live spread quote via the Tradier API
   and manually verify position_monitor's threshold math against it (don't rely
   on --test alone if nothing is close to triggering).
4. If feasible, create a throwaway test copy of active_trades.json with a
   position deliberately near/past a trigger threshold, run position_monitor.py
   against sandbox to confirm an actual BTC order is submitted and accepted by
   Tradier sandbox, then discard the test file (do not let it affect the real
   active_trades.json or trade_log.jsonl).
5. Report via Telegram (TradierBot channel): does the exit path work end-to-end
   (yes/no), with evidence (logs/order IDs/quote comparisons). If it fails at
   any step, stop and report exactly where, without attempting a live fix yet.
```

---

## 4. OpenClaw — Enable LLM-based conviction scoring

**Why now:** IBKR is fully LLM-driven; OpenClaw's conviction scorer still falls back to offline/rule-based scoring. This is the cheapest remaining "intelligence" upgrade.

```
On ubuntu@43.156.9.185, /home/ubuntu/openclaw/ (OpenClaw autonomous spread
trader, Alpaca paper). Enable the LLM-based conviction scorer:

1. Check /home/ubuntu/openclaw/.env (chmod 600 — preserve permissions) for
   ANTHROPIC_API_KEY. If missing, send a Telegram message asking Skon to
   provide one rather than guessing or using a placeholder.
2. Once set, confirm conviction_scorer.py's score_conviction() picks up the key
   and routes to _score_anthropic() instead of _score_offline(). Verify by
   running a manual scan/dry-run (e.g. nova_executor.py dry-run on a current
   candidate, or directly calling score_conviction()) and confirming the
   logged path is the Anthropic-scored one.
3. Run _score_anthropic() and _score_offline() side-by-side on 3-5 current
   candidates and compare the scores — flag if they diverge wildly (e.g. one
   says 85, the other says 40) rather than producing a similar 0-100 range.
4. Given CONVICTION_MIN=75 was calibrated against the offline scorer, note
   whether this threshold still looks appropriate against the new Anthropic
   scores, but do NOT change it without confirming with Skon first.

Report via Telegram: confirmation the scorer is now Anthropic-based, the
side-by-side comparison results, and any recommendation on CONVICTION_MIN.
```

---

## 5. Server contention check — 21:10-21:30 ICT window

**Why now:** IBKR's `guardrail-session` (live execution, 21:15 ICT) now overlaps with Tradier's scan (21:15 Tue-Thu) and OpenClaw's Hermes/vault_updater window (21:10-21:20), and runs alongside a headless IB Gateway under Xvfb/Java — this used to be a "shadow mode" overlap, now it's live money (paper) on IBKR.

```
On ubuntu@43.156.9.185, all three autonomous trading systems (OpenClaw, Tradier,
IBKR guardrail) have cron/systemd timers firing within the 21:05-21:30 ICT
window. IBKR's guardrail-session (21:15 ICT) is now LIVE execution (not shadow),
running alongside a headless IB Gateway under Xvfb + Zulu OpenJDK.

Task:
1. Check system resource usage (load average, CPU, memory) during the
   21:05-21:30 ICT window for the last several trading days. Use journalctl,
   `sar`/`atop` if available, or — if no historical data exists — set up a
   lightweight cron logging `uptime` and `free -m` every minute during that
   window for the next few days.
2. Check for errors/timeouts around 21:05-21:30 ICT in:
   - ~/openclaw/logs/scanner.log and ~/openclaw/logs/vault.log
   - ~/trading-bot/logs/cron.log and ~/trading-bot/logs/monitor.log
   - ~/guardrail/logs/* (especially guardrail-session and the IB Gateway
     connection logs)
   Specifically flag: IB Gateway dropped connections, guardrail-session timing
   out or failing, or any cron job exceeding its expected runtime.
3. Confirm the OpenClaw-side 21:30 position_monitor cron entry — verify which
   script path it actually points to, and confirm (or refute) that it's the
   Tradier monitor rather than a duplicate OpenClaw position_monitor.py call
   (vault_updater already runs OpenClaw's position_monitor internally at
   ~21:20).
4. Report findings via Telegram: any contention/errors found, the double-fire
   confirmation, and whether the IBKR live session has been affected by load
   from the other two systems on any night so far.
```

---

## Notes

- Run #3 (Tradier exit test) and #5 (contention check) in parallel with #1-2 (IBKR) if Hermes can multitask — they're independent.
- #4 (OpenClaw scorer) is the lowest-risk/lowest-effort item; fine to do whenever convenient.
- After all five land, the system is in a good state to start the graduation scorecard (win rate / profit factor / R-multiple across all three, min ~30 trades each) from the original review's §5.3.
