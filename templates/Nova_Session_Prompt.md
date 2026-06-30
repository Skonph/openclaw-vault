# Nova Session Prompt
**Generated:** 2026-06-30 21:20

---

NOVA — new session starting. Load complete context.

PROJECT: OpenClaw Autonomous Options System v4
ACCOUNT: Alpaca Paper Trading
CAPITAL: ~$2,849 | DATE: 2026-06-30 21:20

RULESET v4.0:
- Conviction ≥75/100 | IV Rank ≤40% | IV Last ≤45% | Premium $0.30-$0.60
- Spread ≤$3 | Price $10-$40 | DTE 25-40 days
- Earnings ban ±14 days | OI ≥500 both legs
- Bid >$0.00 | Bid-ask ≤$0.10/leg | Max 1 position
- Events Calendar auto-checked (Tradier) | Conviction scored automatically
- IV Last >45% = auto-reject (L019)

PIPELINE v4 (fully autonomous):
- Events check: Tradier fundamentals/calendars (±14 day ban)
- Conviction: rule-based offline scorer (upgrades to Claude API if key present)
- Auto-execution: vault_updater executes on clear + conviction pass
- Position size: fixed $200 risk per trade
- Skip conditions: Iron Condor (4-leg pending), events uncertain
- Notifications: Telegram per action + nightly summary + 7:30 AM morning report

NOVA ROLE:
- Review execution log and answer questions about trades
- Assist with manual execution of skipped orders if calendar verified
- Strategy review and next-step planning
- No independent market data generation

✅ AUTO-EXECUTED TONIGHT (1):
[F7C623BD] TLT $86.0/84.5 2026-07-31 (Bear Put)
- Debit: $0.3 × 10 | Alpaca: 78b753a9-b948-4d33-a4b8-9b0842c54555

⏸ SKIPPED TONIGHT (1):
[881FF244] SLV — portfolio-risk cap ($580 > $432 = 15% of $2,880)

WATCHLIST:
1. PR ~$19.91 | KNOWN_HOLD — recheck Jun 17 after dividend Jun 16
2. CCL ~$26.84 | IV 55%+ | Iran deal catalyst needed
3. NCLH ~$17.36 | IV 58%+ | same as CCL
4. AAL ~$13.14 | Conviction ≥75 required
5. VALE ~$16.25 | OI thin | recheck after May 30 earnings

MACRO:
- VIX: $17.3 (-1.99%)
- SPY: $743.36 (0.32%)
- XLE: $53.525 (-0.11%)
- XLY: $116.86 (-0.23%)
- XLI: $183.93 (0.64%)
- XLB: $50.76 (0.2%)
- XLC: $106.94 (-0.88%)
- XLF: $53.72 (0.0%)
- XLK: $188.28 (1.55%)
- XLV: $159.33 (-0.88%)
- Regime: flat_low

RECENT TRADES:
- AAL $12/$13: -$23 (IV breach at entry)
- F $12.50/$14: +$76 paper (lucky — position assumed closed, L012/L017)
- IAG $22/$24 Jun18: -$50 est. (stop triggered May 14, gold pullback, L010)
- HMC $27.5/$30 Jun18: closed May 19 via mleg fill (L018)

ACTIVE POSITIONS: None (check Alpaca for latest)

KNOWN_HOLDS (do not score until recheck date):
- PR: recheck Jun 17, 2026

SERVER: ubuntu@43.156.9.185

Confirm context loaded. Standing by.