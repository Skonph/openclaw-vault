# Project Overview — OpenClaw Trading System

## Objective
Build a systematic, rule-based options trading system
using paper trading to develop skills and discipline
before deploying real capital.

## Strategy
Bull Call Spreads (Debit Spreads) on US equities
- Buy lower strike call
- Sell higher strike call (same expiry)
- Net debit = maximum risk
- Spread width - net debit = maximum reward

Bull Call Spread:  Buy lower call + Sell higher call
                   Profit when stock RISES

Bear Put Spread:   Buy higher put + Sell lower put  
                   Profit when stock FALLS

## System Components

### Nova (OpenClaw Bot)
- Telegram-based AI trading assistant
- Role: Scoring + execution only
- Cannot generate candidates independently
- Requires human approval for every order

### Alpaca Markets
- Paper trading account
- API access via ubuntu server
- Base URL in .env file
- Orders executed via Python/requests

### Claude (This Instance)
- Strategic advisor and verification layer
- Cross-checks Nova's data
- Prepares execution code
- Maintains this knowledge base

## Capital
- Starting: $3,000 paper capital
- Current: ~$2,946
- Max risk per trade: $60
- Max positions: 1 at a time

## Timeline
- Started: ~Apr 27, 2026
- Current date: May 7, 2026
- Graduation target: $3,500 with 60% win rate
  over 20 trades → deploy $500 real capital
  
## Broker Accounts (Opened May 7, 2026)

| Broker | Account | Balance | Status |
|--------|---------|---------|--------|
| IBKR | U25439978 | $2,200 | Funded — LIVE STANDBY |
| Tradier | 6YB80974 | $0 | Unfunded — STANDBY |
| Alpaca | Paper | $2,946 | ACTIVE — paper trading |

## Live Trading Policy
Status: LOCKED until graduation threshold met

Graduation requires ALL of:
- Paper account reaches $3,500
- Win rate ≥60% over 20 trades
- 100% rule compliance
- Zero rule exceptions taken

Current progress:
- Trades completed: 2 of 20 minimum
- Win rate: 0/2 (0%)
- Capital: $2,946 of $3,500 target
- Rule compliance: Improving

DO NOT deploy real capital until ALL criteria met.
IBKR and Tradier accounts sit idle until graduation.