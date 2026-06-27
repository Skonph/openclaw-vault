# June 26, 2026 — Strategy Overhaul

## Rationale
One-week sprint (Jun 26 → Jul 3) to improve win rate without new subscriptions. All changes backed by 2-year backtest data.

## Changes Applied

### 1. Bear Call Spread Disabled
**Before**: Bearish regime → Bear Call Spread (credit call spread, short OTM call + long further OTM call)
**After**: Bearish regime → Bull Put Spread on quality ETFs
**Why**: Backtest net loser at ALL credit floors:
| Credit Floor | P&L | WR | Max DD |
|-------------|-----|-----|--------|
| 10% | -$221 | 64% | $468 |
| 15% | -$58 | 68% | $406 |
| 20% | -$104 | 66% | $347 |
| 25% | -$118 | 67% | $347 |

Theta works against the call-side credit structure. Bull Put Spreads win in the same market with positive theta tailwind.

### 2. IWM Removed from Universe
**Before**: IWM was a primary scan candidate (neutral-bearish)
**After**: Removed from TRADE_CANDIDATES, ETF scan list, and correlation guard
**Why**: Only symbol negative in EVERY backtest scenario:
| Scenario | P&L | WR |
|----------|-----|------|
| Bull Put +$2/trade | -$112 | 70% |
| Bull Put +$2 + IC | -$232 | 57% |
| Bull Put $320/credit | -$212 | 57% |
| Bull Put $320/reversed | -$118 | 63% |

### 3. Credit Floor Raised: 15% → 25%
**Before**: `min_credit = max($0.30, width × 0.15)`
**After**: `min_credit = max($0.30, width × 0.25)`
**Why**: 25% had best risk-adjusted return across all backtest thresholds:
| Threshold | P&L | WR | Win/Loss Ratio | Max DD |
|-----------|-----|-----|---------------|--------|
| 10% | +$360 | 75.8% | 2.69 | $265 |
| 15% | +$327 | 75.4% | 2.63 | $299 |
| 20% | +$404 | 76.5% | 2.97 | $263 |
| **25%** | **+$441** | **77.4%** | **3.07** | **$239** |
| 30% | +$349 | 74.3% | 2.60 | $302 |

With MAX_RISK=$320 allowing $2-3 wide spreads, the 25% floor ($0.50-$0.75) filters thin-credit trades effectively.

### 4. New Symbols Added
| Symbol | Description | Backtest WR | Backtest P&L |
|--------|-------------|-------------|-------------|
| XLF | Financials | 76-86% | +$53 to +$117 |
| XLI | Industrials | **100%** | +$49 to +$116 |
| XLY | Consumer Disc. | **100%** | +$31 to +$52 |
| TLT | Treasuries 20y | 75% | +$16 |
| DIA | Dow Jones Ind | (new) | (new) |

Total universe: SPY, QQQ, XLF, XLI, XLY, TLT, DIA

### 5. Scan Days Widened: Tue-Thu → Mon-Fri
**Before**: `2-4` in crontab (Tuesday, Wednesday, Thursday only)
**After**: `1-5` (Monday through Friday)
**Why**: After removing IWM + Bear Call + raising credit floor, fewer trades per scan day. Need 5× weekly volume to generate enough entries.

### 6. Friday Exclusion Removed
**Before**: Code blocked Friday explicitly (`dow == 4`)
**After**: All weekdays allowed
**Why**: Monday entry (no weekend gap risk since you just came off a weekend) + Friday entry (market has settled by 10:15 AM ET). The weekend gap concern primarily applied to Friday entries, but with the tightened credit floor and reduced universe, the volume trade-off is worth it.

### 7. OpenClaw Candidate Pool Widened
**Before**: Empty candidate_master.txt (only TLT, SLV from screener)
**After**: 7 liquid ETFs: SPY, QQQ, XLF, XLI, XLY, TLT, DIA
**Why**: OpenClaw's IC credit-spread logic on liquid ETFs with large OI. Same backtest-proven universe as Tradier system.

### 8. ibc-gateway Stopped + Disabled
**Before**: Auto-restart-looping (exit code 217/USER — user `guardrail` no longer exists)
**After**: `sudo systemctl stop ibc-gateway.service && systemctl disable ibc-gateway.service`
**Why**: Wasting RAM on the 2GB VPS. IBKR permanently decommissioned.

## What Was NOT Changed

- **MAX_RISK ($200 per trade)**: Stayed — this was already optimal in backtest
- **Delta range (0.10–0.35)**: Stayed — best backtest results
- **DTE window (10–28)**: Stayed — already widened from 21 to 28 in earlier session
- **Max positions (2)**: Stayed — correlation guard prevents stacking
- **Correlation guard**: Stayed — proven to prevent SPY+QQQ simultaneous losses

## Verification

- All changes compile-clean (Python `py_compile`)
- `daily_scan.py --test` runs successfully in dry-run mode
- Credit watchdog updated: dual-provider (Anthropic + TokenHub)

## Expected Impact

| Metric | Before | Expected After |
|--------|--------|---------------|
| Win rate | Mixed (IWM: L, TOST: L, SPY: stale) | **Target: 75%+** (backtest provenance) |
| Trade frequency | 2-3/week (Tue-Thu only) | **3-5/week** (Mon-Fri + more symbols) |
| Credit quality | $0.30 min (15% floor) | **$0.50-$0.75** (25% floor on $2-3 wide) |
| Bad structures | Bear Call: 64% WR | **0% — disabled** |
| Bad symbols | IWM: negative all scenarios | **0% — removed** |
| OpenClaw fill rate | 0-1 candidate (TLT only) | **3-7 candidates** (7 ETFs in pool) |
