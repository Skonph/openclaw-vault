# Trade Journal — OpenClaw

## Summary Statistics
| Metric | Value |
|--------|-------|
| Total Trades | 2 |
| Winners | 0 |
| Losers | 2 |
| Win Rate | 0% |
| Total P&L | -$55 |
| Starting Capital | $3,000 |
| Current Capital | ~$2,946 |
| Max Drawdown | 1.8% |

---

## Trade 1 — AAL $12/$13 May29
| Field | Detail |
|-------|--------|
| Entry Date | Apr 27, 2026 |
| Entry Price | $0.37 |
| Exit Date | Apr 29, 2026 |
| Exit Price | $0.14 |
| P&L | -$23 (-62%) |
| IV at Entry | >45% (rule breach) |
| Conviction | <68 (rule breach) |
| Market | Volatile |
| Rule Breached | IV >45%, conviction <68 |
| Lesson | Never enter with IV above limit. Conviction score must be honest, not optimistic. |

---

## Trade 2 — F $12.50/$14 May29
| Field | Detail |
|-------|--------|
| Entry Date | Apr 29, 2026 |
| Entry Price | $0.42 |
| Exit Date | May 1, 2026 |
| Exit Price | $0.10 |
| P&L | -$32 (-76%) |
| IV at Entry | ~24% ✅ (rule passed) |
| Conviction | 72/100 ✅ (rule passed) |
| Market | Broad selloff |
| Rule Breached | None — stop enforced correctly |
| Lesson | Even rule-compliant trades lose. Stop discipline executed correctly. Market timing matters — broad selloff environment hurts bull spreads. |

---

## Cancelled Orders Log
| Date   | Ticker       | Reason                  | Lesson                   |
| ------ | ------------ | ----------------------- | ------------------------ |
| Apr 29 | BAC $54/$57  | Unfilled at open        | Limit too tight          |
| Apr 29 | RIVN $11/$13 | Nova price wrong by 46% | Always verify price      |
| May 1  | CLF $10/$12  | IV 54%, earnings May 22 | IV filter critical       |
| May 1  | VALE $16/$18 | OI=5, bids dead         | OI filter needed         |
| May 1  | CCL $30/$32  | IV 51%                  | Rules enforced correctly |

## Session Log
| Date | Action | Result |
|------|--------|--------|
| May 5 | Nova scan violation — corrected | ✅ |
| May 5 | CCL verified 6/7 rules | HOLD |
| May 5 | NCLH post-earnings verified | HOLD |
| May 5 | System in silent watch mode | Active |

## Graduation Tracker
| Metric | Current | Target | Progress |
|--------|---------|--------|---------|
| Trades | 2 | 20 | 10% |
| Win rate | 0% | ≥60% | 0% |
| Capital | $2,946 | $3,500 | 26% of gain needed |
| Rule compliance | Improving | 100% | In progress |
| Live accounts | Ready | Locked | Waiting |

Next milestone: First winning trade
---

## Trade 3 — IAG $22/$24 Jun18 (ACTIVE)
| Field | Detail |
|-------|--------|
| Entry Date | May 11, 2026 |
| Ticker | IAG (IAMGOLD Corp) |
| Spread | Buy $22C / Sell $24C Jun18 |
| Entry Price | $0.60 net debit |
| Max Risk | $60 |
| Max Reward | $140 |
| R:R | 2.33:1 |
| Breakeven | $22.60 (+19.5% from $18.92) |
| Stop | $0.30 (-50%) |
| DTE at entry | 38 days |
| Conviction | 77/100 ✅ |
| IV Rank | 34 ✅ |
| Status | ACTIVE — Hold |

### Rules at Entry — All Pass ✅
- Price $18.92 ✅
- IV Rank 34 ✅
- OI 10,741/15,959 ✅
- Spread mid $0.60 ✅
- DTE 38 ✅
- Earnings done May 5 ✅
- Events: None ✅
- Conviction 77/100 ✅

### Catalyst
- Gold $4,700+ recovering
- Q1 earnings massive beat ($1B revenue)
- Analyst Strong Buy consensus
- Scotiabank raised PT $23→$25

### Key Dates
- Stop check: Daily
- Expiry: June 18, 2026
