### Nightly Routine — after 9:05 PM Bangkok (Mon–Fri)

**Step 1 — Pull latest vault**

```
ocpull
```

Open Obsidian → `09_Daily_Briefing.md`

---

**Step 2 — Check active position (if any open)**

Currently: no open position as of May 14.

When a position is open — check spread mid manually via IBKR or Yahoo Finance:

- Mid ≤ stop → execute close immediately (short leg first, then long leg)
- Mid > stop → hold, no action needed

⚠️ Server does not yet auto-track active position mid — always check manually.

---

**Step 3 — Review scanner alert (if any)**

The briefing shows all Tradier-automated rule checks (PASS/FAIL). If all pass:

1. Open IBKR → Research → Events Calendar
2. Search the alert ticker
3. Review all events between today and the expiry date
4. Note: earnings, shareholders meeting, dividends, management changes
5. Apply conviction deductions per Ruleset v4.0:
    - Shareholders meeting: −5
    - Special dividend: −5
    - CEO/CFO change: −5
    - Spin-off/merger: −10

Then paste Nova Session Prompt + Events Calendar findings into Nova → Nova scores conviction.

**Human decides:**

- Conviction ≥70 + all rules pass → Approve or Reject
- Any rule borderline → Exception decision (state reason, gets logged)
- Events push conviction below 70 → Reject — log ticker + reason
- Pattern suggests ruleset needs changing → Flag for v4.1 review

---

**Step 4 — Ruleset exception or modification (if needed)**

State the reason → Claude proposes exact change to `02_Ruleset_v4.md` → approve → commit → server uses it next run. No silent exceptions — every override logged in `06_Lessons_Learned.md`.

---

**Step 5 — New candidate sourcing via IBKR (optional)**

Run IBKR MultiSort screener "OpenClaw Bull Call" → note tickers → add to server:

bash

```bash
ssh ubuntu@43.156.9.185
nano ~/trading-bot/candidates.txt
```

Add tickers one per line. Scanner evaluates via Tradier at 9:00 PM. Remove tickers that fail repeatedly.

---

**Quick Reference**

| Command                                        | Action                                    |
| ---------------------------------------------- | ----------------------------------------- |
| `ocpull`                                       | Pull latest vault from GitHub             |
| `ocprompt`                                     | Display Nova session prompt ready to copy |
| `ssh ubuntu@43.156.9.185`                      | Connect to trading server                 |
| `python3 ~/trading-bot/openclaw_scanner.py`    | Run scanner manually                      |
| `tail -50 ~/trading-bot/logs/cron_scanner.log` | Check scanner log                         |
| `tail -50 ~/trading-bot/logs/cron_vault.log`   | Check vault updater log                   |