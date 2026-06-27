# Post-Mortem & Trade Log

## Trade History (all-time)

### Closed Trades

| Date | System | Symbol | Structure | Result | P&L | Lesson |
|------|--------|--------|-----------|--------|-----|--------|
| Jun 2026 | OpenClaw | Various | Debit spreads | LOSS | -$55 | Debit spreads on small caps: theta headwind, thin liquidity |
| Jun 2026 | OpenClaw | TOST | Iron Condor | OPEN (-$44) | — | IC requires tight management; TOST liquidity marginal |
| Jun 2026 | Tradier | Various (3) | Bull Put | **2W/1L** | +$6.67 avg | Bull Put on large ETFs is the proven structure |
| Jun 24 | Tradier | IWM | Bull Put | OPEN | — | IWM backtest net loser — removed from universe Jun 26 |
| Jun (legacy) | Tradier | SPY | Bull Put (3× 695/700P) | OPEN (expired Jun 26) | +$12 credit | Legacy position from early system — expired worthless |
| TBD | Tradier | Multiple | Bull Put | **Target: 75%+ WR** | — | Post-overhaul — first scans start Mon Jun 29 |

### Open Positions (as of Jun 26)

| System | Symbol | Structure | Entry | Expiry | Qty | Status |
|--------|--------|-----------|-------|--------|-----|--------|
| Tradier | IWM | Bull Put (291/289) | Jun 24 | Jul 24 | 1 | Open, -$0.13 unrealized P&L |
| OpenClaw | TOST | Iron Condor | Prior | Prior | 1 | Open, -$44 unrealized |

## Systemic Issues (addressed Jun 26)

### Issue 1: Bear Call Spread — Structural Loser
- **Symptom**: All credit floors produced negative P&L (-$58 to -$221)
- **Root cause**: Call-side credit underperforms in this universe. Theta works against you when the market trends up (which SPY does 55%+ of trading days)
- **Fix**: Disabled. Bearish regime now routes to Bull Put on quality ETFs
- **Source**: 2-year backtest over 500+ trades

### Issue 2: IWM — Symbol-Level Anti-Edge
- **Symptom**: Negative in every single backtest scenario
- **Root cause**: Small-cap index lacks the trending quality of SPY/QQQ. Its options are less liquid, spreads wider, and theta decay patterns less reliable
- **Fix**: Removed from TRADE_CANDIDATES, ETF scan, and correlation guard
- **Source**: 2-year backtest

### Issue 3: Debit Spreads on Small Caps (OpenClaw)
- **Symptom**: 0% win rate (2 losses, -$55 total)
- **Root cause**: Theta works against debit spreads. Small-cap liquidity compounds the problem (wide bid-ask = poor fills)
- **Fix underway**: Added liquid ETFs to candidate pool. IC (credit) prioritized when VIX≥18
- **Source**: Live trading Jun 2026

### Issue 4: Correlation Risk (Legacy)
- **Symptom**: SPY + QQQ long positions invalidated same day → -$3,215 in one session
- **Root cause**: No cross-portfolio correlation guard existed
- **Fix**: Added correlation guard in shared infrastructure. Max 2 bull positions across all systems
- **Source**: Jun 2026 incident

### Issue 5: Thin Credit Floor (15%)
- **Symptom**: Was accepting $0.30 credit on $1-wide spreads (30% return on risk). With wider $2-3 spreads after MAX_RISK increase, 15% floor was leaving money on the table
- **Root cause**: Credit floor set conservatively based on old $1-wide universe
- **Fix**: Raised to 25% (backtest-proven best risk-adjusted)
- **Source**: 2-year backtest credit-floor sweep

## Macro Context (Jun 26, 2026)

- VIX: 18.87 (moderate regime)
- SPY: ~732 (up ~1.5% from last week)
- Regime: moderate
- Calendar: post-FOMC, pre-Jul 4 (next holiday: Jul 3 limited trading)

## Upcoming Events (Jun 29 – Jul 3)
- **Jul 1**: TLT ex-dividend (call assignment risk for short call legs)
- **Jul 1**: SLV ex-dividend (monitor)
- **Jul 3**: US market early close (Jul 4 holiday)
- **Jul 4**: US Independence Day (market closed — no expiration)
- **Jul 16**: NFLX Q2 earnings

## Backtest Summary (Tradier, 2 years of data)

### Best Performers
| Symbol | Bull Put WR | Bull Put P&L | Best Structure |
|--------|------------|-------------|----------------|
| SPY | ~75% | +$100 to +$200/cycle | Bull Put @ mid-delta (0.15-0.25) |
| QQQ | ~78% | +$120 to +$180/cycle | Bull Put, 25% credit floor |
| XLF | 76-86% | +$53 to +$117 | Bull Put, $2-3 wide |
| XLI | 100% | +$49 to +$116 | Bull Put (small sample size) |
| XLY | 100% | +$31 to +$52 | Bull Put (small sample size) |

### Worst Performers
| Symbol | WR | P&L | Note |
|--------|-----|-----|------|
| IWM | 57-70% | -$112 to -$232 | Removed from universe |
| Bear Call (all) | 64-68% | -$58 to -$221 | Disabled entirely |

## Open Questions for Future
1. **Should OpenClaw be merged into Tradier?** Both now share the same ETF universe. The IC structure on OpenClaw is the only differentiating structure.
2. **Should price cap be raised to $200+?** Would unlock AAPL, NVDA, GOOGL liquidity but requires spread-width math adaptation.
3. **Anna's tiered workflow**: Once Anna's macro analysis pipeline is live, tiered candidates can feed the scanner directly.
4. **Backtest on new universe**: The post-overhaul universe (SPY, QQQ, XLF, XLI, XLY, TLT, DIA) needs a fresh 2-year validation to confirm the backtest benefits hold in the specific combination.
