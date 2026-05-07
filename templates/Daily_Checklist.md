# Daily Trading Checklist
**Run every day at 9:30 PM Bangkok time**

## Pre-Check (5 min)
□ Check Google News: "Iran oil diplomacy"
□ Check crude oil: oilprice.com
  → Record: $___/bbl | Signal: above/below $95
□ Check VIX: finance.yahoo.com
  → Record: ___ | Signal: above/below 15
□ Is S&P 500 green today?
  → YES → proceed | NO → stand down

## Options Check (10 min)
□ Pull CCL May29 options chain
  → $28C IV: ___% | OI: ___
  → $30C IV: ___% | OI: ___
  → Verdict: HOLD / ALERT

□ Pull NCLH May29 options (after May 4 only)
  → $18C IV: ___% | OI: ___
  → $20C IV: ___% | OI: ___
  → Verdict: HOLD / ALERT

□ Pull AAL May29 options (if above not triggering)
  → $12C IV: ___% | OI: ___
  → $13C IV: ___% | OI: ___
  → Verdict: HOLD / ALERT

## If ALERT Triggered
□ Screenshot price chart (Google Finance)
□ Screenshot options chain (Yahoo Finance)
□ Share both with Claude for verification
□ Await Claude analysis before sending to Nova
□ Await Nova confirmation before running bash

## Daily Log Entry (2 min)
Date: ___________
CCL: $_____ | IV ____% | Action: ________
NCLH: $_____ | IV ____% | Action: ________
AAL: $_____ | IV ____% | Action: ________
VIX: _____ | Crude: $_____ | Market: Green/Red
Notes: _________________________________

### 9:00 PM Bangkok (30 min before scan):

STEP 1 — IBKR Market Overview (5 min)
→ Check S&P 500 5-day direction
→ Check VIX level
→ Check crude oil price
→ Note sector winners/losers

STEP 2 — IBKR Market Screener 2.0 (5 min)
→ Run "OpenClaw Bull Call Setup" screen
→ Note any tickers showing IV < 40%
→ Run "OpenClaw Bear Put Setup" if market down
→ Add any new qualifying tickers to list

STEP 3 — IBKR Events Calendar (2 min)
→ Check earnings dates for all candidates
→ Eliminate any within 14 days

STEP 4 — Yahoo Finance Options Chain (5 min)
→ Pull chain for top 1-2 candidates only
→ Screenshot for human verification
→ Confirm OI > 500 on target strikes

STEP 5 — Send to Nova (2 min)
→ Share screened candidates + screenshots
→ Request conviction score
→ Await scoring before any order

Total time: ~20 minutes vs current 45+ minutes