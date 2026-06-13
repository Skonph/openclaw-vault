# OpenClaw Trading Ruleset v4.0
**Effective:** May 8, 2026
**Previous:** v3.0 (May 1, 2026)

---

## Entry Criteria

| Rule | Requirement |
|------|-------------|
| Conviction Score | ≥75/100 |
| IV Rank (52W Percentile) | ≤40% |
| IV Last (Absolute) | ≤45% |
| Premium | $0.30–$0.60 net debit |
| Spread Width | ≤$3 |
| Underlying Price | $10–$40 |
| DTE | 25–40 days |
| Earnings Ban | ±14 days |
| Market Condition | Green/stable only |
| Max Positions | 1 at a time |
| Max Risk | $60/trade |

---

## Liquidity Requirements

| Rule | Requirement |
|------|-------------|
| OI both legs | ≥500 contracts |
| Bid/Ask | Both legs live bid > $0.00 |
| Bid-ask spread | ≤$0.10 per leg |
| Options existence | Must verify chain exists before analysis |

---

## Pre-Trade Verification Checklist
*(Complete in this exact order)*

| Step | Action | Tool |
|------|--------|------|
| 1 | Verify options chain exists | Yahoo Finance |
| 2 | Confirm price ±2% of estimate | Google Finance screenshot |
| 3 | Check daily move ≤±3% | Google Finance |
| 4 | Verify OI ≥500 both legs | Yahoo Finance chain |
| 5 | Confirm live bids >$0.00 | Yahoo Finance chain |
| 6 | Check IV ≤40% | Yahoo Finance chain |
| 7 | Check IBKR Events Calendar | IBKR Research |
| 8 | Verify earnings >14 days away | IBKR Events Calendar |
| 9 | Score conviction ≥75 | Nova scoring |
| 10 | Human final approval | Chat confirmation |

---

## Sector Coverage

### Primary
- Consumer Discretionary
- Industrials
- Energy
- Materials
- Communication Services

### Secondary
- Financials (price ≤$40 only)
- Healthcare (no biotech, no earnings ±14d)
- Technology (small/mid cap, $10–$40 only)

### Excluded Permanently
- Utilities
- REITs
- Consumer Staples

---

## Candidate Sourcing

- Human provides tickers via IBKR screener or watchlist
- Nova scores only — no independent scanning
- Human screenshot verifies price ±2%
- Human verifies IV on live options chain
- OI must be verified at market open (not after hours)
- IBKR Events Calendar checked for all candidates

---

## Order Execution

- Human explicit approval required
- Fill within 10% of limit
- Day orders only
- Auto-cancel if unfilled at next open
- SSH to ubuntu@43.160.222.7 for execution
- Verify order status in Alpaca within 10 minutes

---

## Risk Management

- Stop loss: 50% of entry premium (hard rule)
- No exceptions, no hope-holding
- Close immediately when triggered
- Never average down on losing position

---

## Auto-Reject Conditions
*Any single condition = immediate rejection*

| Condition | Reason |
|-----------|--------|
| IV Rank >40% | Options too expensive (relative) |
| IV Last >45% | Options too expensive (absolute) — even if IV Rank passes |
| Earnings within ±14 days | IV spike risk |
| Price outside $10–$40 | Filter breach |
| Conviction <75/100 | Insufficient edge |
| Nova price vs actual >2% | Data unreliable |
| Broad market selloff day | Directional headwind |
| Deep ITM strikes | Wrong structure |
| OI <500 on either leg | Liquidity risk |
| Bid = $0.00 on either leg | Market closed or illiquid |
| Options chain doesn't exist | Cannot trade |
| 2+ simultaneous bearish signals | Momentum against trade |
| Same ticker as recent loss | Extra scrutiny required |
| Data captured after market hours | Stale bids/IV |

---

## Conviction Score Adjustments

### Base Score Components
| Factor | Max Points |
|--------|-----------|
| Business model stability | +15 |
| Recent earnings result | ±10 |
| Sector tailwind | +10 |
| IV environment | +10 |
| Price trend alignment | +10 |
| Volume/liquidity | +8 |
| Macro support | +8 |
| Catalyst clarity | +5 |
| **Max base score** | **76** |

### Mandatory Deductions
| Condition | Deduction |
|-----------|-----------|
| EPS miss at last earnings | -5 |
| Annual/shareholders meeting in window | -5 |
| Special dividend in window | -5 |
| Spin-off or merger news | -10 |
| 2+ bearish technical signals | -8 |
| CEO/CFO change | -5 |
| Sector headwinds | -5 to -10 |
| Prior loss on same ticker | -8 |
| Low options volume (<1000/day) | -8 |

### Score Interpretation
| Score | Action |
|-------|--------|
| ≥80 | Strong — enter if all rules pass |
| 75–79 | Minimum — enter only if all other rules strong |
| 70–74 | Reject — below new threshold (raised May 22, 2026) |
| <70 | Hard reject |

---

## IBKR Events Calendar Rule
*(Added May 7, 2026)*

Before approving any trade, check IBKR Events Calendar
for the ticker covering full trade window (entry → expiry):

**Disqualifying events:**
- Earnings announcement (already covered by earnings ban)

**Conviction-reducing events:**
- Annual/special shareholders meeting: -5
- Special dividend announcement: -5
- CEO/CFO/major management change: -5
- Spin-off, merger, acquisition news: -10
- 2+ simultaneous bearish technical signals: -8

**Procedure:**
1. IBKR → Research → Events Calendar
2. Search ticker
3. Review all events between today and expiry date
4. Apply deductions to conviction score
5. If revised score <75 → reject

---

## Market Direction Filter
*(Added May 7, 2026)*

Before entering any trade check sector ETF:

| Trade Type | ETF to Check | Condition Required |
|------------|-------------|-------------------|
| Bull Call Spread | Sector ETF | Price above EMA(20) |
| Bear Put Spread | Sector ETF | Price below EMA(20) |

| Ticker Sector | ETF |
|--------------|-----|
| Consumer Discretionary | XLY |
| Industrials | XLI |
| Energy | XLE |
| Materials | XLB |
| Communication Services | XLC |

---

## Strategy Types Approved
*(Added May 7, 2026)*

### Type 1 — Bull Call Spread (Original)
- Buy lower strike call + Sell higher strike call
- Enter on uptrending stocks
- Profit when stock rises above breakeven

### Type 2 — Bear Put Spread (New)
- Buy higher strike put + Sell lower strike put
- Enter on downtrending stocks
- Same IV, OI, premium rules apply
- Sector ETF must be below EMA(20)
- All other rules unchanged

---

## Post-Trade Protocol

- Log every trade in 04_Trade_Journal.md
- Log every cancellation with reason
- Log every lesson in 06_Lessons_Learned.md
- Create individual Trade_Card in /trades folder
- Check IBKR Events Calendar findings in trade card
- Weekly: review conviction score accuracy

---

## Candidate Discovery Workflow
*(Updated May 7, 2026)*