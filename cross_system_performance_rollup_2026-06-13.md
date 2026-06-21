# Cross-System Performance Rollup — 2026-06-13

Combined view of the Tradier bot (`Tradier/`) and OpenClaw (`OpenClaw/`), both running on Alpaca/Tradier paper accounts. Source data: `Tradier/trade_log.jsonl`, `Tradier/active_trades.json`, `OpenClaw/04_Trade_Journal.md`, `OpenClaw/pending_orders.json`, `OpenClaw/OPENCLAW_CONTEXT.md`.

---

## 1. Headline Numbers

| Metric | Tradier | OpenClaw | Combined |
|---|---|---|---|
| Starting capital | $2,000 | $3,000 | $5,000 |
| Current equity | $2,000 | $2,898.17 (paper, as of 2026-06-13) | $4,898.17 |
| Total P&L | $0.00 | -$101.83 (-3.4%) | -$101.83 (-2.0%) |
| Closed trades | 0 | 4 | 4 |
| Win rate (closed) | N/A — no real fills yet | 25% (1/4) | 25% (1/4) |
| Open positions | 0 | 1 (TOST Iron Condor, open, unrealized -$44) | 1 |

**Headline takeaway:** the combined drag is entirely on the OpenClaw side (-$101.83), driven by 3 losing debit-spread trades (AAL, IAG, HMC) against 1 reported winner (F); the figure already incorporates the -$44 unrealized loss on the still-open TOST Iron Condor, since `equity` is mark-to-market. Tradier has not yet produced a real fill — every entry in `trade_log.jsonl` is either a simulated scan (`order_status: "simulated"`, `order_id: "TEST-AUTO-001"`) or a `position_monitor.py --test` diagnostic exit (`order_id: "AAA"/"BBB"/"CCC"/"TEST001"`), and `active_trades.json` is currently `[]`. So Tradier's $2,000 is unchanged since inception.

---

## 2. Tradier Detail

No real trades to break down yet. For reference, the *simulated* scan in `trade_log.jsonl` has repeatedly proposed the same Bull Put Spread (SPY 732/730, exp 2026-06-19, $0.37 credit) across several days — this reflects the scanner re-running in test mode, not multiple real entries.

| Item | Value |
|---|---|
| Real fills | 0 |
| Simulated/test log entries | 11 (all SPY, mix of Bull Put / Bear Call / Iron Condor) |
| Current open positions | 0 |

---

## 3. OpenClaw Detail

### By trade

| Trade | Symbol | Type | Entry | Exit | P&L (paper) | P&L (real est.) | Rule compliance |
|---|---|---|---|---|---|---|---|
| 1 | AAL | Debit spread $12/$13 | $0.37 | $0.14 | -$23 (-62%) | — | ❌ IV >45%, conviction <68 |
| 2 | F | Debit spread $12.50/$14 | $0.42 | $0.10 | -$32 (-76%) | — | ✅ |
| 3 | IAG | Debit spread $22/$24 | $0.60 | ~$0.20 | -$115 | ~-$50 | ✅ |
| 4 | HMC | Debit spread $27.5/$30 | $0.40 | $0.10 | -$45 | -$30 | ✅ |
| 5 | TOST | Iron Condor | filled at -$0.46 net credit (2026-06-08) | — (OPEN, exp 2026-07-17) | -$44 unrealized | -$44 unrealized | n/a (used as live-fire mleg test) |

### By symbol

All five OpenClaw trades are on different underlyings (AAL, F, IAG, HMC, TOST) — no repeat exposure.

### By strategy

| Strategy | Count | Paper P&L |
|---|---|---|
| Debit spreads (bull/bear call or put) | 4 (closed) | -$215 |
| Iron Condor | 1 (open) | -$44 unrealized |

### Win rate

The journal's running summary states "Winners: 1 (F accidental — see L017)", but Trade 2 (F) is shown with -$32 (-76%) P&L — i.e. a loss. I can't reconcile this from the journal text alone; flagging as a data-quality issue rather than guessing. Taking the journal's own stated win-rate figure (1/4 = 25%) at face value for the headline table above.

---

## 4. Data-Quality Caveats (read before acting on this report)

1. **Tradier has zero real trades.** All P&L above for Tradier is $0 by definition — there's no track record yet to evaluate. The backtest (`Tradier/backtest_results.json`, Improvement #4) is the only performance signal available for this system (73% WR / +20.3% over ~2 years of historical replay).
2. ~~**TOST Iron Condor outcome is unresolved.**~~ **Resolved (2026-06-13).** It was executed 2026-06-08 (filled at -$0.46 net credit, i.e. $46 received) as a live-fire test for the mleg pricing fix (Improvement #3). A read-only check (`check_tost_status.py` against Alpaca) confirms all 4 legs are still **OPEN** as of 2026-06-13, with a combined unrealized P&L of **-$44** (27C -$11, 29C -$19, 20P -$13, 21P -$1) and no closing order on record. Since Alpaca's `equity` field is mark-to-market, this -$44 is already folded into the $2,898.17 equity figure — the -$101.83 combined P&L figure is current and does not need adjustment. `04_Trade_Journal.md` (Trade 5) and `OPENCLAW_CONTEXT.md` §10 have been updated to reflect this open position.
3. **"Real est. P&L" vs "paper P&L" diverge significantly** for IAG (-$50 real vs -$115 paper) and HMC (-$30 real vs -$45 paper) due to documented Alpaca paper-pricing distortion (L013). The -$101.83 combined figure uses paper equity ($2,898.17) since that's the only consistently-tracked number; the *real* P&L is likely smaller in magnitude (closer to -$80 to -$90 based on the per-trade real estimates).
4. **F's "winner" classification is unverified** (see §3) — if it's actually a 4th loss, OpenClaw's win rate is 0% (0/4), not 25%.

---

## 5. Cross-System Risk Exposure Check

| System | Per-trade max risk (current rules) | Max concurrent positions | Theoretical max combined open risk |
|---|---|---|---|
| Tradier | $100 (tier 1–2) up to $150 (tier 3, post-Improvement #5) | 2 | $300 (both at tier-3) |
| OpenClaw | $200–$500 (tier 1) up to $1,000 (tier 3, post-Improvement #5) | 1 (per `02_Ruleset_v4.md`) | $1,000 |
| **Combined** | | | **$1,300** (worst case, both systems simultaneously at max tier) |

$1,300 against combined equity of $4,898.17 is **~26.5%** — a theoretical ceiling, not a typical exposure (tier-3 requires VIX>20 plus exceptional credit/conviction on both systems at once, which is unlikely to coincide).

**Finding worth a look:** `OpenClaw/02_Ruleset_v4.md` documents "Max Risk: $60/trade", but `vault_updater._calc_qty` (both before and after Improvement #5) computes a risk amount of $200–$500 at baseline (tier 1), 3.3x–8.3x the documented limit. This predates today's change — Improvement #5 widens the gap further at tiers 2–3 ($300–$1,000). This looks like a stale ruleset doc rather than a live risk-control gap (the actual $200 floor has been in place since the "v4 changes" noted at the top of `vault_updater.py`), but worth confirming which number is the intended policy and updating whichever side is wrong.

---

## 6. Suggested Next Steps

- Monitor the open TOST Iron Condor (unrealized -$44, exp 2026-07-17) into expiry or per `02_Ruleset_v4.md` exit rules if a stop condition is hit first.
- Reconcile the F trade win/loss classification (caveat #4) in the journal.
- Update `02_Ruleset_v4.md`'s "$60/trade" max-risk line to match `vault_updater.py`'s actual $200–$1,000 tiered amounts, or vice versa, so the two docs agree (caveat in §5).
- Once Tradier produces its first real fill, re-run this rollup to get a true side-by-side comparison.
