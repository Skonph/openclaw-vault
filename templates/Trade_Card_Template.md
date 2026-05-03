# Trade Card Template — OpenClaw
**Copy this for every new trade being evaluated**

---

## PRE-TRADE SCORING CARD

### Basic Info
| Field | Value |
|-------|-------|
| Date | |
| Ticker | |
| Sector | |
| Underlying Price | $ (HUMAN VERIFIED ✅) |
| Price Source | Google Finance screenshot |
| Screenshot Time | AM/PM EDT |

### Spread Structure
| Field | Value |
|-------|-------|
| Long Strike | $___C |
| Short Strike | $___C |
| Expiry | May/Jun/Jul 29, 2026 |
| DTE | ___ days |
| Order Type | Bull Call Spread — Buy to Open / Sell to Open |

### Options Chain Data (HUMAN VERIFIED)
| Leg | Bid | Ask | Mid | OI | IV |
|-----|-----|-----|-----|----|----|
| Long $___C | $ | $ | $ | | % |
| Short $___C | $ | $ | $ | | % |

### Calculated Values
| Metric | Formula | Value |
|--------|---------|-------|
| Net Debit | Long Ask − Short Bid | $ |
| Max Risk | Net Debit × 100 | $ |
| Max Reward | (Width − Net Debit) × 100 | $ |
| R:R Ratio | Reward ÷ Risk | :1 |
| Breakeven | Long Strike + Net Debit | $ |
| Stop Price | Net Debit × 0.50 | $ |
| % Move Needed | (Breakeven − Price) ÷ Price | % |

---

## RULESET v3.0 CHECKLIST

| # | Rule | Required | Actual | PASS/FAIL |
|---|------|----------|--------|-----------|
| 1 | Conviction Score | ≥70/100 | /100 | |
| 2 | IV Rank | ≤40% | % | |
| 3 | Underlying Price | $10–$30 | $ | |
| 4 | Net Debit | $0.30–$0.60 | $ | |
| 5 | Spread Width | ≤$3 | $ | |
| 6 | DTE | 25–40 days | days | |
| 7 | Earnings Ban | >14 days away | days | |
| 8 | OI Long Leg | ≥500 | | |
| 9 | OI Short Leg | ≥500 | | |
| 10 | Long Bid Active | >$0.00 | $ | |
| 11 | Short Bid Active | >$0.00 | $ | |
| 12 | Market Condition | Green/stable | | |
| 13 | Daily Move | ≤±3% | % | |
| 14 | Nova Price Check | Within ±2% actual | % diff | |

### Overall Verdict