# Forecast Signal Pipeline — Session Handoff

**Date:** 2026-06-26
**Status:** Items 1–4 built, validated live, deployed. Quota-hardened. Ready to consume.
**For:** Antigravity agent, Hermes (DeepSeek strategist) — pick up from here.

---

## TL;DR for the next agent

A new forecast-feature layer is live. It writes one merged signal block per
candidate to:

```
/home/ubuntu/openclaw-vault/signals/forecast_signals.json
```

Read that file. Each ticker has two axes already fused — current vol/positioning
(Tradier) and forward-looking event/revision signals (Finnhub) — plus a `notes`
list with plain-language flags AND cross-signal observations. **Consume the
`notes` and the structured fields directly when scoring conviction.** Do not
re-fetch this data; it's already computed nightly.

---

## What was built (4 modules, all on the server in ~/openclaw/)

| Module | Role | Backend | Cost |
|---|---|---|---|
| `openclaw_tradier_vol.py` | Items 1–3: IV regime, term structure, skew, OI | Tradier (`smv_vol`, `greeks=true`) | free (existing token) |
| `openclaw_forward.py` | Item 4: earnings risk, analyst drift, PEAD streak, (opt) news | Finnhub + optional Alpha Vantage | free |
| `openclaw_signals.py` | Merge layer + cross-signal flags + candidates reader | — | — |
| `openclaw_features.py` | IBKR-MCP prototype for items 1–3 (NOT used on server) | IBKR MCP | reference only |

Source of truth: committed to `openclaw-vault` repo (pushes to GitHub working).

---

## The signal block schema (per ticker in forecast_signals.json)

```
VOL / POSITIONING (Tradier, items 1–3):
  spot                 current price
  iv_annual            ATM IV %  (from smv_vol)
  hv_annual            30d realized vol % (computed from /markets/history)
  iv_hv_ratio          <0.85 = options cheap vs realized | >1.2 = rich
  term_front/back_iv   ATM IV at front vs back monthly expiry
  term_slope, term_state   contango / backwardation / flat
  atm_iv, skew_25d, skew_state   steep_put / normal / call_skew
  pcr_oi               put/call open-interest ratio
  iv_percentile_13w    IV-Rank (0..1) — ACCUMULATING, see note below
  iv_rank_ok           true if IV-Rank <= 0.40 (only once accumulated)
  tradeable_dte_found  is there an expiry in 25–50 DTE?

FORWARD (Finnhub + opt AV, item 4):
  next_earnings_date, days_to_earnings
  earnings_ban_active  TRUE = within +/-14d of earnings (ruleset v4.0 block)
  last_eps_surprise_pct, eps_beat_streak, eps_surprise_direction   PEAD context
  rec_strong_buy/buy/hold/sell/strong_sell   analyst rating distribution
  rec_net_score        weighted consensus, -1..+1
  rec_trend_delta, rec_trend_state   improving / deteriorating / stable (3mo)
  news_sentiment_mean, news_sentiment_state   ONLY if AV enabled (off by default)

notes[]   plain-language flags + cross-signal observations (see below)
```

## Cross-signal flags (the payoff — only the merge layer produces these)

These appear in `notes[]` and combine both axes. Weight them heavily:
- "cheap IV + improving analyst consensus — favourable asymmetry"
- "put skew corroborated by deteriorating consensus — downside conviction"
- "vol/IV-rank look tradeable BUT earnings ban active — skip per v4.0"
- "unusual option flow aligns with a fundamental catalyst — flow likely informed"
- "term backwardation explained by earnings inside front expiry"

---

## How to run it (already cron-scheduled)

Nightly at 21:25 Bangkok, after the scanner (21:05):
```
cd /home/ubuntu/openclaw && set -a && . ./.env && . ./.env.openclaw && set +a && \
  python3 openclaw_signals.py --candidates /home/ubuntu/openclaw/candidates.txt \
  --out /home/ubuntu/openclaw-vault/signals/forecast_signals.json
```
- Reads candidates via the scanner's OWN `load_candidates()` (same cooling-off filter)
- Loads BOTH env files (`.env` = Tradier; `.env.openclaw` = Finnhub/AV) — critical
- Writes atomically (temp + rename) so vault_updater never reads a partial file

---

## Important operational notes / gotchas

1. **Alpha Vantage news is OFF by default.** AV free tier = 25 req/day, too tight
   for a 9-ticker nightly run. Finnhub carries earnings + ratings for ALL tickers
   with no quota. To enable news sentiment for a manual run (≤12 names, early in
   the day, under quota): `OPENCLAW_ENABLE_AV=1 python3 openclaw_signals.py ...`

2. **IV-Rank is accumulating.** Tradier doesn't serve IV percentile, so the
   pipeline logs ATM IV nightly to `/home/ubuntu/openclaw/logs/iv_history.json`.
   `iv_percentile_13w` / `iv_rank_ok` stay null until ~40 samples (~early Sept 2026).
   Until then, USE `iv_hv_ratio` as the "is vol cheap" signal — it's available now.

3. **The scanner's `IV_RANK_MAX=40` is currently NOT enforced** in scanner logic
   (it gates on `IV_LAST_MAX=45` raw smv_vol). The new pipeline will eventually
   provide a real IV-Rank that could wire into conviction_scorer.py — currently
   `conviction_scorer.py` references `iv_rank` only in a test fixture (line 397),
   not in scoring. Decision pending: make this the canonical IV-Rank source.

4. **Both env files must load.** A run with only `.env.openclaw` gives
   `has_vol_axis: false` (no Tradier token). The cron line loads both.

5. **EPS surprise outliers are flagged, not trusted.** e.g. Ford's last surprise
   was +247% (a one-off). The note flags it; weight `eps_beat_streak` instead.

---

## Validation evidence (real live data, this session)

Ford (F) live run produced coherent numbers cross-checked vs IBKR:
  spot 14.11, ATM IV 39.1%, HV 65.6%, iv_hv_ratio 0.60 (options cheap),
  term contango +5.3, skew +0.7 (normal), pcr_oi 0.63, earnings 32d out.
9-candidate run (USFD ZION WBS ZWS HRL URBN ALLY PRMB SON): all got vol +
ratings + earnings; news limited to first 5 by AV quota (now off by default).

---

## Open threads (NOT done this session)

- **Port 22 lockdown** — server is under SSH brute-force; MaxStartups widened +
  fail2ban installed, but port 22 still open to 0.0.0.0. Recommend restricting to
  user IP in Tencent security group. (User reviewing before action.)
- **conviction_scorer.py integration** — the forecast block is written but the
  scorer does not yet READ forecast_signals.json. Wiring it in is the next
  high-value step: have score_conviction() ingest the merged block (esp. the
  cross-signal flags and earnings_ban_active) as scoring inputs.
- **candidates "passed-filter" tightening** — currently reads raw candidates.txt
  (with cooling-off). Could tighten to only scanner-passed spread candidates.

---

## Recommended next action for Hermes/DeepSeek

The single highest-value next step: **wire `forecast_signals.json` into
`conviction_scorer.py`** so the strategist's conviction score actually uses these
signals. Priority inputs: `earnings_ban_active` (hard veto), the cross-signal
`notes`, `iv_hv_ratio`, `rec_trend_state`, `skew_state`. The data is ready and
waiting — it just isn't consumed by scoring yet.
