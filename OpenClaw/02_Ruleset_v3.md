# OpenClaw Trading Ruleset v3.0
**Effective:** May 1, 2026

## Entry Criteria
| Rule | Requirement |
|------|-------------|
| Conviction Score | ≥70/100 |
| IV Rank | ≤40% |
| Premium | $0.30–$0.60 net debit |
| Spread Width | ≤$3 |
| Underlying Price | $10–$30 |
| DTE | 25–40 days |
| Earnings Ban | ±14 days |
| Market Condition | Green/stable only |
| Max Positions | 1 at a time |
| Max Risk | $60/trade |

## Liquidity Requirements (Added May 1)
| Rule | Requirement |
|------|-------------|
| OI both legs | ≥500 contracts |
| Bid/Ask | Both legs must show live bid > $0.00 |
| Spread width | Bid-ask spread ≤$0.10 per leg |

## Sector Coverage
### Primary
- Consumer Discretionary
- Industrials
- Energy
- Materials
- Communication Services

### Secondary
- Financials (price ≤$30 only)
- Healthcare (no biotech, no earnings ±14d)
- Technology (small/mid cap, $10–$30 only)

### Excluded
- Utilities
- REITs
- Consumer Staples

## Candidate Sourcing
- Human provides tickers only
- Nova scores only — no independent scanning
- Human screenshot verifies price ±2%
- Human verifies IV on live options chain
- OI must be verified before approval

## Order Execution
- Human explicit approval required
- Fill within 10% of limit
- Day orders only
- Auto-cancel if unfilled at next open

## Risk Management
- Stop loss: 50% of entry premium (hard rule)
- No exceptions, no hope-holding
- Close immediately when triggered

## Auto-Reject Conditions
Any of these = immediate rejection:
- IV Rank >40%
- Earnings within ±14 days
- Price outside $10–$30
- Conviction <70/100
- Nova price vs actual >2% variance
- Broad market selloff day
- Deep ITM strikes
- OI <500 on either leg
- Bid = $0.00 on either leg
- Same ticker as recent loss (extra scrutiny)

## Post-Trade Protocol
- Log every trade in 04_Trade_Journal.md
- Log every cancellation with reason
- Log every lesson in 06_Lessons_Learned.md
- Weekly: review conviction score accuracy

## Version History
| Version | Date   | Key Change                                     |
| ------- | ------ | ---------------------------------------------- |
| v1.0    | Apr 27 | Initial — conviction ≥68, IV ≤45%              |
| v2.0    | Apr 29 | Fill tolerance, auto-cancel, screenshot verify |
| v3.0    | May 1  | Conviction ≥70, IV ≤40%, OI ≥500 added         |