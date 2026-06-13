# Options Trading Playbook — Skon's $2K POC Account
**Platform:** Tradier (Paper Trading) | **Capital:** $2,000 | **Level:** 4 (Defined-Risk POC)
**Max Drawdown Per Trade:** $200 (10% of account) | **Updated:** 2026-05-24

---

## MARKET ENVIRONMENT SNAPSHOT — Week of May 26, 2026

| Indicator | Value | Regime |
|---|---|---|
| VIX | ~16.70 | 🟡 MODERATE (trending down from 18.17) |
| SPY | ~$742 | 🟢 UPTREND (above 20/50-day MA) |
| QQQ | ~$708 | 🟡 UPTREND, MACD rolling over |
| SPY Resistance | $748 | Near-term ceiling |
| SPY Support | $730 | Key level to defend |

**Sector Leadership (Overweight these):**
- 🥇 Semiconductors (NVDA, AVGO, SMH/SOXX) — AI infra spend dominant
- 🥈 Energy (XOM, XLE) — supply discipline, FCF expansion
- 🥉 Defense / Industrials — geopolitical demand, margin expansion

**Sector Laggards (Avoid directional longs):**
- Consumer Discretionary — cautious guidance, housing headwinds
- Commercial Real Estate (REIT) — rate sensitivity

**Key Earnings This Week (May 26):**
- LOW (Lowe's) — Tues pre-market | Consumer, discretionary caution
- TJX Companies — Tues pre-market | Off-price retail, resilient
- TGT (Target) — Tues pre-market | Consumer discretionary risk
- ADI (Analog Devices) — Semiconductor, sector leader

**Macro Context:**
- Fed rate cuts expected in 2026 → growth tailwind for QQQ/tech
- AI infrastructure capex cycle intact ($5-8T through 2030)
- VIX trending down = premium sellers in control, but watch for $748 SPY resistance rejection

---

## TRADING CURRICULUM — 3-PHASE FRAMEWORK

### Phase 1: Foundation (Weeks 1–4) — "Learn to Sell Premium"
**Goal:** Discipline over profit. Master entry criteria, sizing, and exit rules.

| Parameter | Rule |
|---|---|
| Strategies | Bull Put Spreads, Bear Call Spreads (vertical credit spreads only) |
| Max risk/trade | $150–$200 |
| Trades/week | 1–2 max (no overtrading) |
| Underlyings | SPY, QQQ, IWM only (highly liquid, tight B/A spreads) |
| DTE at entry | 14–21 days |
| Delta of short strike | 0.20–0.30 (20–30% probability of being in-the-money) |
| Profit target | Close at 50% of max credit received |
| Stop loss | Close if position doubles in cost (2× premium paid to close) |

**Phase 1 Graduation Criteria:**
- Complete 8 trades minimum
- Win rate ≥ 60%
- Zero trades exceeding $200 max loss
- Demonstrate consistent entry/exit discipline (no impulse adjustments)

---

### Phase 2: Income Engine (Weeks 5–8) — "Iron Condors & IV Crush"
**Goal:** Capture elevated IV around earnings; deploy iron condors on rangebound underlyings.

| Parameter | Rule |
|---|---|
| Strategies | Iron Condors, Earnings IC plays, Diagonal spreads |
| Trigger | IV Rank (IVR) > 50 on underlying |
| Max risk/trade | $200 (still) |
| Concurrent positions | Up to 2 |
| Underlyings | SPY, QQQ + high-IV single stocks (AMZN, AAPL, NVDA post-earnings) |
| Earnings plays | Enter 1–2 DTE before earnings; exit day-of after print |

**Phase 2 Graduation Criteria:**
- 2 consecutive profitable weeks
- Demonstrate IV crush capture on ≥2 earnings plays
- Total account drawdown never exceeded 15%

---

### Phase 3: Directional + Scale (Weeks 9–12) — "Add Conviction"
**Goal:** Incorporate directional debit spreads when trend conviction is high. Scale position count.

| Parameter | Rule |
|---|---|
| Strategies | Debit spreads (sector calls), 0DTE SPY scalps (1 contract max), Wheel |
| Max concurrent positions | 3 |
| Total portfolio risk | ≤ $500 at any time |
| Directional trigger | Sector breakout + volume confirmation + VIX < 20 |

---

## THREE CORE STRATEGIES — CURRENT CONDITIONS

### Strategy 1: Bull Put Spread (PRIMARY — Use Now)
**Best when:** VIX 13–22, market in uptrend or mild consolidation, no imminent macro shock
**Logic:** Sell an OTM put spread below key technical support. Time decay (theta) works for you every day. Define max loss upfront.

**Setup Template:**
- Sell the put at ~0.25 delta (~15–20% OTM)
- Buy the put 2–5 strikes lower (define risk)
- 14–21 DTE entry window
- Close at 50% max profit

**Current Fit:** ✅ VIX 16.70, SPY uptrend intact, clear support at $730 — ideal conditions

---

### Strategy 2: Earnings Iron Condor (SECONDARY — Use on high-IVR events)
**Best when:** IVR > 60 on underlying within 1–5 DTE of earnings
**Logic:** Elevated IV before earnings = overpriced options. Sell a strangle (OTM call + OTM put), buy wings for defined risk. IV crushes after the print, options lose value fast.

**Setup Template:**
- Sell the straddle at 1× expected move on each side
- Buy wings 2–3 strikes further out
- Enter 1–2 days before earnings, exit same day as print
- Target: 30–50% of max credit

**Current Fit:** ✅ TJX, ADI, LOW reporting this week — screen for IVR > 60

---

### Strategy 3: Debit Call Spread — Sector Leadership (DIRECTIONAL — Use when trend is clear)
**Best when:** VIX < 20, clear sector breakout, 3:1 reward/risk minimum
**Logic:** Buy a call spread on a leading sector ETF (SMH, XLE). Defined risk, defined reward. Profit from continued sector momentum.

**Setup Template:**
- Buy ATM or slight OTM call (0.50–0.40 delta)
- Sell OTM call 3–5% above entry (cap upside, reduce cost)
- 21–30 DTE
- Target: 50–75% of max profit

**Current Fit:** 🟡 Semiconductors are leading but QQQ MACD is rolling — wait for dip confirmation before entry

---

## WEEK 1 TRADE SETUP — LIVE PAPER TRADE

### TRADE #001 | SPY Bull Put Spread
```
Status:        PENDING YOUR APPROVAL
Strategy:      Bull Put Spread (defined risk, credit spread)
Underlying:    SPY (SPDR S&P 500 ETF Trust)
Current Price: ~$742 (as of May 22 close)
Expiration:    June 6, 2026 (12 DTE — theta-sweet-spot)
Structure:     SELL SPY $732 Put / BUY SPY $730 Put
Spread Width:  $2.00
```

#### Entry Criteria (ALL must be green before submitting)
- [ ] SPY price > $737 at time of entry (short strike $732 has >$5 buffer)
- [ ] VIX < 22 (no panic spike)
- [ ] SPY is above its 20-day moving average
- [ ] Net credit received ≥ $0.40 (otherwise pass — not worth the risk)
- [ ] No Fed announcement or CPI data same day

#### Position Sizing
| Metric | Value |
|---|---|
| Contracts | 1 |
| Width | $2.00 |
| Target Credit | $0.40–$0.55 |
| Max Profit | ~$45 (if credit = $0.45) |
| Max Loss | ~$155 (if credit = $0.45) |
| Breakeven at expiry | ~$731.55 |
| % of account at risk | ~7.75% |
| Prob. max profit | ~78% (SPY must stay above $732) |

#### Exit Rules (follow in priority order)
1. **Profit Target** — BTC spread at $0.22 or less (~50% profit). Do NOT hold to expiration.
2. **Stop Loss** — BTC immediately if spread trades at $0.90 (2× entry) or worse
3. **Time Stop** — Close at market if still open with 2 DTE remaining (eliminates gamma risk)
4. **SPY Alert** — If SPY breaks $735 intraday, re-evaluate and consider early close

#### Trade Management Schedule (30 min/day)
| Action | When |
|---|---|
| Check P&L + SPY level | Once per day, preferably 9:45–10:00 AM ET |
| No action needed if | SPY > $737, spread < $0.35 |
| Monitor closely if | SPY between $733–$737 |
| Close immediately if | SPY < $730 or spread > $0.90 |

---

## TRADIER API AUTOMATION FRAMEWORK

### API Base URL (Paper Trading)
```
https://sandbox.tradier.com/v1/
```

### Authentication Header
```python
headers = {
    "Authorization": "Bearer YOUR_PAPER_TRADING_TOKEN",
    "Accept": "application/json"
}
```

### Step 1: Morning Market Scan (Automated Daily)
```python
# Fetch key market quotes
GET /v1/markets/quotes?symbols=SPY,QQQ,IWM,VIX,SMH,XLE
```

### Step 2: Fetch Options Chain for Trade Construction
```python
# Get SPY options chain with Greeks for June 6
GET /v1/markets/options/chains?symbol=SPY&expiration=2026-06-06&greeks=true
```
Filter for: strikes $728–$736, puts only, calculate net credit for $732/$730 spread

### Step 3: Validate Entry Criteria (Automated Check)
```python
# Automated pre-flight checklist
entry_criteria = {
    "spy_above_737": spy_price > 737,
    "vix_below_22": vix_level < 22,
    "credit_at_least_40_cents": net_credit >= 0.40,
    "no_major_catalyst": check_economic_calendar()
}
all_green = all(entry_criteria.values())
```

### Step 4: Construct Order (Presented for Your Approval)
```python
# Multi-leg order — REQUIRES YOUR APPROVAL BEFORE SUBMISSION
order_payload = {
    "class": "multileg",
    "symbol": "SPY",
    "type": "limit",
    "price": "0.45",       # Net credit (limit order, adjust to live quote)
    "duration": "day",
    "option_symbol[0]": "SPY260606P00732000",
    "side[0]": "sell_to_open",
    "quantity[0]": "1",
    "option_symbol[1]": "SPY260606P00730000",
    "side[1]": "buy_to_open",
    "quantity[1]": "1"
}

# Submit after approval:
POST /v1/accounts/{account_id}/orders
```

### Step 5: Monitor Active Positions
```python
# Check live P&L on open positions
GET /v1/accounts/{account_id}/positions

# Fetch current option prices to calculate unrealized P/L
GET /v1/markets/quotes?symbols=SPY260606P00732000,SPY260606P00730000
```

### Step 6: Close Order (Profit Target or Stop)
```python
# BTC order — close at $0.22 (profit target)
order_payload = {
    "class": "multileg",
    "symbol": "SPY",
    "type": "limit",
    "price": "0.22",       # Debit to close
    "duration": "gtc",     # Good-til-cancelled
    "option_symbol[0]": "SPY260606P00732000",
    "side[0]": "buy_to_close",
    "quantity[0]": "1",
    "option_symbol[1]": "SPY260606P00730000",
    "side[1]": "sell_to_close",
    "quantity[1]": "1"
}
POST /v1/accounts/{account_id}/orders
```

---

## TRADE LOG

| # | Date | Strategy | Underlying | Structure | Credit | Max Risk | Status | P&L | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 001 | 2026-05-26 | Bull Put Spread | SPY | $732/$730 Jun6 | TBD | ~$155 | PENDING | — | Week 1 POC entry |

---

## RISK MANAGEMENT RULES (NON-NEGOTIABLE)

1. **Hard max loss per trade: $200** — No exceptions, no "let it come back"
2. **Max 2 open positions simultaneously** (Phase 1) — Concentration is discipline
3. **Never sell naked options** — Defined risk only during POC
4. **50% profit rule** — Take the win at 50%, don't get greedy near expiration
5. **Earnings blackout** — Do NOT hold positions through earnings on the underlying
6. **VIX spike rule** — If VIX spikes >25 in a single day, close all positions same day, go to cash
7. **Weekly review** — Every Sunday: log all trades, calculate win rate, assess Phase graduation readiness

---

## WEEKLY REVIEW TEMPLATE

### Week of: ___________
- Trades opened: ___
- Trades closed: ___
- Win/Loss: ___ / ___
- Total P&L: $___
- Largest single loss: $___
- VIX range this week: ___ – ___
- SPY range this week: ___ – ___
- Key lesson learned:
- Phase graduation progress: Phase ___ | Criteria met: ___ / ___

---

*Playbook maintained via Tradier API + Claude automation. All trades require manual approval before execution.*
