# Deploy Checklist — 2026-06-24 Autonomous Hardening & 5-Agent Framework

> Companion to `SESSION_2026-06-24_SYSTEM_STATUS.md`. The USER runs every command
> (the agent can't SSH). Server: `ubuntu@43.156.9.185` · paths `~/tradier/`, `~/shared/`.
> Run top-to-bottom. Stop and report if any ❌ check fails.

## 0. Pre-flight (local, before pushing)
- [ ] Local compile clean:
  ```bash
  cd ~/AI_Prompt/Obsidient/SkonVault
  python3 -m py_compile Tradier/daily_scan.py Tradier/telegram_bot.py Tradier/position_monitor.py \
                        shared/conviction_scorer.py shared/conviction_accuracy_tracker.py \
                        shared/portfolio_auditor.py shared/sentiment_agent.py shared/uoa_detector.py && echo OK
  ```
- [ ] Conviction tests pass: `python3 shared/test_conviction_credit.py` → **6 passed, 0 failed**
- [ ] Scaling tests pass: `cd Tradier && python3 test_tradier_scaling.py` → all ✅

## 1. Back up the server copies (rollback safety)
- [ ] ```bash
  ssh ubuntu@43.156.9.185 'cp -a ~/tradier ~/tradier_bak_20260624 && cp -a ~/shared ~/shared_bak_20260624'
  ```

## 2. Push all updated files via rsync
- [ ] ```bash
  cd ~/AI_Prompt/Obsidient/SkonVault
  rsync -avz --exclude 'venv' --exclude '__pycache__' -e "ssh -o StrictHostKeyChecking=no" Tradier/ ubuntu@43.156.9.185:~/tradier/
  rsync -avz --exclude 'venv' --exclude '__pycache__' -e "ssh -o StrictHostKeyChecking=no" shared/ ubuntu@43.156.9.185:~/shared/
  ```

## 3. Verify on the server (no orders sent)
- [ ] Remote compile clean:
  ```bash
  ssh ubuntu@43.156.9.185 'cd ~/tradier && python3 -m py_compile daily_scan.py telegram_bot.py position_monitor.py && \
    cd ~/shared && python3 -m py_compile conviction_scorer.py conviction_accuracy_tracker.py portfolio_auditor.py sentiment_agent.py uoa_detector.py && echo REMOTE_OK'
  ```
- [ ] Config sanity (expect `BULL_PUT_ONLY = True`, `MAX_RISK = 320`, `MAX_POSITIONS = 5`, `STARTING_CAPITAL ... 16000`):
  ```bash
  ssh ubuntu@43.156.9.185 "grep -nE 'BULL_PUT_ONLY|^MAX_RISK |^MAX_POSITIONS|STARTING_CAPITAL' ~/tradier/daily_scan.py"
  ```
- [ ] Clear stale position count, then dry-run against live data:
  ```bash
  ssh ubuntu@43.156.9.185 "echo '[]' > ~/tradier/active_trades.json && cd ~/tradier && python3 daily_scan.py --dry-run"
  ```

## 4. Confirm the dry-run output shows ALL of these
- [ ] Banner: `🧪 DRY-RUN — LIVE Tradier data ... NO order sent`
- [ ] Real strikes near each ETF spot (e.g. XLF puts ~$42–54, TLT ~$86) — **not** SPY-level $730s
- [ ] Sanity guard stays silent (no false strike rejections)
- [ ] Correct per-symbol labels (e.g. "IWM closes below $X", never "SPY" on a non-SPY trade)
- [ ] Conviction line prints `[advisory]` with a real moving score (not a constant ~98)
- [ ] Bear-Call / Iron Condor never construct (if a regime ever routes there: `🚫 ... is CUT (BULL_PUT_ONLY)`)
- [ ] On a proposed trade: `🧪 DRY-RUN — order NOT submitted` and NO new line in `active_trades.json` / `trade_log.jsonl`
- [ ] If vetoed: `🧹 Vetoed/rejected trade cleared from pending_trade.json`
- [ ] Portfolio Audit outputs `🛡️ Portfolio Audit: PASS` or intercepts effectively.
- [ ] Research Layer properly scans Sentiment and UOA.

## 5. Restart the live services (only after §3–4 pass)
- [ ] Restart whatever runs the bot/scan (adjust names to your setup):
  ```bash
  ssh ubuntu@43.156.9.185 'systemctl --user restart tradier-bot 2>/dev/null; \
    sudo systemctl restart tradier-bot 2>/dev/null; true'
  ```
- [ ] Verify cron unchanged: `ssh ubuntu@43.156.9.185 "crontab -l | grep -i tradier"`
- [ ] Confirm next-run is the scan wrapper passing args through (so `--dry-run`/flags work):
  ```bash
  ssh ubuntu@43.156.9.185 "cat ~/tradier/run_scan.sh"   # expect: python3 daily_scan.py \"$@\"
  ```

## 6. First live (non-dry) smoke check
- [ ] At/after market open, let the normal cron fire once (or `/scan` in Telegram), then:
  ```bash
  ssh ubuntu@43.156.9.185 "tail -3 ~/tradier/trade_log.jsonl; echo '---'; cat ~/tradier/active_trades.json"
  ```
- [ ] If a trade executed: confirm `shared/conviction_log.jsonl` got a paired entry:
  ```bash
  ssh ubuntu@43.156.9.185 "tail -2 ~/shared/conviction_log.jsonl"
  ```

## 7. Rollback (if anything misbehaves)
- [ ] ```bash
  ssh ubuntu@43.156.9.185 'rm -rf ~/tradier ~/shared && mv ~/tradier_bak_20260624 ~/tradier && mv ~/shared_bak_20260624 ~/shared'
  ```
- [ ] Restart services (§5), re-verify config (§3).

---
**Done when:** §3 REMOTE_OK + §4 all ticked + §5 services up + cron intact. The system
is then running the validated build: autonomous, bull-put-only, 13-ETF, $16k sizing,
with conviction live in advisory mode accruing its track record, and the portfolio auditor & research layer protecting execution.
