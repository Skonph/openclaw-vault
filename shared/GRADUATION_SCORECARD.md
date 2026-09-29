# Graduation Scorecard — Paper → Real Account (cost-aware)
_Generated 2026-08-18 18:01. Verdict on NET-of-cost numbers. Criteria: ≥30 trades, net expectancy > $0, net PF ≥ 1.3, max DD ≤ 15%, and net run-rate > $90/mo fixed overhead. Commissions: Tradier $0.35/Alpaca $0.05/IBKR $0.65 per contract. Real trades only._

## Combined — ⏳ KEEP PAPER TRADING
### All systems — ⏳ NOT READY
- Trades: **9**  |  Win rate: **22%**  |  Fees paid: **$10.20**
- **Gross**: P&L $-340.00 · exp $-37.78 · PF 0.19
- **Net (after commissions)**: P&L $-350.20 · exp $-38.91 · PF 0.18 · max DD $350 (2.2%)
- By strategy (net): Bull Put Spread (5, 40%WR, $-107); bear_put (2, 0%WR, $-95); debit_put_spread (1, 0%WR, $-133); unknown (1, 0%WR, $-15)
- Gaps: sample 9 < 30 required; net expectancy $-38.91 not > $0.0; net profit factor 0.18 < 1.3; net run-rate $-507.62/mo does not cover $90/mo fixed overhead

## Economics (the real break-even)
- Fixed overhead: **$90/mo ($1,080/yr)** = **6.8%** of the $16,000 live account.
- Net P&L run-rate: **$-508/mo**  (≈ annualized **-38.1%** on $16,000)  →  ❌ does NOT cover fixed costs.
- Min viable capital for ≤7% fixed-cost drag: **$15,429**.

## Per system
### Tradier — ⏳ NOT READY
- Trades: **5**  |  Win rate: **40%**  |  Fees paid: **$7.00**
- **Gross**: P&L $-100.00 · exp $-20.0 · PF 0.45
- **Net (after commissions)**: P&L $-107.00 · exp $-21.4 · PF 0.43 · max DD $109 (0.7%)
- By strategy (net): Bull Put Spread (5, 40%WR, $-107)
- Gaps: sample 5 < 30 required; net expectancy $-21.4 not > $0.0; net profit factor 0.43 < 1.3

### Openclaw — ⏳ NOT READY
- Trades: **3**  |  Win rate: **0%**  |  Fees paid: **$0.60**
- **Gross**: P&L $-110.00 · exp $-36.67 · PF 0.00
- **Net (after commissions)**: P&L $-110.60 · exp $-36.87 · PF 0.00 · max DD $111 (3.7%)
- By strategy (net): bear_put (2, 0%WR, $-95); unknown (1, 0%WR, $-15)
- Gaps: sample 3 < 30 required; net expectancy $-36.87 not > $0.0; net profit factor 0.00 < 1.3

### Guardrail — ⏳ NOT READY
- Trades: **1**  |  Win rate: **0%**  |  Fees paid: **$2.60**
- **Gross**: P&L $-130.00 · exp $-130.0 · PF 0.00
- **Net (after commissions)**: P&L $-132.60 · exp $-132.6 · PF 0.00 · max DD $133 (0.1%)
- By strategy (net): debit_put_spread (1, 0%WR, $-133)
- Gaps: sample 1 < 30 required; net expectancy $-132.6 not > $0.0; net profit factor 0.00 < 1.3


_Graduation hinges on NET expectancy + profit factor + bounded drawdown over a real sample AND out-earning the fixed monthly overhead — not gross paper P&L or raw win rate._