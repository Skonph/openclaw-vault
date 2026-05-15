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

## Trade 3 — IAG $22/$24 Jun18 CLOSED

| Field | Detail |
|-------|--------|
| Entry Date | May 11, 2026 |
| Exit Date | May 14, 2026 |
| Entry Price | $0.60 net debit |
| Exit Price | ~$0.20 net (paper distorted) |
| Paper P&L | -$115 (Alpaca) |
| Real est. P&L | ~-$50 (IBKR pricing) |
| Rule Compliance | ✅ All rules followed at entry |
| Stop Triggered | ✅ Yes — correctly |
| Close Method | Leg by leg (L011 protocol) |
| Notes | Paper pricing severely distorted (L013) |
|        | Gold pullback caused IAG -7.77% May 14 |

---

## Summary Statistics (Updated May 14)

| Metric | Value |
|--------|-------|
| Total trades | 3 |
| Winners | 1 (F accidental — see L017) |
| Losers | 2 (AAL, IAG rule-compliant) |
| Cancelled/unfilled | Multiple |
| Real est. P&L | ~-$105 |
| Paper capital | ~$2,890 |
| Rule compliance rate | Improving — IAG was 100% |

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

### Catalyst
- Gold $4,700+ recovering
- Q1 earnings massive beat ($1B revenue)
- Analyst Strong Buy consensus
- Scotiabank raised PT $23→$25

### Key Dates
- Stop check: Daily
- Expiry: June 18, 2026

---

## Trade 4 — HMC $27.5/$30 Jun18 (ACTIVE)
| Field | Detail |
|-------|--------|
| Entry Date | May 15, 2026 |
| Ticker | HMC (Honda Motor ADR) |
| Spread | Buy $27.5C / Sell $30C Jun18 |
| Entry Price | $0.40 net debit |
| Avg Fill | $0.45 long / $0.05 short |
| Max Risk | $40 |
| Max Reward | $210 |
| R:R | 5.25:1 |
| Breakeven | $27.90 (+1.9%) |
| Stop | $0.20 (-50%) real IBKR price |
| DTE at entry | 34 days |
| Conviction | 73/100 ✅ |
| IV Rank | 6 ✅ |
| Status | ACTIVE — HOLD |

### Catalyst
- Q4 revenue +9% YoY
- FY2027 profit rebound guided
- 15 hybrid models by 2030
- Analyst fair value $32 (+21% upside)
- Stock +12% post earnings

### Key Levels
- Current HMC: $26.38 (+2.77%)
- Breakout above April consolidation ✅
- Real stop trigger: HMC below $25.50

### Notes
- Paper pricing at $0.20 on entry day
- Confirmed L013 distortion — use IBKR for real P&L
- Hold confirmed — stock at day high
