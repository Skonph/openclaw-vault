# Nova Session Prompt
**Generated:** 2026-06-23 00:20

---

NOVA — new session starting. Load complete context.

PROJECT: OpenClaw Autonomous Options System v4
ACCOUNT: Alpaca Paper Trading
CAPITAL: ~$2,880 | DATE: 2026-06-23 00:20

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

No trades tonight. Standing by.

WATCHLIST:
1. PR ~$19.91 | KNOWN_HOLD — recheck Jun 17 after dividend Jun 16
2. CCL ~$26.84 | IV 55%+ | Iran deal catalyst needed
3. NCLH ~$17.36 | IV 58%+ | same as CCL
4. AAL ~$13.14 | Conviction ≥75 required
5. VALE ~$16.25 | OI thin | recheck after May 30 earnings

MACRO:
- VIX: $17.2 (2.51%)
- SPY: $745.11 (-0.22%)
- XLE: $54.005 (1.17%)
- XLY: $115.555 (-1.18%)
- XLI: $182.2 (0.97%)
- XLB: $51.79 (0.34%)
- XLC: $106.455 (-2.49%)
- XLF: $53.805 (0.8%)
- XLK: $191.74 (0.28%)
- XLV: $150.585 (1.24%)
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