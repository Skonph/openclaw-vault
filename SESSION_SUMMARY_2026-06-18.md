# Session Summary — Paper-Trading Systems Review (2026-06-18)

> Continuation brief. Load alongside the three context files to resume:
> `OpenClaw/OPENCLAW_CONTEXT.md`, `Tradier/tradier_context.md`, `options_guardrail/ibkr_context.md`.

## Setup

Three parallel paper systems on `ubuntu@43.156.9.185` (ICT/UTC+7):
- **OpenClaw** (Alpaca) — `~/openclaw`
- **Tradier** (sandbox) — `~/trading-bot`
- **IBKR guardrail** — `~/guardrail`
- **Shared** — `~/shared` · **Vault (Mac):** `~/AI_Prompt/Obsidient/SkonVault`

**Constraint:** the Cowork sandbox CANNOT reach the server (network-isolated, proxy-locked). All deploys/diagnostics are run by the user via SSH from their Mac; the agent stages/edits files in the vault and interprets pasted output.

**Crontab (ICT):** market_context_writer 20:55 · OpenClaw scanner 21:05 · vault_updater 21:20 · Tradier scan 21:15 (Tue–Thu) · position_monitor 21:30/00:00/02:30 · guardrail-session 21:15 · guardrail-flatten 02:50 · reports 07:30–08:00.

## What we resolved

### Tradier "zero real fills" — diagnosed & fixed
Root cause was NOT a stuck mode flag: `order_status:"simulated"` comes only from the `--test` argv flag; the live cron runs live. Real issue: no-trade days logged nothing, so a healthy decline looked identical to a crash. **Fix (DEPLOYED):** `log_scan_heartbeat()` in `daily_scan.py` records every live decline (reason: calendar_skip / position_limit / cash / pass / no_qualifying_spread); `daily_summary.py` ignores `type=="scan"` records. Tests `test_scan_heartbeat.py` 10/10.

### Live diagnostic findings
Cron genuinely live (real data, sandbox HTTP 200, clean crontab, no stray `--test`). Found a real open position: **3× SPY 695/700 bull put spread**, exp 2026-06-26, ~−$213 mark, manual/legacy origin (no `executed_*.json`, no trade_log row). The proxy-403 seen in old logs was an environmental artifact, not the server.

### active_trades.json malformed → position UNMANAGED (fixed)
Server file was hand-edited to unquoted-key non-JSON → `position_monitor.json.load()` crashed → live position unmanaged. **Fix (DEPLOYED):** rewrote as valid JSON (values preserved); hardened `load_active()` to fail-loud (Telegram alert + `SystemExit`) rather than silently report "no active trades." Verified monitor now HOLDS the position (`$0.75 cost-to-close < $2.50 stop`, no false trigger). Tests `test_load_active_safety.py` 4/4.

### Oversized-position audit
`daily_scan` cannot produce it: `_score_spread` rejects `max_loss > MAX_RISK ($100)`; a $5-wide spread (~$425–496 max loss) is structurally impossible; qty=3 needs per-contract risk ≤ $50. No other automated Tradier executor exists (`executor.py`/`nova_executor.py` are OpenClaw/Alpaca tools). Conclusion: **entered manually in the Tradier sandbox.** Max loss ~$1,488 ≈ 15× the $100 policy; ~99% prob expires OTM. Hold-vs-close is the user's call; monitor manages it either way.

### Shared macro signal — reconciled (use what's already there)
Discovered an existing publisher `market_context_writer.py` (runs 20:55 ICT → `~/shared/market_context.json`) that NOTHING consumed. The agent's initially-built `macro_publisher.py` was redundant → RETIRED. Repointed the freshness-guarded reader + consumers at the real schema (`generated_at` Z-timestamp, `quotes.<SYM>.last/change_pct`, `calendar_skip`, `regime`, `signals`).
- **Tradier consumer (DEPLOYED):** `apply_shared_macro()` in `morning_scan` sources VIX/SPY/QQQ/IWM from context when fresh, falls back otherwise. Tests 9/9; reader `PARSES OK` on server. Benefit = consistency + resilience (Tradier still needs one call for sectors).
- **OpenClaw consumer (STAGED, NOT deployed):** `_macro_quote_from_context()` + wired the macro loop; sources VIX/SPY from context (skips 2 of 10 per-ticker calls), sectors still fetch live for EMA20. Tests 7/7, compiles, imports clean.

### Docs updated
`tradier_context.md` (heartbeat + live-position findings), `shared/INTEGRATION.md` (reconciliation + per-consumer wiring guide).

## Deployment status (end of 2026-06-18)

- **Deployed:** Tradier heartbeat (`daily_scan.py`, `daily_summary.py`), `active_trades.json` repair, `position_monitor.py` hardening, Tradier macro consumer; **OpenClaw macro consumer** (`openclaw_scanner.py` + `read_macro_signal.py` + `test_macro_consumer.py`, 7/7 on server).
- **Verified live:** Tradier `📡` chain (manual `morning_scan`, VIX 18.44 flowed through); live position managed + holding; server `shared/` cleanup done.
- **Fixed (was never running):** guardrail execution — `User=guardrail` → `User=ubuntu` drop-ins on `guardrail-session`/`guardrail-flatten` (CHDIR crash-loop, 5,450+ failures since 06-16). Crash-loop stopped; pending first live run.
- **Retired:** `macro_publisher.py`, `run_macro.sh`, `test_macro_signal.py`, `macro_signal.json` (server clean; confirm vault `shared/`).
- **Awaiting first live cycle (06-18 night):** Tradier+OpenClaw live context consumption, guardrail first real execution. **→ See `SESSION_2026-06-19_RESUME.md` for the verification block.**

## Open items / next steps

1. **Run the morning-after verification block** after tonight's full cycle — confirms Tradier `📡` (writer→reader→consumer chain live), heartbeat/fills, position management (`Holding`), cleanup, guardrail-session, and server contention (Fable5 P5).
2. **If Tradier `📡` confirms → deploy the staged OpenClaw consumer:**
   ```bash
    scp ~/AI_Prompt/Obsidient/SkonVault/OpenClaw/openclaw_scanner.py \
        ~/AI_Prompt/Obsidient/SkonVault/OpenClaw/read_macro_signal.py \
        ~/AI_Prompt/Obsidient/SkonVault/OpenClaw/test_macro_consumer.py \
        ubuntu@43.156.9.185:~/openclaw/
    ssh ubuntu@43.156.9.185 'cd ~/openclaw && python3 test_macro_consumer.py | tail -3'
   ```
3. **Wire guardrail as 3rd consumer** — lightest touch, cross-check only, never route orders through it (respects its invariants).
4. **Decide on the 695/700 position** (hold vs. close).
5. **Loose end:** confirm cleanup command ran on both server and vault `shared/`.
6. **Optional improvement:** oversized-position risk-audit guard — alert when any live account position exceeds `MAX_RISK` (would have auto-caught the manual position).
7. Fable5 #2 (OpenClaw Anthropic key) already done per context — quick server verify only.

## Morning-after verification block (paste output back)

```bash
ssh ubuntu@43.156.9.185 'echo "════ 1. SHARED CONTEXT FRESHNESS ════"; \
  stat -c "market_context.json written: %y" ~/shared/market_context.json; \
  echo; echo "════ 2. TRADIER consumed it? (📡) + outcome ════"; \
  grep -E "Shared market_context|📡|Strategy:|CALENDAR SKIP|AUTO-EXECUTED|heartbeat" "$(ls -t ~/trading-bot/logs/20*.log | head -1)" | tail -8; \
  echo; echo "════ 3. HEARTBEAT / fills (trade_log last 3) ════"; \
  tail -3 ~/trading-bot/trade_log.jsonl; \
  echo; echo "════ 4. LIVE POSITION still managed? ════"; \
  ./trading-bot/venv/bin/python3 -c "import json;d=json.load(open(\"/home/ubuntu/trading-bot/active_trades.json\"));print(\"active_trades VALID,\",len(d),\"trade(s)\")"; \
  grep -E "Holding|STOP LOSS|PROFIT|EXIT|UNMANAGED" ~/trading-bot/logs/monitor.log | tail -5; \
  echo; echo "════ 5. CLEANUP (shared/) ════"; \
  ls ~/shared | grep -E "macro_publisher|run_macro|macro_signal.json|market_context_writer|read_macro_signal"; \
  echo; echo "════ 6. GUARDRAIL session ════"; \
  journalctl -u guardrail-session --since "yesterday 21:00" --no-pager | tail -15; \
  echo; echo "════ 7. SERVER CONTENTION ════"; \
  free -m; uptime'
```
```
