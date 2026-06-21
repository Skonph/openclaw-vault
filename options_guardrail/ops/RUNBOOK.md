# Operator Runbook — Guardrail Paper Trading

The system is built to run itself. This is what *you* do around it: deploy, watch,
and know exactly how to stop it. Read the "Kill it now" section first.

---

## 🛑 Kill it now (memorize this)

From your MacBook over SSH, in order of escalation:

```bash
# 1. Stop opening new trades + stop the manager (positions stay open in IBKR):
sudo systemctl stop guardrail-session.service guardrail-session.timer

# 2. Stop tonight's strategist from generating a new plan:
sudo systemctl stop guardrail-strategist.timer

# 3. Flatten everything (pull the plug on the broker side):
sudo systemctl stop ibc-gateway        # Gateway down = no API orders at all
#    …then close positions manually in the IBKR mobile/web app if needed.
```

Telegram is also a kill switch in **semi** mode: just stop approving, or reply
Reject. Nothing opens without your tap.

---

## ⏳ Interim phase — no IBKR yet (data + reporting only)

While options permission / Gateway aren't live, you can stand up everything except
execution and get real daily Telegram reports. Nothing trades.

```bash
# on the server, as the 'ubuntu' user
cd /opt/guardrail && chmod +x run.sh deploy.sh && ./deploy.sh   # venv + 89 tests
cp .env.example .env && chmod 600 .env && nano .env
#   set: OPENROUTER_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID=8069530075,
#        STARTING_CAPITAL, TRADIER_ENV=prod, TRADIER_PROD_TOKEN

# 1) confirm all three connections (posts a summary to Telegram):
./run.sh preflight.py --ping

# 2) confirm Tradier pulls live data:
./run.sh tradier_feed.py SPY QQQ IWM

# 3) one full dry pass: build context -> strategist -> shadow report to Telegram:
./run.sh context_builder.py --watchlist SPY,QQQ,IWM
./run.sh strategist_run.py --context data/context.json
./run.sh shadow_report.py --watchlist SPY,QQQ,IWM
```

Schedule just the interim jobs (NOT session/flatten — those need IBKR). Edit the
`User=` in the unit files to `ubuntu` first (or create a `guardrail` user):
```bash
sudo cp ops/guardrail-strategist.* ops/guardrail-shadow.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo timedatectl set-timezone UTC
sudo systemctl enable --now guardrail-strategist.timer guardrail-shadow.timer
# (also add a cron/timer for context_builder.py just before the strategist)
```
You'll get a daily Telegram "Shadow Report": market snapshot + exactly what the
system *would* have traded (guardrail-sized), with zero risk. When IBKR clears,
enable `guardrail-session/flatten/report` timers and disable `guardrail-shadow`.

## Architecture recap (where things run)

Everything autonomous runs on the **Ubuntu server**. The MacBook is dev/monitoring
only. Components:

- `ibc-gateway.service` — IB Gateway (paper) kept alive by IBC. Always on.
- `guardrail-strategist.timer` → `strategist_run.py` — evening: Opus writes the plan.
- `guardrail-session.timer` → `run_ops_session.py` — at the open: opens plans that
  pass the guardrail, then runs the exit monitor until flat. **Fully autonomous**
  (`GUARDRAIL_MODE=auto`) — no approval needed.
- `guardrail-flatten.timer` → `flatten_all.py` — ~10 min before the US close:
  force-closes anything still open so nothing carries overnight unmanaged.
- `guardrail-report.timer` → `daily_report.py` — 08:30 ICT (01:30 UTC), Tue-Sat:
  posts the day's trade log to Telegram after the US close.

Evening prep also runs `context_builder.py` (before the strategist) to assemble
`data/context.json` — account snapshot + watchlist + your flow/IV/calendar feeds.
- Telegram bot — **notifications only** in auto mode (opens, closes, halts, daily
  report). Set `GUARDRAIL_MODE=semi` if you ever want per-trade approval back.

State lives in `/opt/guardrail/data/` (`session_state.json`, `session_positions.json`,
`strategist_output.json`). Logs in `/opt/guardrail/logs/`.

---

## One-time deploy

```bash
# on Ubuntu, as the guardrail user
git clone <your repo> /opt/guardrail        # or rsync from the MacBook
cd /opt/guardrail
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt anthropic    # ib_async + anthropic for live
cp .env.example .env && chmod 600 .env       # fill in keys, then edit
mkdir -p data logs

# IB Gateway + IBC: follow ops/ibc-setup.md, confirm a DU... account appears.

# install units (run as root):
sudo cp ops/guardrail-*.service ops/guardrail-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ibc-gateway
sudo systemctl enable --now guardrail-strategist.timer guardrail-session.timer \
                            guardrail-flatten.timer guardrail-report.timer
```

Add a context-builder step to the evening (cron or a line before the strategist
service). It must run *before* `strategist_run.py`:
```bash
30 8 * * 1-5  /opt/guardrail/run.sh /opt/guardrail/context_builder.py --watchlist SPY,QQQ,IWM
# (then the strategist timer at 08:30 reads data/context.json)
```

Smoke-test before trusting timers:
```bash
. venv/bin/activate
python3 -m pytest -q                          # 56 tests green
python3 strategist_run.py --context data/context.json   # writes strategist_output.json
GUARDRAIL_MODE=semi python3 run_ops_session.py          # should ping Telegram, ask approval
```

---

## Daily rhythm

**Evening (your ICT, ~before sleep)**
1. Make sure `data/context.json` is fresh — overnight flow, IV, econ calendar,
   account snapshot. (Build this from your feeds; see schema in `strategist_run.py`
   `build_user_content`.) If it's stale, the strategist reasons on priors only.
2. The strategist timer fires automatically. Check Telegram / `logs/strategist.log`
   for "wrote strategist_output.json … plans=N". `plans=0` (no edge) is normal and fine.
3. Glance at the plan. In semi mode you'll approve each entry at the open anyway.

**At the open (while you sleep, or watching)**
- Session manager starts, posts "Session start", then one approval request per plan
  (semi) or just opens (auto). Approve/Reject from your phone.
- You'll get a Telegram line on every open, every close (with reason + P&L), and a
  loud 🛑 if the daily/weekly kill-switch arms.

**Anytime**
- `systemctl status guardrail-session.service` — is the manager alive?
- `tail -f /opt/guardrail/logs/session.log` — live detail.
- `cat data/session_state.json` — equity, day/week P&L, open count, deployed.

---

## What to check / health signals

| Check | Good | Act if… |
|---|---|---|
| `ss -ltnp \| grep 7497` | Gateway listening | missing → `systemctl restart ibc-gateway` |
| `managedAccounts()` | returns `DU…` | returns `U…` → STOP, wrong login |
| `session_state.json` day P&L | > −5% | at/below −5% → kill-switch should be armed; verify no new opens |
| Telegram silence at the open | not silent | silent → check session.service + bot token |
| `logs/*.log` errors | none | repeated tracebacks → stop session, investigate |

---

## Known limitations (do NOT skip)

- **Paper only.** The executor refuses non-`DU/DF` accounts by design. Do not
  repoint it at a live account.
- **Entry-time invalidation + monitored exits**, but combo P&L marking via IBKR is
  simplified — verify a few closes by hand early on.
- **Drawdown vs kill-switch gap**: the halt fires on *realized* equity; open marked
  losses can dip past −10% before positions close. Watch the first volatile session.
- **No OPRA = no real option quotes.** If you haven't subscribed, marking/exits run
  on stale/delayed data. Subscribe before trusting live-paper numbers.
- **Timezone/DST**: timers are in UTC; US open shifts 13:30↔14:30 UTC across DST.
  Re-check the session timer at each DST change.
- **It does not target 80%/20%.** It targets bounded loss and honest measurement.
  Treat the paper phase as data collection, not validation of a profit promise.

---

## Promotion path: semi → auto

Only after, across many sessions: opens/closes match what you'd have done, the
kill-switch behaved, and the backtest on *real* data shows acceptable drawdown.
Then set `GUARDRAIL_MODE=auto` in `.env` and restart the session service. Keep the
Telegram notifications — you lose the approval gate but not the visibility.
