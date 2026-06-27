# Tradier Bot (`~/trading-bot/`)

## Purpose
Primary credit-spread scanner on Tradier paper ($100K sandbox). Enters Bull Put spreads at Monday–Friday on backtest-proven ETFs. ~$320 max risk per trade, max 2 concurrent positions.

## File Map
| File | Purpose |
|------|---------|
| `daily_scan.py` | Core engine: market scan → VIX regime → strategy → spread construction → auto-execute |
| `position_monitor.py` | Exit manager: 50% profit target, 2× stop loss, ≤2 DTE forced close |
| `telegram_bot.py` | Always-on systemd service — command dispatcher (/scan, /positions, /history) |
| `daily_summary.py` | 8:00 AM ICT daily Telegram report |
| `run_scan.sh` | Cron entry point wrapper |
| `active_trades.json` | Current open positions (max 2) |
| `trade_log.jsonl` | All entries/exits with full metadata |
| `pending_trade.json` | Constructed trade awaiting execution |
| `.env` | Tradier tokens, API keys (sandbox + prod) |

## Scan Algorithm (`daily_scan.py`)

### Strategy Selection (priority order)
1. **VIX > 30** → Cash (extreme volatility)
2. **VIX < 12** → Skip (IV too cheap)
3. **VIX 12–15** → QQQ scan only (fallback low-volatility mode)
4. **Active positions ≥ 2** → Skip
5. **FOMC/CPI within 2 days** → Skip
6. **SPY > +0.5%** → Bull Put Spread ✅ (primary)
7. **SPY < −0.5%** → Bull Put Spread (Bear Call disabled — backtest net loser)
8. **Flat + VIX ≥ 18** → Iron Condor
9. **Default** → Bull Put Spread

### Spread Parameters
| Parameter | Current Value | Notes |
|-----------|--------------|-------|
| Delta | 0.10–0.35 | OTM strike selection |
| DTE | 10–28 | Was 21, widened to 28 Jun 2026 |
| Spread width | $1–$10 | Based on total max loss limit |
| Credit floor | max($0.30, **25%** of width) | Raised from 15% Jun 26 |
| Max loss | $200 | Per trade |
| Max positions | 2 | Global across all systems |
| ETF universe | SPY, QQQ, XLF, XLI, XLY, TLT, DIA | Removed IWM Jun 26 |

### Scan Windows
- **Morning scan**: 10:15 AM ET (21:15 ICT) — Mon–Fri
- **Midday scan**: 1:15 PM ET (00:15 ICT) — Tue–Sat

### Strategy Changes (Jun 26, 2026)
| Change | Before | After | Reason |
|--------|--------|-------|--------|
| Bear Call | Active | **Disabled** | Net loser across all backtest thresholds (-$58 to -$221) |
| IWM | In universe | **Removed** | Negative P&L in every backtest scenario |
| Credit floor | 15% of width | **25% of width** | Best risk-adjusted return (77% WR, $239 max DD) |
| Scan days | Tue–Thu | **Mon–Fri** | Need volume after removing IWM/Bear Call |
| New symbols | — | XLF, XLI, XLY, TLT, DIA | Backtest-proven winners (XLF 76–86%, XLI 100%, XLY 100%) |

## Position Monitor (`position_monitor.py`)
Runs 3× daily via cron (21:30, 00:00, 02:30 ICT):
- **Profit target**: Close at 50% of max credit
- **Stop loss**: Close at 2× credit received
- **Time stop**: Close at ≤2 DTE of expiry
- **Profit check**: At 50% mark, book immediately (no trailing)

## Execution API
Tradier brokerage (paper sandbox). Order type: `credit` for credit spreads (not `limit`). Multileg option orders via `multileg_order`.

## Known Pitfalls
- **Null options response**: Tradier sandbox frequently returns `{"options": null}` for monthly expirations until ~1 week before. Workaround: multi-expiry fallback loop.
- **Holiday expirations**: US market holidays (Juneteenth, Jul 4, etc.) return null — not a code bug.
- **Plain-text 400 responses**: Sandbox occasionally returns plain-text errors instead of JSON. Fixed: check content-type before `.json()`.
- **Stale `pending_trade.json`**: Can cause auto-execute to fail. Delete manually if stuck.
