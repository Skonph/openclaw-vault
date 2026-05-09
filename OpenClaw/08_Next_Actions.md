
# 08_Next_Actions.md
**Updated:** May 8, 2026

---

## 🚨 IMMEDIATE — May 9 (9:30 PM Bangkok)

### Action 1 — PR Options Chain FIRST

URL: finance.yahoo.com/quote/PR/options Select: Jun 18, 2026 expiry Calls only Screenshot: $20C through $23C rows Check:

- IV $22C ≤40%? (was 40.72% May 8)
- DTE = 40 ✅
- OI unchanged (was 12,515)
- Price still ~$19-21
- Daily move <±3%

**If ALL pass → Execute immediately:**
```bash
ssh ubuntu@43.160.222.7
~/trading-bot/load_context.sh
# Run PR execution code from 03_Watchlist.md
```

### Action 2 — IBKR MultiSort Screener

Run "OpenClaw Bull Call" MultiSort screen Note any new tickers in $10-$30 with low IV rank Add promising ones to manual verification list

### Action 3 — CCL Check

Pull CCL Jun18 or Jun20 chain Check if IV has finally compressed below 50% Note: Jun12 expiry is illiquid — use Jun18/Jun20

## May 9-15 — Secondary Actions

### IBKR Tools to Set Up

□ Configure MultiSort screener "OpenClaw Bull Call" Factors: IV Rank (low), Price/EMA20 (high) Filters: $10-$30, NYSE/NASDAQ, Vol >1M

□ Configure MultiSort screener "OpenClaw Bear Put"  
Factors: IV Rank (low), Price/EMA20 (low) Same filters

□ Check Volatility Lab for PR and CCL IBKR → Research → Volatility Lab Confirms IV rank history before entry

□ Check Options Analytics for PR IBKR → Trade → Options Analytics Verify delta, theta on $21/$22 spread

### VALE Recheck — May 15

Pull VALE Jun18 options chain Need: OI ≥500 at $16C/$17C Current: OI was 5 on May 1

### CCL Post-May29 Expiry Watch
After May 29 options expire:

- Jun20 OI expected to build significantly
- Check CCL Jun20 chain May 30+
- Target: IV ≤43%, OI ≥500

## IBKR Tool Utilization Gaps to Close 
### Currently Underutilized 
| Tool               | Action Needed                    |
| ------------------ | -------------------------------- |
| MultiSort Screener | Set up Bull/Bear screens         |
| Volatility Lab     | Use before every entry           |
| Options Analytics  | Verify Greeks on candidates      |
| Why Is It Moving   | Check catalyst before entry      |
| Barchart IV Rank   | Cross-reference screener results |

### Full Daily Workflow (Updated)

9:00 PM Bangkok — Pre-scan prep: □ IBKR Market Overview — S&P direction, VIX □ IBKR MultiSort — run Bull or Bear screen □ Barchart IV Rank — cross-reference candidates □ Note crude oil price (oilprice.com)

9:30 PM Bangkok — Live data: □ Yahoo Finance — options chain screenshots □ IBKR Events Calendar — verify candidates □ IBKR Volatility Lab — confirm IV rank □ IBKR Why Is It Moving — catalyst check

Post-scan: □ Send verified candidates to Nova for scoring □ Update this file with findings □ Sync vault to server