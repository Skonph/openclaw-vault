# PION2 MULTI-ASSET DIVERSIFICATION & 10-YEAR BACKTEST MANDATE
**Directive for Hermes (CEO/COO) and Anna (VP Market Intelligence)**

## 1. Core Mandate Objective
Pion2 (`PA3C75K8SZ57`) is explicitly designated as our **Decorrelated Multi-Asset System**. It must not mirror 100% SPY/QQQ index beta. All trade setups on Pion2 must originate from the **10-Year Backtested Winning Strategy Framework**:

1. **Asset Diversification Beyond Main Index Beta**:
   - **Bond & Rates Beta**: `TLT` (20+ Year Treasuries), `IEF` (7-10 Year Treasuries). Spikes on Fed policy, zero correlation to equity beta.
   - **Commodity Beta**: `GLD` (Gold), `SLV` (Silver). Macro inflation & flight-to-safety hedges.
   - **Sector ETFs**: `XLE` (Energy), `XLF` (Financials), `XLI` (Industrials), `IWM` (Small Caps), `XLV` (Healthcare), `XLU` (Utilities).
   - **IBKR MultiSort Liquid Single Names**: Candidates meeting Volume 1M-10M, IV Percentile 0-5%, IV Rank 0-25 (`APGE`, `TECH`, `MTG`, `KIM`, `OGE`, `CDP`, `ORI`, `AEG`, `HESM`, `HST`, `FIVE`, `COGT`, `ELVN`, `AMBP`, `HR`, `CUBE`, `BGC`, `KT`, `ZION`).

2. **10-Year Backtested Strategy Standard (Spina / Williams)**:
   - **Structure**: Defined-Risk Bull Put & Bear Call Spreads.
   - **Target DTE**: 30 to 45 DTE (ideal decay curve).
   - **Delta Target**: ~20 Delta short strike ($2–$5 strike width).
   - **Conviction Threshold**: $\ge 75$ conviction score (4/5 Tumblers PASS).

---

## 2. Automated Execution Pipeline Alignment
- **Scanner**: `shared/pion2_scanner.py` runs across the 43 multi-asset universe using parallel worker threads.
- **Post-FOMC Deployment**: First post-FOMC deployment triggers Thursday Jul 30 at 18:30 ICT.
- **Closed-Loop Verification**: Intent Nodes locked in `intent_graph.json` enforce position count and risk budget checks ($\le 15\%$ total risk budget).
