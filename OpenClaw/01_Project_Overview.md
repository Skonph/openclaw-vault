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

### Cowork Automation
- Task 1: VALE daily snapshot (9:30 PM Bangkok)
- Task 2: VALE trade analyzer (9:35 PM Bangkok)
- Saves to: /Users/SkonP/AI_Prompt/trade/price_snapshots/

## Capital
- Starting: $3,000 paper capital
- Current: ~$2,946
- Max risk per trade: $60
- Max positions: 1 at a time

## Timeline
- Started: ~Apr 27, 2026
- Current date: May 2, 2026
- Graduation target: $3,500 with 60% win rate
  over 20 trades → deploy $500 real capital