# 03_Watchlist.md
**Updated:** May 20, 2026
**Ruleset:** v4.0

---

## ⏳ ON HOLD — Pending Recheck

### PR (Permian Resources) — KNOWN_HOLD UNTIL JUN 17
| Field | Value |
|-------|-------|
| Last Price | ~$19.91 (May 8 data — stale, recheck Jun 17) |
| Sector | Energy ✅ |
| Earnings | Q1 done May 7 ✅ Next Q2 far |
| IV last seen | 38.5–39.0% ✅ (May 20 scan — within limit) |
| OI $21C | 3,783 ✅ |
| OI $22C | 12,515 ✅ exceptional |
| Target Spread | $20/$21 or $21/$22 Jul18 (recheck at Jun 17) |
| Conviction | 73/100 ✅ (last scored) |
| Hold Reason 1 | Shareholders meeting May 19 ✅ PASSED |
| Hold Reason 2 | Dividend Jun 16 ❌ NOT YET PASSED — 27 days away |
| KNOWN_HOLD | Scanner auto-suppresses until 2026-06-17 |
| Status | ⏳ DO NOT ENTER — wait until Jun 17 recheck |

**JUN 17 ACTION:**
Pull PR Jul18 chain at 9:30 PM Bangkok
Confirm dividend Jun 16 cleared ✅
Check IV ≤40%, OI ≥500, DTE 25–40 → proceed to Events Calendar + Nova

⚠️ NOTE: Any briefing showing "Jun 16 passed" before Jun 17 is INCORRECT — scanner KNOWN_HOLD is active.

*Execution code will be generated manually in Cowork on Jun 17 after full approval chain.*

---

## Priority 1 — PRIMARY WATCH

### CCL (Carnival Corporation)
| Field | Value |
|-------|-------|
| Price | $26.84 (May 8, 2026) |
| Sector | Consumer Discretionary ✅ |
| Earnings | Next ~June ✅ |
| Current IV | 55-59% — NOT compressing despite crude drop |
| OI Jun12 | Very low (31-218) — illiquid expiry |
| OI Jul17 | 2,392-6,058 — too far DTE |
| Issue | Liquidity gap between May29 and Jul17 |
| Macro trigger | WTI $94.57 ✅ fired — but IV not responding |
| Revised thesis | Needs Iran resolution, not just crude drop |
| Next check | After May 29 expiry — Jun20 OI will build |
| Est. trigger | Late May / early June |

---

## Priority 2 — SECONDARY WATCH

### NCLH (Norwegian Cruise Line)
| Field | Value |
|-------|-------|
| Price | $17.36 (May 7, 2026) |
| Sector | Consumer Discretionary ✅ |
| Earnings | May 4 done ✅ — cautious guidance |
| Current IV | 58% — not compressing |
| OI | Below 500 at near strikes |
| Status | HOLD — same Iran/oil thesis as CCL |
| Est. trigger | Late May |

---

## Priority 3 — TERTIARY WATCH

### AAL (American Airlines)
| Field | Value |
|-------|-------|
| Price | $13.14 (May 7, 2026) |
| Sector | Industrials ✅ |
| Earnings | Q1 done April 23 ✅ |
| Conviction requirement | ≥72 (prior loss) |
| IBKR signal | +1.55% relative strength |
| Status | Monitor |

---

## Priority 4 — LOW PRIORITY

### VALE (Vale SA)
| Field | Value |
|-------|-------|
| Price | $16.25 (May 7, 2026) |
| Sector | Materials ✅ |
| Earnings | May 30 ✅ |
| OI issue | $16C OI = 5 on May 1 |
| Recheck | May 15, 2026 |

---

## IBKR Screener Candidates

### AM (Antero Midstream) — REJECTED
| Reason | Detail |
|--------|--------|
| Conviction | 62/100 — below 70 |
| Bearish signals | Williams %R, Momentum, MA cross |
| Event risk | Shareholders meeting Jun 3 |
| Recheck | When technicals improve |

### PR (Permian Resources) — SEE ELEVATED ALERT ABOVE

---

## IBKR MultiSort Screener — Bull Call Setup
*Run daily to find new candidates*

Factors to set (prefer LOW values):

1. IV Rank — LOW (want cheap options)
2. Price/EMA(20) — HIGH (uptrend)
3. 52W IV Percentile — LOW

Filters:

- Price $10-$40 (expanded May 22, 2026)
- Market Cap >$5B
- NYSE + NASDAQ only
- Avg Volume >1M

⚠️ Post-screener manual check (add "IV Last" as visible column):
- IV Last ≤45% — reject any result with IV Last >45% even if IV Rank passes
- L019: IV Rank alone insufficient — always verify absolute IV before proceeding

Save as: "OpenClaw Bull Call"

```
## IBKR MultiSort Screener — Bear Put Setup
*Run when market trending down*
```

Factors to set:

1. IV Rank — LOW
2. Price/EMA(20) — LOW (downtrend)
3. Price/EMA(50) — LOW

Same filters: Price $10-$40, Market Cap >$5B, NYSE+NASDAQ, Avg Vol >1M
Same IV Last ≤45% post-screener check applies
Save as: "OpenClaw Bear Put"

```

## Candidates Pipeline — After Each Cowork Session
*(Added May 22, 2026)*

After running IBKR MultiSort and reviewing candidates, update the server scanner:

```bash
ssh ubuntu@43.156.9.185 'cat > ~/trading-bot/candidates.txt << '"'"'EOF'"'"'
# OpenClaw Candidates
# Updated: YYYY-MM-DD
# [note active positions or none]

TICKER1
TICKER2
...
EOF
echo "✅ Done" && cat ~/trading-bot/candidates.txt'
```

Rules for candidates.txt:
- Include tickers that pass screener IV Rank + IV Last + price filter
- Exclude tickers in KNOWN_HOLDS (scanner suppresses them automatically)
- Exclude tickers with earnings within 14 days
- Max ~15 tickers — scanner runs all of them nightly

---

## Barchart IV Rank Screener (Free)
*Use alongside IBKR for IV rank verification*
```

URL: barchart.com/options/iv-rank-percentile Filter: IV Rank < 40 Filter: Price $10-$30 Use: Cross-reference IBKR screener results

```

---

## IBKR Watchlist Live Monitor
*Check daily 9:30 PM Bangkok*

| Ticker | Type | Today May 7 | Signal |
|--------|------|------------|--------|
| VIX | Index | 17.32 | ↓ Compressing |
| XLB | ETF | -1.03% | ↓ Materials weak |
| XLE | ETF | -2.00% | ↓ Energy selling |
| XLI | ETF | -1.11% | ↓ Industrials weak |
| XLY | ETF | -0.13% | → Flat |
| VALE | Stock | -1.46% | ↓ Weak |
| AAL | Stock | +1.55% | ✅ Strong |
| NCLH | Stock | -2.20% | ↓ Weak |
| CCL | Stock | +0.25% | ✅ Relative strength |

---

## Macro Triggers

| Signal | Target | Current | Status |
|--------|--------|---------|--------|
| WTI Crude | <$95 | **$94.57** | ✅ TRIGGERED |
| VIX | <15 | 17.32 | ❌ Compressing |
| CCL IV | ≤43% | 55-59% | ❌ Not responding |
| PR $22C IV | ≤40% | 40.72% | ⚠️ 0.72% away |

---

## Disqualified Tickers

| Ticker | Reason | Review |
|--------|--------|--------|
| CLF | IV 54%+, earnings May 22 | Jun 2026 |
| PSKY | New, unproven liquidity | Jul 2026 |
| LUV | Price $37.92 > $30 | Monitor |
| GOLD | Price $45.19 > $30 | Monitor |
| RIVN | Data unreliability | Never |
| PARA | Delisted | N/A |
| SOFI | Earnings miss, IV spike | Sep 2026 |
| HOOD | Earnings miss, IV spike | Sep 2026 |
| MARA | IV 87-102% | Monitor |
| NIO | Below $10 | Monitor |
| GRAB | Below $10 | Monitor |
| AM | Conviction 62, bearish signals | Monitor |
```