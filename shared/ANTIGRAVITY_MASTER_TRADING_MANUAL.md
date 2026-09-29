# 🦅 ANTIGRAVITY MASTER TRADING MANUAL
**The Canonical Single-Source Knowledge Base & Strategy Framework**
*Version 5.0 — Consolidated Quantitative Architecture*

---

## EXECUTIVE SUMMARY

This document serves as the **single source of truth** for the Antigravity Autonomous Trading Architecture. It consolidates all quantitative trading methodologies, institutional market frameworks, risk controls, and context engineering protocols into a unified system ready to deploy across AI agents, system prompts, and execution engines.

---

## SECTION 1: THE 5-AGENT SYSTEM ARCHITECTURE

The trading engine operates as a decentralized, multi-agent autonomous desk across 5 specialized roles:

```
                          ANTIGRAVITY 5-AGENT DESK
                                     │
         ┌───────────────────────────┼───────────────────────────┐
         ▼                           ▼                           ▼
  1. ANNA (Flowmaster)        2. HERMES (CSO)            3. CONVICTION SCORER
  (VP Market Intelligence)   (Execution & Post-Mortem)    (Adversarial Debate)
         │                           │                           │
         └───────────────────────────┼───────────────────────────┘
                                     ▼
                            4. OPENCLAW / TRADIER
                            (Automated Execution)
                                     │
                                     ▼
                            5. PORTFOLIO AUDITOR
                            (Global Risk & IBKR Guardrail)
```

1. **Anna ("The Flowmaster" — VP Market Intelligence):** Analyzes macro data, Greeks, volatility surfaces, COT positioning, Markov regimes, and unusual option flow. Outputs daily market intelligence briefs and trade verdicts via `agent_bridge.py`. Does not execute trades.
2. **Hermes (CSO & Operational Lead):** Handles trade execution approval, preflight context gathering (`hermes_preflight.py`), daily kill-switch enforcement, and post-mortem loss analysis (`post_mortem_agent.py`).
3. **Conviction Scorer & Adversarial Debate Engine (`adversarial_debate_engine.py`):** Runs an objective Bull vs Bear debate for every candidate, requiring Net Conviction $\ge 70/100$ before trade authorization.
4. **OpenClaw & Tradier Execution Bots:** Autonomous order placement daemons connected to Alpaca and Tradier APIs, executing defined-risk credit spreads and iron condors.
5. **Portfolio Auditor & Risk Guardrail:** Global risk boundary daemon enforcing account drawdown limits, cash buffer reserves, and portfolio beta weighting.

---

## SECTION 2: THE SYNTHESIZED 6-TUMBLER COMBINATION LOCK

Before any high-conviction trade signal is issued, it must unlock the **6-Tumbler Combination Lock**. A minimum alignment of **4/6 tumblers** is required for notable signals; **5/6** for high conviction; **6/6** for maximum conviction. Signals below 4 tumblers are automatically rejected.

```
       ┌─────────────────────────────────────────────────────────────┐
       │               THE 6-TUMBLER COMBINATION LOCK                │
       ├─────────────────────────────────────────────────────────────┤
       │ 🔓 TUMBLER 1: Seasonal Window & Calendar Expirations        │
       │ 🔓 TUMBLER 2: COT & Institutional Positioning (WILLCO)       │
       │ 🔓 TUMBLER 3: Market Regime & Markov Persistence (Stickiness) │
       │ 🔓 TUMBLER 4: Technical, Location & Volatility Setup        │
       │ 🔓 TUMBLER 5: Risk Parameters, Sizing & Exits (1.5-Day / 24h)│
       │ 🔓 TUMBLER 6: Adversarial Swarm Net Conviction (≥ 70/100)    │
       └─────────────────────────────────────────────────────────────┘
```

### Tumbler Details:

1. **Tumbler 1: Seasonal Window & Calendar Expirations**
   - Check seasonal tendencies (True Seasonal, TDW/TDOM, election/earnings cycles).
   - Hard Veto: Skip entries 0–2 days before earnings or FOMC/CPI announcements.

2. **Tumbler 2: COT & Institutional Positioning**
   - Commercial trader positioning via **WILLCO (Williams Commercial Index)**.
   - Extremes ($<20\%$ or $>80\%$) signal institutional accumulation/distribution. Cross-reference with options Open Interest (OI) concentrations.

3. **Tumbler 3: Market Regime & Markov Persistence**
   - Quantitative 3-State Markov Model (`BULL`, `SIDEWAYS`, `BEAR`) calculated via rolling 20-day returns and volatility.
   - **Persistence (Stickiness) $\ge 65\%$:** Confirms stable state.
   - **+GEX vs -GEX Regime:** $+GEX$ (price above GEX flip) favors mean-reverting theta strategies (Iron Condors / Strangles); $-GEX$ (price below GEX flip) favors directional credit spreads or protective hedging.

4. **Tumbler 4: Technical, Location & Volatility Setup**
   - **Larry Williams Patterns:** Oops! (gap reversal), Smash Day (emotional trap), Volatility Breakout (range compression).
   - **Volume Profile Equilibrium:** Value Area High (VAH), Value Area Low (VAL), Point of Control (POC).
   - **Box Theory Location Filter:** Dips near Previous Day Low (PDL) $\to$ Bull Put Spreads; Rallies near Previous Day High (PDH) $\to$ Bear Call Spreads. Veto entries in the Middle 30% Chop Zone ($35\% - 65\%$).
   - **Reality Bands:** Volatility ATR bounds to eliminate false breakouts.

5. **Tumbler 5: Risk Parameters, Sizing & Mechanical Exits**
   - **Position Sizing:** 1–5% of Buying Power per trade; Largest Loss Rule for directional trades.
   - **Cash Buffer:** 50% Cash Buffer held in reserve.
   - **50–60% Max Profit Target:** Log GTC limit order immediately upon entry.
   - **21 DTE Gamma Risk Exit:** Automatically close/roll short premium at 21 DTE.
   - **1.5-Day (36h) Cadence / 24h Express Mode:** Fast Harvest at 24h if Conviction $\ge 85.0$ or Kalman $Z \ge +2.5\sigma$; Option A Recycle exit at 36h if profit $\ge 25\%$.

6. **Tumbler 6: Adversarial Swarm Debate (`RULE-039`)**
   - Candidate must undergo a Bull vs Bear debate in `adversarial_debate_engine.py`.
   - Requires Net Conviction Score $\ge 70.0/100$ and zero unhedged tail risks.

---

## SECTION 3: CONSOLIDATED TRADING STRATEGY COMPENDIUM

This section consolidates all 8 core trading frameworks into a single unified knowledge repository:

```
               CONSOLIDATED STRATEGY COMPENDIUM
                              │
 ┌────────────────────────────┼────────────────────────────┐
 ▼                            ▼                            ▼
1. LARRY WILLIAMS          2. JULIA SPINA               3. TOM SOSNOFF
(Macro & COT)              (Options & VRP)              (Boring Mechanics)
 │                            │                            │
 ├────────────────────────────┼────────────────────────────┤
 ▼                            ▼                            ▼
4. LEWIS JACKSON           5. CHRIS CREAMER             6. FABIO & MARCI
(Markov Regimes)           (Robbins Cup GEX)            (Footprint & Bands)
 │                            │                            │
 └────────────────────────────┼────────────────────────────┘
                              ▼
                   7. BOX THEORY & 8. UOB 1.5D
                   (Location & 36h Cadence)
```

### 1. Larry Williams Framework (Macro & Direction)
- **COT Index / WILLCO:** Quantifies commercial smart money accumulation.
- **WAD (Williams Accumulation/Distribution):** Detects divergence between price and institutional buying/selling pressure.
- **Close Location Value (CLV):** Measures where the daily close lands relative to the day's high-low range.
  $$\text{CLV} = \frac{(\text{Close} - \text{Low}) - (\text{High} - \text{Close})}{\text{High} - \text{Low}}$$
- **Patterns:** *Oops!* (gap open opposite yesterday's move, price reverses into yesterday's range), *Smash Day* (extreme close, immediate reversal), *Volatility Breakout* (narrow range compression followed by range expansion).

### 2. Julia Spina / Tastytrade Options Framework (Volatility & Mechanics)
- **Volatility Risk Premium (VRP):** Exploits the systematic overstatement of Implied Volatility (IV) relative to Realized Volatility (RV).
- **IV Rank (IVR) & IV Percentile (IVP):**
  $$\text{IV Rank} = \frac{\text{Current IV} - \text{52W Low IV}}{\text{52W High IV} - \text{52W Low IV}} \times 100$$
  - Sell premium when IVR or IVP $> 50\%$. Stand aside when $< 20\%$.
- **45 DTE Trade Entry:** Optimal balance between accelerating theta decay and manageable gamma risk.
- **Managing Winners at 50% Max Profit:** Maximizes win rate and capital velocity while mitigating risk/reward decay.
- **21 DTE Expiration Management:** Close or roll positions at 21 DTE to eliminate exploding gamma risk and assignment threats.

### 3. Tom Sosnoff "11 Boring Trading Strategies" (Mechanization & Probability)
- **Probability of Profit (POP) > Max Profit:** A "boring" trade with a 70% chance of making $50 is mathematically superior over 1,000 occurrences than an "exciting" 30% POP trade aiming for $500.
- **Excitement Costs Money; Boring Pays Rent:** Mechanized entry and exit rules remove emotional over-trading ("tilting").
- **Daily Kill Switch:** Automatic shutdown if daily portfolio loss reaches max drawdown limit.

### 4. Lewis Jackson Markov Regime Model (Quant State Persistence)
- **3-State Classification:** Categorizes market into `BULL` (0), `SIDEWAYS` (1), or `BEAR` (2) using rolling 20-day returns and volatility.
- **Transition Probability Matrix ($3 \times 3$):** Calculates state persistence $P(S_t \to S_t)$.
- **Strategy Fit:**
  - `SIDEWAYS` + Sticky ($P \ge 65\%$): 45 DTE Iron Condors / Short Strangles.
  - `BULL` / `BEAR` + Sticky ($P \ge 65\%$): Directional Credit Spreads (Bull Put / Bear Call).
  - Low Persistence ($P < 50\%$): High transition risk $\to$ Reduce sizing by 50% or stand aside.

### 5. Chris Creamer Robbins World Cup Champion Framework (Orderflow & GEX)
- **Step 1: Environment (GEX Volatility Regime):** $+GEX$ $\to$ Mean reversion & theta selling; $-GEX$ $\to$ Volatility expansion & directional spreads/hedging.
- **Step 2: Location (Value Areas):** Define entry zones using Volume Profile Value Areas (VAH, VAL, POC) and Fibonacci retracements (0.705, 0.788, 0.886).
- **Step 3: Confirmation (Orderflow Shift):** Absorption at structural extremes followed by aggressive market sweeps.
- **Step 4: Timing Window:** Trade execution restricted to the high-liquidity window (first 90 minutes of NY Open: 09:30–11:00 EST / 20:30–22:00 ICT).

### 6. Fabio Valentino & Marci Silfrain Frameworks (Footprint & Reality Bands)
- **Footprint / Intraday Orderflow:** Track bid-ask imbalances and institutional absorption at key levels.
- **"Reality Bands":** Combine Bollinger Bands with ATR volatility bounds to filter out false breakouts and confirm true exhaustion.

### 7. Box Theory Strategy (PDH/PDL Range Box & Location Filter)
- **Previous Day High (PDH) & Low (PDL) Box:** Connect PDH and PDL to form daily structural boundaries.
- **Top 35% Box (Premium Zone):** Sell Bear Call Spreads.
- **Bottom 35% Box (Discount Zone):** Sell Bull Put Spreads.
- **Middle 30% Box (35%–65% Range):** **NO-TRADE CHOP ZONE.** All entries vetoed.

### 8. UOB "Resilience Over Perfection" Strategy (36h Cadence & AI Infrastructure)
- **Resilience Over Perfection:** Focus on steady capital turnover rather than squeeze-holding for 100% profit.
- **36h Option A Cadence & 24h Express Mode:**
  - Fast harvest at 50%–60% profit target (or at 24h in Express Mode when Conviction $\ge 85.0$ / Kalman $Z \ge +2.5\sigma$).
  - Auto-close at 36h if profit $\ge 25\%$ to recycle capital into fresh setups.
- **AI Infrastructure Universe (14 Tickers):**
  - *Semis / Hardware:* NVDA, AVGO, AMD
  - *Data Center / Power / Utilities:* VRT, CEG, GE, XLU
  - *Decoupled Low-Beta Anchors:* GLD, XLP, JNJ

### 9. Wyckoff Accumulation & Spring Sweep Framework (Liquidity Traps & Exhaustion)
- **Phases of Accumulation:** Identifies Preliminary Support (PS), Selling Climax (SC), Automatic Rally (AR), Secondary Test (ST), and Spring (Phase C).
- **Spring Liquidity Trap:** Intraday price piercing below PDL/VAL support followed by a fast range reclaim within 60 minutes. Triggers highest conviction Bull Put Spread entries (+15 pts).
- **Supply Exhaustion Confirmation:** Secondary Test (ST) volume must show a $\ge 30\%$ reduction relative to initial Selling Climax (SC) volume ($V_{\text{ST}} \le 0.70 \times V_{\text{SC}}$) to confirm selling pressure has dried up.

### 10. 20 SMA Slope Health & Overextension Veto Framework (Parabolic Risk & Retracements)
- **20 SMA Slope Classifier:** Evaluates 5-day slope angle in degrees ($\text{Angle} = \arctan(\Delta \text{SMA20}) \times \frac{180}{\pi}$).
- **Overextension Veto ($\text{Angle} > 55^\circ$ or Distance $> 3.0\times\text{ATR}$):** Vetoes Bull Put Spreads to protect against buying-climax reversals and parabolic drops.
- **Sustainable Trend Bonus ($25^\circ \le \text{Angle} \le 50^\circ$):** Grants +15 Conviction Bonus when price pulls back to the rising 20 SMA.
- **Base Consolidation ($-15^\circ \le \text{Angle} \le 15^\circ$):** Requires volume-contracted base breakout before credit spread authorization.

### 11. The 3R Rule & 50 SMA Macro Trend Framework (Primary Trend & Retracements)
- **R1: Regard Primary Trend (50 SMA Gate):**
  - Golden Trend Alignment ($\text{Price} > \text{SMA50}$ and $\text{SMA20} > \text{SMA50}$): +10 Conviction Bonus.
  - Macro Caution Gate ($\text{Price} < \text{SMA50}$): Caps BP utilization at 35% reserve & requires Wyckoff Spring confirmation.
- **R2: Risk-Reward Ratio Asymmetry:** Enforces positive Expected Value ($EV > 0$) via high Probability of Profit ($POP \ge 85\%$) paired with 50%–60% profit target takes.
- **R3: Retracement Entry Gate:** Restricts credit spreads strictly to 20 SMA retracements or PDL box discounts, prohibiting entries at 5-day price highs.




---

## SECTION 4: QUANTITATIVE FORECASTING & VOLATILITY SUITE

The system runs 5 quantitative forecasting algorithms in `shared/`:

```
                    QUANTITATIVE FORECASTING SUITE
                                   │
 ┌─────────────────────────┬───────┴───────┬─────────────────────────┐
 ▼                         ▼               ▼                         ▼
1. KALMAN FILTER     2. GARCH(1,1)   3. MARKOV REGIME          4. GEX BOUNDARY
(Dynamic Fair Value) (VRP Forecast)  (Persistence Score)       (Walls & Flip)
 │                         │               │                         │
 └─────────────────────────┴───────┬───────┴─────────────────────────┘
                                   ▼
                       5. MULTI-FACTOR ENSEMBLE
                      (Adversarial Score ≥ 70/100)
```

1. **Kalman Filter Dynamic Fair Value (`kalman_forecaster.py`):**
   - Recursively updates dynamic fair value and residual standard error ($\epsilon_t$).
   - Deviations $> 1.5\sigma$ forecast mean reversion within 24–48 hours. Residuals $\ge +2.5\sigma$ trigger **24h Express Mode**.
2. **GARCH(1,1) Volatility Forecaster (`garch_vol_forecaster.py`):**
   - Predicts tomorrow's Realized Volatility ($\sigma_{\text{forecast}}$).
   - If $IV > 1.25 \times \sigma_{\text{forecast}}$, triggers **VRP Mispricing Harvest Signal**. If Spread $\ge +8.0\%$, upgrades profit target to 60%.
3. **Markov Regime Forecaster (`shared/markov_regime.py`):**
   - Calculates state transition matrix and persistence score.
4. **GEX Boundary Predictor (`gex_boundary_forecaster.py`):**
   - Calculates GEX Flip, Call Wall, and Put Wall boundaries.
5. **Multi-Factor Quant Ensemble (`quant_ensemble_forecaster.py`):**
   - Synthesizes technical, options, macro, and GEX signals into a normalized conviction score (0–100).
   - **Backtest Accuracy Rule (`RULE-044`):** All forecasting models must achieve $\ge 65.0\%$ historical backtest accuracy before live signal activation.

---

## SECTION 5: CONTEXT ENGINEERING & OPERATIONAL PROTOCOLS

To maintain low token costs (~84% savings) and high execution accuracy (~39% boost) during long sessions, the system enforces strict context engineering protocols:

### @noisyb0y1 6-File Context Pointer Architecture
The LLM context window carries only pointers to 6 disk-anchored files, avoiding raw history dumps:

```
                            6-FILE POINTER ARCHITECTURE
                                         │
    ┌────────────────┬───────────────────┼───────────────────┬────────────────┐
    ▼                ▼                   ▼                   ▼                ▼
1. CLOSED_LOOP    2. PREFLIGHT         3. INTENT           4. TASK          5. POST_MORTEM    6. WALKTHROUGH
   PROTOCOL.md       summary.md           graph.json          task.md          log.jsonl         walkthrough.md
(System Rules)    (State Snapshot)     (Commitments)       (Progress)       (Journal/Rules)   (Verification)
```

### Core Execution Directives (`RULE-039` to `RULE-043`):

* **`RULE-039` (Adversarial Swarm Debate):** Execution permitted ONLY if Net Conviction Score $\ge 70.0/100$ and Bear tail risks are strictly hedged.
* **`RULE-040` (Quant Dynamic Projections):**
  - Quant Conviction $\ge 85.0 \to$ Project 2 contracts.
  - Kalman $Z \ge +2.5\sigma \to$ Accelerate hold time to 24h Express Mode.
  - VRP Spread $\ge +8.0\% \to$ Upgrade profit target from 50% to 60%.
* **`RULE-041` (SQLite WAL Mode & Lock Hardening):** All SQLite DBs (`bridge.db`, `intent_graph.json`) MUST enable Write-Ahead Logging (`PRAGMA journal_mode = WAL;`) with 30,000ms busy timeout. Auto-healed daily at 06:00 ICT.
* **`RULE-042` (Signal Priority Hierarchy):**
  - *Top Priority (40% Weight):* 36h Option A Cadence & Decoupled Low-Beta Assets (`GLD`, `XLP`, `JNJ`, `XLU`).
  - *High Priority (35% Weight):* Adversarial Swarm Net Conviction Score ($\ge 70/100$).
  - *Medium Priority (15% Weight):* Institutional UOA Volume Anomalies ($\text{Vol} > 3.0\times \text{OI}$).
  - *Background Filter (10% Weight):* Macro VIX Regime & Weekly COT Data.
* **`RULE-043` (Market Session Polling & Token Optimization):**
  - FastHarvest poller terminates strictly at 03:30 ICT (30m post-market close) to eliminate post-close token bloat.
  - Morning Digest (08:00 ICT) generates output from pre-compiled disk snapshots (`position_snapshots.json`), saving ~5.2M tokens/day.

---

## SECTION 6: READY-TO-USE SYSTEM PROMPT INJECTION

To load this knowledge base directly into a new Antigravity account or system prompt, copy the block below:

```markdown
SYSTEM DIRECTIVE: YOU ARE ANNA / HERMES OPERATING UNDER THE ANTIGRAVITY MASTER TRADING MANUAL V5.0.

CORE DIRECTIVES:
1. UNLOCK THE 6-TUMBLER COMBINATION LOCK BEFORE ISSUING TRADE SIGNALS (MINIMUM 4/6 ALIGNMENT REQUIRED).
2. ENFORCE THE 6-FILE CONTEXT POINTER ARCHITECTURE: NEVER DUMP RAW LOGS INTO PRIMARY CONTEXT. CONSUME preflight_summary.md AND USE ON-DEMAND LINE SLICING.
3. EXECUTE THE 1.5-DAY (36-HOUR) OPTION A CADENCE OR 24H EXPRESS MODE (WHEN KALMAN Z >= +2.5σ OR CONVICTION >= 85.0).
4. SET GTC LIMIT ORDERS AT 50%–60% MAX PROFIT UPON ENTRY. SYSTEMATICALLY EXIT OR ROLL AT 21 DTE.
5. RESPECT THE BOX THEORY NO-TRADE CHOP ZONE (35%–65% RANGE MIDPOINT).
6. RUN ADVERSARIAL SWARM DEBATE: REQUIRE NET CONVICTION SCORE >= 70/100 AND HEDGED BEAR RISKS BEFORE EXECUTION.
```

---
*End of Master Trading Manual — Consolidating All Strategy Frameworks & Context Architecture.*
