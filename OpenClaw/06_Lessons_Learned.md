# Lessons Learned — OpenClaw

## Critical Lessons

### L001 — Nova Cannot Generate Reliable Price Data
Date: Apr 29, 2026
Incident: RIVN quoted at $10.92, actual $15.975 (+46% error)
Impact: Order submitted and had to be cancelled
Rule Added: Human screenshot verification ±2% tolerance
Status: ✅ Protocol in place

### L002 — IV Filter is the Most Important Rule
Date: May 1, 2026
Incident: CLF IV 54-64%, CCL IV 51% — both rejected correctly
Impact: Zero bad trades entered
Lesson: High IV = expensive premium that decays against you
Status: ✅ Rule enforced

### L003 — OI Must Be Verified at Market Open
Date: May 1, 2026
Incident: VALE $16C showed OI=5, bids=$0.00 after hours
Impact: Would have been unfillable order
Rule Added: OI ≥500 both legs, bid >$0.00
Status: ✅ Added to ruleset v3.0

### L004 — Stop Discipline Preserves Capital
Date: May 1, 2026
Incident: F spread dropped to $0.11 vs stop $0.21
Action: Closed immediately, -$32 loss
Lesson: Stopping at $0.11 vs holding to $0 saved ~$11
Status: ✅ Executed correctly

### L005 — Nova Recycled Rejected Candidates
Date: Apr 29, 2026
Incident: RIVN resubmitted with same wrong price
Impact: Wasted time and nearly placed bad order
Rule Added: Nova restricted to scoring only
Status: ✅ Nova role restricted

### L006 — Earnings Events Kill Debit Spreads
Date: Apr 29, 2026
Incident: SOFI down 13% on earnings day, IV spiked
Impact: Would have been large loss
Rule Added: ±14 day earnings ban
Status: ✅ Rule enforced

### L007 — After-Hours Options Data is Useless
Date: May 1, 2026
Incident: VALE bids=$0.00, OI=0 captured after close
Impact: Wasted analysis on stale data
Rule Added: Only capture/analyze during 9:30AM-4PM EDT
Status: ✅ Cowork task timing adjusted

### L008 — Broad Market Selloff Kills Bull Spreads
Date: May 1, 2026
Incident: F trade entered in volatile market, lost -76%
Lesson: Check S&P direction before any entry
Rule: No new trades on red market days
Status: ✅ Added to daily checklist

## Developing Insights

### D001 — $10-$30 Universe Has Structural Liquidity Issues
Observation: Most stocks in price range have thin options OI
Solution: Pre-screen for OI >500 before full analysis
Tickers with confirmed liquidity: CCL ($27C OI 2,010), AAL

### D002 — Iran Conflict is the Master Variable
Observation: Iran war drives crude oil, which drives VIX,
which drives IV across consumer discretionary
Implication: Watch crude oil as leading indicator for IV
Target: Crude <$95 = CCL/NCLH IV likely to compress