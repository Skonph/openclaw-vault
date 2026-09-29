# WILLIAMS_FRAMEWORK.md — Larry Williams Complete Reference

> Full analytical reference for Anna's Williams-integrated methodology.
> See SOUL.md for operational protocols. This file contains formulas, detailed tables, and deep context.

---

## Larry Williams — Background

- **Born**: October 6, 1942, Miles City, Montana
- **1987 World Cup Championship**: $10,000 → $1,137,600 (11,376% return) — all-time record
- **Peak equity**: ~$2M before October 1987 crash drawdown
- **Core philosophy**: "Conditional Trading" — no signal stands alone
- **Key books**: *Long-Term Secrets to Short-Term Trading*, *Trade Stocks & Commodities with the Insiders*

---

## Indicators — Formulas

### Williams %R (Percent Range)

```
%R = ((Highest High - Close) / (Highest High - Lowest Low)) × (-100)
```

| Zone | Reading | Meaning |
|---|---|---|
| Overbought | 0 to −20 | Close near top of range |
| Neutral | −20 to −80 | Mid-range |
| Oversold | −80 to −100 | Close near bottom of range |

**Critical**: In a strong uptrend, %R staying overbought = strength, NOT a sell signal.

### WILLCO (Williams Commercial Index)

**Step 1 — Calculate Q:**
```
Q = (Commercial Longs - Commercial Shorts) / Total Open Interest
```

**Step 2 — Normalize over n weeks:**
```
WILLCO = (Current Q - Min Q over n weeks) / (Max Q over n weeks - Min Q over n weeks)
```

Lookback: 26 weeks (6 months) or 156 weeks (3 years)

| WILLCO | Meaning | Action |
|---|---|---|
| 80–100% | Commercials extremely bullish | Potential bottom — look for longs |
| 20–80% | Neutral | NO signal — stand aside |
| 0–20% | Commercials extremely bearish | Potential top — look for shorts |

### Williams Accumulation/Distribution (WAD)

**True Range Components:**
- TRH = max(Today's High, Yesterday's Close)
- TRL = min(Today's Low, Yesterday's Close)

**Daily Price Move:**
- Close > Yesterday's Close: Price Move = Close − TRL
- Close < Yesterday's Close: Price Move = Close − TRH
- Close = Yesterday's Close: Price Move = 0

**Cumulative:**
- Daily WAD = Price Move × Volume
- Cumulative WAD = WAD(today) + WAD(yesterday)

**Key Signals:**
- Bullish Divergence: Price new low, WAD higher low → accumulation → reversal likely
- Bearish Divergence: Price new high, WAD fails → distribution → top forming

### Close Location Value (CLV)

```
CLV = ((Close - Low) - (High - Close)) / (High - Low)
```

| CLV | Meaning |
|---|---|
| +1.0 | Close = High (max bullish) |
| 0 | Close = Midpoint |
| −1.0 | Close = Low (max bearish) |

### Ultimate Oscillator

Combines Buying Pressure / True Range across 7, 14, 28 periods with 4:2:1 weighting. Scale: 0-100. Signals via divergence only.

### Larry Williams Proxy Index (LWPI)

Synthetic COT substitute for intraday use. Calculates (Open–Close) vs. Range relationship over lookback period. Mimics commercial positioning without waiting for weekly CFTC report.

---

## COT Report — The Three Groups

| Group | Who | Behavior | Williams' View |
|---|---|---|---|
| **Commercials** | Producers, processors, commodity users | Hedge business risk; sell into strength, buy into weakness | **FOLLOW THEM** — insiders |
| **Large Speculators** | Hedge funds, commodity funds | Trend-followers; max exposure at reversals | **Be cautious** — often late |
| **Small Speculators** | Retail traders | Typically wrong at major turns | **FADE THEM** — contrarian indicator |

### When COT Signals Are Strongest
1. WILLCO at true extreme (0% or 100%)
2. Commercials and Small Specs on **opposite sides**
3. Aligns with seasonality and technical patterns
4. Price action confirms (reversal pattern, trendline break)

### When COT Signals Are Weakest
- Neutral territory (20-80%)
- Commercials and speculators on same side
- Used standalone without price confirmation

---

## Open Interest Matrix

| Price | Open Interest | Interpretation |
|---|---|---|
| ↑ Rising | ↑ Rising | 🟢 Bullish — new longs entering, trend supported |
| ↑ Rising | ↓ Falling | 🟡 Warning — short covering rally, unsustainable |
| ↓ Falling | ↑ Rising | 🔴 Bearish — new shorts entering aggressively |
| ↓ Falling | ↓ Falling | 🟡 Potential bottom — liquidation exhaustion |

### Special OI Signals
- **1-2 day OI spikes** → climax top (panic buying)
- **1-2 day OI collapses** → washout bottom (panic liquidation)
- OI from Commercials = sustainable; OI from Speculators = unsustainable
- Trend + rising OI = continues; Trend + falling OI = likely reverses

---

## Trading Patterns

### Oops! Pattern (Gap Reversal)
- **Long**: Gap down below previous low → buy stop at previous low → enter if price recovers
- **Short**: Gap up above previous high → sell stop at previous high → enter if price drops back
- Exit: Bailout / First Profitable Opening (FPO)

### Smash Day (Emotional Trap / Reversal)
- **Buy**: Day closes below previous low (violent selloff) → next day trades above Smash Day high → buy
- **Sell**: Day closes above previous high (violent rally) → next day trades below Smash Day low → sell

### Volatility Breakout (Compression → Expansion)
- Small ranges precede large moves
- Calculate volatility factor (multiplier of previous day's range)
- Buy stops above Open + factor; Sell stops below Open − factor
- Short-term momentum trades

### Swing Point System (3-Bar Pattern)
- **Swing Low**: Bar low < both adjacent bars' lows
- **Swing High**: Bar high > both adjacent bars' highs
- Inside days are skipped
- **Uptrend**: Higher swing lows + higher swing highs
- **Downtrend**: Lower swing highs + lower swing lows

### 3-Bar Moving Average System
- 3-bar MA of daily highs + 3-bar MA of daily lows
- Uptrend: buy at low MA, sell at high MA
- Downtrend: sell at high MA, cover at low MA

---

## Cross-Market Capital Flow

| Leading Market | Leads → | Logic |
|---|---|---|
| **Bonds** (prices) | → Equities | Rising bonds often lead equity rallies |
| **Gold** | → Inflation/Risk | Signals risk appetite shifts |
| **Dollar Index** | → Commodities/Equities | Affects commodity prices, multinational earnings |
| **Yield Curve** | → Economic Regime | Inversion = recession risk; steepening = growth |
| **Money Supply (M2)** | → Asset Prices | Expansion fuels inflation; contraction = headwinds |

### Options Flow Cross-Reference
| Options Signal | Interpretation |
|---|---|
| TLT puts + SPY calls | Risk-on rotation |
| GLD calls + SPY puts | Risk-off rotation |
| UUP weakening + commodity calls | Dollar weakness → commodity rally |
| Yield curve steepening + XLF calls | Growth expectations rising |

---

## Risk Management

### The Largest Loss Position Sizing Rule
```
Contracts = (Account Balance × Risk Percent) / Largest Historical Loss
```

Based on worst-case, not average. Ensures survival through max adverse scenarios.

| Profile | Risk Per Trade |
|---|---|
| Conservative | 0.5% – 2% |
| Normal | ~5% |
| Aggressive | 10% – 15% |

### Bailout / First Profitable Opening (FPO)
- Exit at market open on the first day the trade is profitable — even if profit is one tick
- Acts as a "lifeboat" — avoids going down with a sinking ship
- Central to Williams' 1987 championship performance

### Key Quotes
- "Money management is the single most important aspect of trading"
- "A mediocre system with superior money management will outperform a superior system with poor money management"
- "What should happen but doesn't happen is the most powerful signal"
- "COT data is a map, not a clock"

---

## Timing Patterns

### Trade Day of Week (TDW)
Certain weekdays have statistical directional biases per instrument. Filter, not signal.

### Trade Day of Month (TDOM)
Beginning/end-of-month carry distinct tendencies. Align entries/exits with favorable windows.

### True Seasonal Index
Walk-forward seasonal analysis — only uses data available up to current date (no curve-fitting). Key drivers: holidays, tax seasons, elections, quarterly cycles.
