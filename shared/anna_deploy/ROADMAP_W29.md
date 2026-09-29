# Pre-Week 29 Capital Velocity & MRR Roadmap

> [!IMPORTANT]  
> **Target Execution Window**: Pre-Week 29 Planning (Sunday, Aug 9, 2026)  
> **Core Intent**: Accelerate Monthly Recurring Return (MRR) & Capital Turnover leveraging Week 28 empirical data.

---

## Key Milestone Objectives

### 1. High-Velocity Position Rollover (Capital Recycling)
* **Goal**: Automatically recycle margin buying power as soon as positions hit 50% max profit.
* **Mechanism**: Dynamic GTC limit orders + instant margin release to re-deploy into new high-ROC spreads.

### 2. Pion2 Weekly Alpha Engine (LIVE) & 0DTE Shadow Engine (PAPER)
* **Pion2 Live Engine**: Deployed `pion2_weekly_alpha.py` targeting **+$20.00 USD / week ($80/mo)** via 14–21 DTE 15-delta options (yielding **+$27.50/wk** live). Covers $70/mo OpEx with zero intraday stress.
* **0DTE Live Execution Posture**: **ON HOLD** for live real money to prevent explosive intraday gamma spikes ($\Gamma$) and 60-second execution latency risks.
* **0DTE Shadow Module**: Build `0dte_shadow_engine.py` as a paper-trading background module to collect 30 trading days of empirical win-rate, slippage, and drawdown data before evaluating live deployment.

### 3. Sub-Minute Bridge Latency Tuning (<60s Checks)
* **Goal**: Upgrade Hermes bridge listener to sub-minute execution latency during active trading hours (20:30–03:00 ICT).
* **Metric**: Reduce worst-case verdict-to-order latency from 14 minutes to <60 seconds.

---

## Operating Expense Breakeven Compliance (RULE-011)

- **Itemized Monthly Expenses**:
  - TokenHub API Token Fee: **$45.00/mo**
  - Server Rental Fee: **$5.00/mo**
  - Antigravity AI Pro Plan: **$20.00/mo**
  - **Total OpEx Target**: **$70.00 USD / month** (~$17.50 USD / week).
- **Pion2 Yield Coverage**: **+$27.50 USD / week ($110.00 / month)** $ightarrow$ **100% Compliant (+ $40/mo net surplus)**.
