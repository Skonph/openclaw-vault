# Lessons Learned — OpenClaw

### Critical Lessons

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

### L009 — Nova Repeatedly Violates Role Restriction
Date: May 5, 2026
Incident: Nova scanned RIVN, NIO, MARA, GRAB, BARK independently despite clear directive to score human-provided tickers only
Impact: Wasted analysis time, priority tickers missed
Pattern: 3rd time Nova has done this
Action: Add explicit blocklist to Nova prompt and reinforce role restriction every session
Status: ⚠️ Ongoing issue — monitor closely

### L010 — First Clean Entry: IAG Confirms System Works
Date: May 11, 2026
Incident: IAG $22/$24 Jun18 entered with all 11 rules passing. Conviction 77/100 — highest scored trade to date.
Impact: First trade with zero rule exceptions.
Outcome (May 14): Closed at loss ~$55 paper
          Real loss est. ~$50 (IBKR pricing)
          IAG dropped -7.77% on May 14 after gold pullback. Stop triggered correctly.
Lesson: System worked as designed. Entry was clean.
        Loss was due to market conditions not rule breach. This is acceptable. Process > outcome.
        Paper pricing distorted actual loss — see L013.
Status: ✅ CLOSED — loss accepted, rules followed

### L011 — Multi-Leg Close Orders Are Unreliable in Alpaca Paper Trading
Date: May 14, 2026
Incident: IAG $22/$24 multi-leg close orders submitted at $0.20, then $0.10 limit — both expired unfilled. F close attempt May 1 was rejected outright.
Impact: IAG required leg-by-leg close; F stayed open 13 extra days unnoticed
Rule Added: Always close leg-by-leg in Alpaca paper trading. Short leg first (buy_to_close), then long leg (sell_to_close). Never use multi-leg close orders.
Status: ✅ Protocol established

### L012 — Always Verify Close Fills Before Ending Session
Date: May 14, 2026
Incident: F close order on May 1 was rejected by Alpaca. Assumed closed. Position remained open 13 days undiscovered. Inadvertent hold → +$76 win (not repeatable).
Impact: Journal showed wrong P&L for 13 days. Capital tracking wrong. Win came from luck, not skill.
Rule Added: After any close order, confirm fill status before logging trade as closed. Check orders page + positions page. A submitted order ≠ a filled order.
Status: ✅ Protocol established

### L013 — Alpaca Paper Options Pricing Can Be Severely Distorted
Date: May 14, 2026
Incident: IAG $24C (deep OTM, real value ~$0.10) priced at $0.75 in Alpaca paper account. Had to submit limit at $0.80 to force fill. Paper P&L −$115 vs real est. −$50.
Impact: Paper account P&L meaningless for this trade. Capital tracking distorted.
Rule Added: For real P&L tracking, use IBKR options chain pricing, not Alpaca paper fills. Note distorted paper pricing in trade card when it occurs.
Status: ✅ Noted — check IBKR for real P&L reference on each exit

### L014 — Execution Code in Briefing Triggered Premature Order
Date: May 15, 2026
Incident: vault_updater.py auto-embedded Alpaca execution code in 09_Daily_Briefing.md alongside PR alert. Code was run before Events Calendar check was completed. PR $20/$21 Jun18 order submitted — but PR was already rejected (conviction 45/100, events in window: shareholders meeting May 19, dividend Jun 16).
Impact: Unauthorised order placed on a previously rejected ticker. Order unfilled (multi-leg) but protocol violated.
Root Cause: Execution code should never be auto-generated before human Events Calendar check + Nova conviction ≥70 + explicit approval.
Fix: Remove execution code block from vault_updater.py briefing template entirely. Execution code is generated manually by human after full approval chain.
Also: Scanner returned Price $N/A for PR — this should have been a FAIL, not pass.
Status: ✅ vault_updater.py fix applied — see below

### L015 — Scanner Must Treat Price N/A as Auto-Fail
Date: May 15, 2026
Incident: openclaw_scanner.py returned Price $N/A for PR but still flagged PR as qualifying alert.
N/A price should have been immediate rejection.
Impact: Invalid alert generated. Combined with L014 execution code issue, led to unauthorized
PR order being submitted.
Root cause: Missing null/None check in price validation logic in analyze_spread() function.
Fix Applied:
    Added to scanner:
    if not price or price == 0:
		holds.append(f"{symbol}: price unavailable")
	    continue
Rule Added: Price N/A = auto-fail same as price
        outside $10-$30 range.
Status: ✅ Fix applied to openclaw_scanner.py

### L016 — IAG Position Monitoring Gap

L016 — Active Position Needs Daily Stop Check
Date: May 14, 2026
Incident: IAG spread dropped below $0.30 stop without automated detection. Stop was triggered by manual observation not by scanner alert.
Impact: Position held past stop level briefly. Fortunately closed same day discovered.
Root cause: Scanner monitors for NEW trade alerts but does not automatically check
active position stop levels.
Fix Needed: Add monitor_active_position() to daily scanner cron job. Alert if spread value ≤ stop trigger.
Rule Added: Scanner must check active position stop level daily and alert if breached.
Status: ⏳ monitor_active_position() code exists but needs adding to cron scan flow

### L017 — F Position Accidental Win
Date: May 14, 2026 
Incident: F $12.50/$14 May29 position assumed closed May 1. Actually remained open 13 days. Discovered May 14 when Alpaca showed it. Closed at profit +$76 (paper). 
Impact: Win was pure luck — market recovered while we thought position was flat. Could just as easily have been larger loss. 
Lesson: Unmonitored open positions are dangerous regardless of outcome. A lucky win from a process failure is still a process failure. Never mistake luck for skill. 
Rule reinforced: Always verify close fills. Check positions tab after every close order. See L012. Status: ✅ Documented — L012 protocol covers this

### L018 — Multi-Leg Close Orders Can Fill (Addendum to L011)
Date: May 19, 2026
Incident: HMC $27.5/$30 Jun18 closed via mleg order (limit $0.15 net) — both legs filled simultaneously.
Contrast: IAG mleg close orders at $0.20 and $0.10 never filled (L011).
Difference: HMC $27.5C had OI 1,143 on ask side; IAG was deeper OTM with near-zero liquidity.
L013 still applies: $30C paper filled at $0.20 vs IBKR ask $0.05 — always use IBKR for real P&L.
Updated Close Protocol:
  1. Try mleg close first (limit = net spread bid from IBKR)
  2. If unfilled after 3–5 minutes → cancel and go leg-by-leg (L011 fallback)
  3. Always verify fills on Orders + Positions page (L012)
Status: ✅ Protocol updated

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
