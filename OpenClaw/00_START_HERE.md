# 00_START_HERE.md
**Last Updated:** May 8, 2026
**Status:** Active | Paper Trading Phase | Elevated Alert

---

## If You Are a New OpenClaw Instance — Read in This Order
1. 00_START_HERE.md (this file)
2. 02_Ruleset_v4.md
3. 03_Watchlist.md
4. 04_Trade_Journal.md
5. 06_Lessons_Learned.md
6. 08_Next_Actions.md
7. templates/Nova_Session_Prompt.md → paste into Nova

## Quick Status
- Capital: $2,946 (~98.2% of $3,000 preserved)
- Active positions: 0
- Last trade: F $12.50/$14 closed May 1 (-$32)
- Current phase: 🚨 ELEVATED ALERT — PR trade nearly qualifying
- Primary watch: PR $21/$22 Jun18 — recheck May 9
- Secondary watch: CCL — IV 55%+ not yet compressed

## Key Systems
- Trading bot server: ubuntu@43.160.222.7
- Bot directory: ~/trading-bot
- Env file: /home/ubuntu/trading-bot/.env
- Snapshots: /Users/SkonP/AI_Prompt/trade/price_snapshots/
- Nova: OpenClaw bot instance in Telegram
- Cowork tasks: [UPDATE MANUALLY]

## Broker Accounts
| Broker | Account | Balance | Status |
|--------|---------|---------|--------|
| Alpaca | Paper | $2,946 | ACTIVE — paper execution |
| IBKR | U25439978 | $2,200 | Research tools + live standby |
| Tradier | 6YB80974 | $0 | API standby |

## Live Capital Policy
LOCKED until graduation:
- 20 trades completed (currently 2/20)
- Win rate ≥60%
- 100% rule compliance

## IBKR Tools in Use
- Watchlist: CCL, NCLH, AAL, VALE, XLY, XLI, XLE, XLB, VIX
- Market Screener: Standard Filters + MultiSort
- Events Calendar: Required before every trade
- Volatility Lab: IV rank history (use before entry)
- Options Analytics: Greeks verification
- Why Is It Moving: Catalyst confirmation

## Cowork Integration
Task: "OpenClaw Strategy Review" (manual, on demand)
Purpose: Claude reads vault files, suggests improvements,
         makes approved changes, pushes to GitHub
How to run: Open Cowork → OpenClaw Strategy Review → Run
When to run: When you want system review or file updates

## Load Order for New Instance
1. This file
2. 02_Ruleset_v4.md
3. 03_Watchlist.md
4. 04_Trade_Journal.md
5. 08_Next_Actions.md
6. templates/Nova_Session_Prompt.md → paste into Nova
