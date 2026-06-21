# Backtest Report

Generated: 2026-06-13T10:55:37  
Policy: **MODERATE**  |  Symbols: SPY, QQQ  |  Days: 120  |  Seed: 7  |  Data: synthetic GBM

> Synthetic data. Run with `--real` to use historical closes from Tradier.

## Headline metrics

| Metric | Value |
|---|---|
| Trades | 29 |
| Win rate | 37.9% |
| Avg win / loss | $2,700 / $-1,083 |
| Expectancy / trade | $352 |
| Profit factor | 1.52 |
| Total return | +10.20% |
| Final equity | $110,202 (from $100,000) |
| **Max drawdown** | **-7.11%** |

## Exit reasons

| Reason | Count |
|---|---|
| END_OF_TEST | 5 |
| INVALIDATION | 15 |
| STOP | 2 |
| TAKE_PROFIT | 7 |

## Equity curve (marked-to-model, every ~10th point)

| Date | Marked equity |
|---|---|
| 2026-01-05 | $100,000 |
| 2026-01-15 | $99,516 |
| 2026-01-25 | $102,625 |
| 2026-02-04 | $104,676 |
| 2026-02-14 | $101,706 |
| 2026-02-24 | $104,763 |
| 2026-03-06 | $103,960 |
| 2026-03-16 | $105,941 |
| 2026-03-26 | $104,820 |
| 2026-04-05 | $107,491 |
| 2026-04-15 | $107,567 |
| 2026-04-25 | $112,126 |
| 2026-05-04 | $110,202 |

Full trade log: `backtest_trades.csv` (29 trades).