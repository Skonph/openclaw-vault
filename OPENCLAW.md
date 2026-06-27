# OpenClaw v3 (`~/openclaw/`)

## Purpose
Secondary options system on Alpaca (paper, ~$2,898 equity). Combines structured candidate screening (earnings/ex-div filtering) with Iron Condor (credit) and debit spread (Bull Call / Bear Put) construction. 25–50 DTE window.

## File Map
| File | Purpose |
|------|---------|
| `openclaw_scanner.py` | Main scanner — regime → strategy → structure |
| `candidate_screener.py` | Screens 79+ tickers → writes `candidates.txt` |
| `candidate_master.txt` | Master ticker list (7 ETFs currently) |
| `candidates.txt` | Screener output — consumed by scanner |
| `candidates_verdict.json` | Structured event verdicts (SSOT) |
| `conviction_scorer.py` | Ranks candidates (OpenClaw version) |
| `approval_manager.py` | Trade execution gating |
| `morning_report.py` | Pre-market brief |
| `vault_updater.py` | Persists scan results to vault |
| `position_monitor.py` | OpenClaw exit manager |
| `events_checker.py` | Earnings/ex-div safety checks |
| `known_dividends.py` | Hardcoded dividend calendar (TLT, SLV, etc.) |
| `.env` + `.env.openclaw` | Credential files |

## Candidate Screening Pipeline

```
candidate_master.txt → candidate_screener.py → candidates.txt + candidates_verdict.json
                                                          ↓
                                              openclaw_scanner.py
```

### Screener Flow
1. Reads candidate_master.txt (79+ tickers)
2. Filters: price $10–$100, weekly options available
3. Fetches option chains via Tradier prod API (10 workers parallel)
4. Filters: OI ≥ 300, bid-ask ≤ $0.15, premium $0.30–$0.60, DTE 25–50
5. Events check per ticker:
   - **BLOCKED**: earnings inside ±14d entry/expiry OR held-through
   - **MONITOR**: ex-div inside ±14d (call_blocked flag set for call-short structures)
   - **UNCERTAIN**: API failure (never auto-clears)
   - **CLEAR**: no events
6. Writes `candidates.txt` + `candidates_verdict.json`

### Events Rules (Three Conditions — ALL Must Check)
1. **entry_in**: `today ≤ event_date ≤ today + 14d` — earnings too close to entry
2. **expiry_in**: `expiry − 14d ≤ event_date ≤ expiry + 14d` — earnings near expiry
3. **held_through**: `today ≤ event_date ≤ expiry` — position holds through earnings

→ Violation of any = BLOCKED

## Strategy Routing (openclaw_scanner.py)

| Regime Condition | Strategy | Type |
|-----------------|----------|------|
| SPY flat (±0.5%) + VIX ≥ 18 | Iron Condor | Credit |
| SPY flat (±0.5%) + VIX < 18 | Bull Call Spread | Debit |
| SPY > +0.5% | Bull Call Spread | Debit |
| SPY < −0.5% | Bear Put Spread | Debit |

## Event-Level Logic (from candidates_verdict.json)
The Scanner enforces:
- **blocked** → skip ticker
- **monitor** with ex-div → `call_blocked=True` (prevents IC/Bull Call — short call leg faces assignment risk). Bear Put always allowed.
- **uncertain** → skip (unless manual override)
- **clear** → proceed normally

**Staleness re-check**: Scanner calls Finnhub at build time for any earnings announced since screener run. API failure → `uncertain` (fail-closed — never silently confirms `clear`).

## Known Issues
- **Monthly-cycle spike**: Most large-cap names use monthly options. Names like ZION, ZWS, URBN only enter the 25–50 DTE window for ~6 days per month.
- **Price cap ($100) blocks mega-caps**: AAPL, MSFT, NVDA, GOOGL, AMZN unavailable — could raise to $200+ for massive OI liquidity.
- **Tradier fundamentals endpoint dead** (404 since Jun 2026). Events fall back to hardcoded KNOWN_EVENTS/KNOWN_CLEAR overrides.
- **ETF auto-clear is blind to ex-div risk**: TLT pays monthly (~Jul 1, Jul 7 estimated). Fixed v1.4: ETFs no longer auto-pass; dividend calendar check surfaces monitor flags.
- **Strike increments vary**: Mid-caps use $2.50 increments (ZION, WBS). Large caps use $0.50/$1.00. Always verify from chain output.
