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