#!/usr/bin/env python3
"""
OpenClaw Conviction Scorer v1.0

Two modes (auto-detected):
  1. Anthropic API (Claude Haiku) — if ANTHROPIC_API_KEY is set in .env
  2. Rule-based offline scorer — deterministic, fast, no external call

Both modes return the same dict:
  {
    'symbol':  str,
    'score':   int,         # 0–100
    'pass':    bool,        # True if score >= CONVICTION_MIN (75)
    'mode':    str,         # 'api' or 'offline'
    'factors': {...},       # scoring breakdown
    'reasoning': str,       # one-line summary
  }

Offline scoring rubric (max 100):
  Liquidity  (30 pts): OI per leg, bid-ask spread width
  Volatility (25 pts): IV Rank, IV Last
  Structure  (25 pts): R:R ratio, DTE sweet-spot
  Market     (20 pts): ETF above/below EMA20, VIX level

Usage (standalone test):
  python3 conviction_scorer.py
"""

import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv('/home/ubuntu/trading-bot/.env')

CONVICTION_MIN = 75   # must match scanner constant
VIX_LOW        = 15   # VIX < this = strong market
VIX_MID        = 18   # VIX 15–18 = acceptable


# ─── Offline rule-based scorer ────────────────────────────────────────────────

def _score_offline(alert: dict, macro: dict) -> dict:
    """
    Score a spread alert using quantitative rules only.
    alert keys expected (same as scanner output):
      spread_type, long_iv, short_iv, long_oi, short_oi,
      long_bid, long_ask, short_bid, short_ask,
      spread_mid, max_profit, rr, dte, price,
      etf_above_ema20 (bool|None)
    macro keys expected: {'VIX': {'price': 14.5, ...}, ...}
    """
    score       = 50  # base
    factors     = {}
    spread_type = alert.get('spread_type', 'bull_call')
    is_credit   = spread_type == 'iron_condor'   # credit strategies score differently

    # ── Liquidity (max +30) ────────────────────────────────────────────────────
    long_oi  = alert.get('long_oi',  0)
    short_oi = alert.get('short_oi', 0)
    min_oi   = min(long_oi, short_oi)

    if min_oi >= 2000:
        liq_oi = 15
    elif min_oi >= 1000:
        liq_oi = 10
    elif min_oi >= 500:   # minimum allowed
        liq_oi = 5
    else:
        liq_oi = 0
    score += liq_oi
    factors['oi_score'] = liq_oi

    # Bid-ask per leg (lower = better fill probability)
    long_ba  = round(alert.get('long_ask',  0) - alert.get('long_bid',  0), 3)
    short_ba = round(alert.get('short_ask', 0) - alert.get('short_bid', 0), 3)
    max_ba   = max(long_ba, short_ba)

    if max_ba <= 0.04:
        liq_ba = 15
    elif max_ba <= 0.06:
        liq_ba = 10
    elif max_ba <= 0.08:
        liq_ba = 5
    else:
        liq_ba = 0
    score += liq_ba
    factors['bid_ask_score'] = liq_ba

    # ── Volatility (max +25) — direction depends on strategy ─────────────────
    iv_rank  = alert.get('iv_rank', 30)   # scanner may not populate; default mid
    long_iv  = alert.get('long_iv', 0)
    short_iv = alert.get('short_iv', 0)
    avg_iv   = (long_iv + short_iv) / 2 if long_iv and short_iv else long_iv

    if is_credit:
        # Iron Condor: higher IV rank = more premium to sell = better
        if iv_rank >= 40:   vol_rank = 15
        elif iv_rank >= 30: vol_rank = 10
        elif iv_rank >= 20: vol_rank = 5
        else:               vol_rank = 0   # IV too cheap to sell
    else:
        # Debit spread: lower IV rank = cheaper to buy = better
        if iv_rank <= 15:   vol_rank = 15
        elif iv_rank <= 25: vol_rank = 10
        elif iv_rank <= 35: vol_rank = 5
        else:               vol_rank = 0
    score += vol_rank
    factors['iv_rank_score'] = vol_rank

    if is_credit:
        # Iron Condor: higher avg IV = more premium collected (within L019 cap)
        if avg_iv >= 35:   vol_last = 10
        elif avg_iv >= 28: vol_last = 7
        elif avg_iv >= 22: vol_last = 3
        else:              vol_last = 0   # too cheap to sell
    else:
        # Debit spread: lower avg IV = cheaper to buy
        if avg_iv <= 30:   vol_last = 10
        elif avg_iv <= 38: vol_last = 7
        elif avg_iv <= 42: vol_last = 3
        else:              vol_last = 0   # L019 should have already blocked >45%
    score += vol_last
    factors['iv_last_score'] = vol_last

    # ── Structure (max +25) — R:R thresholds differ by strategy ──────────────
    rr  = alert.get('rr', 0)
    dte = alert.get('dte', 0)

    if is_credit:
        # IC credit ratio: net_credit / max_loss — naturally lower (0.25–0.50 normal)
        if rr >= 0.50:   str_rr = 15
        elif rr >= 0.35: str_rr = 12
        elif rr >= 0.25: str_rr = 8
        elif rr >= 0.15: str_rr = 3
        else:            str_rr = 0
    else:
        # Debit spread: max_profit / debit paid (1.5–5+)
        if rr >= 4.0:   str_rr = 15
        elif rr >= 3.0: str_rr = 12
        elif rr >= 2.0: str_rr = 8
        elif rr >= 1.5: str_rr = 3
        else:           str_rr = 0
    score += str_rr
    factors['rr_score'] = str_rr

    if 28 <= dte <= 35:     # sweet spot (same for all strategies)
        str_dte = 10
    elif 25 <= dte < 28 or 35 < dte <= 40:
        str_dte = 5
    else:
        str_dte = 0
    score += str_dte
    factors['dte_score'] = str_dte

    # ── Market alignment (max +20) ────────────────────────────────────────────
    etf_above_ema = alert.get('etf_above_ema20')   # True/False/None

    if is_credit:
        # Iron Condor is direction-neutral — ETF direction not a factor
        mkt_etf = 5   # neutral partial credit always
    elif etf_above_ema is True and spread_type == 'bull_call':
        mkt_etf = 10   # bullish ETF + bull call = aligned
    elif etf_above_ema is False and spread_type == 'bear_put':
        mkt_etf = 10   # bearish ETF + bear put = aligned
    elif etf_above_ema is None:
        mkt_etf = 3    # uncertain — partial credit
    else:
        mkt_etf = 0    # misaligned
    score += mkt_etf
    factors['etf_alignment_score'] = mkt_etf

    vix_price = None
    try:
        vix_price = float((macro.get('VIX') or {}).get('price') or 0) or None
    except Exception:
        pass

    if vix_price is not None:
        if is_credit:
            # Iron Condor: higher VIX = more premium to sell = better
            if vix_price >= 20:   mkt_vix = 10
            elif vix_price >= 18: mkt_vix = 7
            elif vix_price >= 15: mkt_vix = 3
            else:                 mkt_vix = 0   # VIX < 15 = too cheap to sell
        else:
            # Debit spread: lower VIX = cheaper premiums = better
            if vix_price < VIX_LOW:    mkt_vix = 10
            elif vix_price < VIX_MID:  mkt_vix = 5
            else:                      mkt_vix = 0
    else:
        mkt_vix = 3   # unknown VIX — partial credit
    score += mkt_vix
    factors['vix_score'] = mkt_vix

    # ── Cap at 100 ────────────────────────────────────────────────────────────
    score = min(score, 100)

    # ── One-line reasoning ────────────────────────────────────────────────────
    top = sorted(factors.items(), key=lambda x: -x[1])[:2]
    bottom = [k for k, v in factors.items() if v == 0]
    reasoning_parts = [f"OI {min_oi:,}", f"R:R {rr}:1", f"DTE {dte}d", f"avg IV {avg_iv:.0f}%"]
    if etf_above_ema is not None:
        reasoning_parts.append('ETF aligned' if mkt_etf == 10 else 'ETF misaligned')
    if vix_price:
        reasoning_parts.append(f"VIX {vix_price:.1f}")
    reasoning = ' | '.join(reasoning_parts)

    passed = score >= CONVICTION_MIN
    return {
        'symbol':    alert.get('symbol', '?'),
        'score':     score,
        'pass':      passed,
        'mode':      'offline',
        'factors':   factors,
        'reasoning': reasoning,
    }


# ─── Anthropic API scorer (upgrade path) ─────────────────────────────────────

def _score_anthropic(alert: dict, macro: dict) -> dict | None:
    """
    Call Claude Haiku for conviction scoring.
    Returns score dict, or None if API call fails (caller falls back to offline).
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        return None

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        spread_type = alert.get('spread_type', 'bull_call')
        type_label  = 'Bull Call' if spread_type == 'bull_call' else 'Bear Put'
        vix  = (macro.get('VIX') or {}).get('price', 'N/A')
        spy  = (macro.get('SPY') or {}).get('change_pct', 'N/A')

        prompt = f"""You are OpenClaw conviction scorer. Score this options spread 0-100.
Respond with ONLY a JSON object. No explanation outside JSON.

SPREAD:
- Symbol: {alert['symbol']} ({type_label})
- Strikes: ${alert['long_strike']} / ${alert['short_strike']}
- Expiry: {alert.get('expiry', 'N/A')} ({alert['dte']} DTE)
- Net Debit: ${alert['spread_mid']} | Max Profit: ${alert['max_profit']} | R:R: {alert['rr']}:1
- IV (long/short): {alert['long_iv']}% / {alert['short_iv']}%
- OI (long/short): {alert['long_oi']:,} / {alert['short_oi']:,}
- Stock Price: ${alert.get('price', 'N/A')}
- ETF above EMA20: {alert.get('etf_above_ema20', 'unknown')}

MACRO:
- VIX: {vix}
- SPY change: {spy}%

RULES ALREADY PASSED:
- IV Rank ≤40%, IV Last ≤45%, OI ≥500 both legs, DTE 25-40, bid-ask ≤$0.10/leg
- Events calendar clear ±14 days, no known holds

SCORING CRITERIA:
- Liquidity quality (OI depth, tight bid-ask)
- Volatility setup (IV rank relative cheapness, IV last absolute level)
- Spread structure (R:R, DTE sweet spot 28-35)
- Market alignment (ETF direction, VIX level, broad market)

Respond ONLY with this JSON:
{{"score": 82, "pass": true, "reasoning": "one concise sentence", "factors": {{"liquidity": 25, "volatility": 20, "structure": 22, "market": 15}}}}"""

        message = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=200,
            messages=[{'role': 'user', 'content': prompt}],
        )
        import json
        raw_text = message.content[0].text.strip()
        # Strip markdown fences if present
        if raw_text.startswith('```'):
            raw_text = raw_text.split('```')[1].lstrip('json').strip()
        result = json.loads(raw_text)
        return {
            'symbol':    alert.get('symbol', '?'),
            'score':     int(result.get('score', 0)),
            'pass':      bool(result.get('pass', False)),
            'mode':      'api',
            'factors':   result.get('factors', {}),
            'reasoning': result.get('reasoning', ''),
        }
    except Exception as e:
        print(f'  ⚠️  Anthropic API scorer error: {e} — falling back to offline')
        return None


def _score_tokenhub(alert: dict, macro: dict) -> dict | None:
    """
    Call Tencent Cloud TokenHub (OpenAI compatible) for conviction scoring.
    """
    api_key = os.environ.get('TOKENHUB_API_KEY', '')
    if not api_key:
        return None

    model = os.environ.get('TOKENHUB_MODEL', 'deepseek-v4-flash')
    api_url = os.environ.get('TOKENHUB_API_URL', 'https://tokenhub-intl.tencentcloudmaas.com/v1/chat/completions')

    try:
        import urllib.request
        import json

        spread_type = alert.get('spread_type', 'bull_call')
        type_label  = 'Bull Call' if spread_type == 'bull_call' else 'Bear Put'
        vix  = (macro.get('VIX') or {}).get('price', 'N/A')
        spy  = (macro.get('SPY') or {}).get('change_pct', 'N/A')

        prompt = f"""You are the OpenClaw conviction scorer. Score this options spread 0-100.
Respond with ONLY a JSON object. No explanation outside JSON.

SPREAD:
- Symbol: {alert['symbol']} ({type_label})
- Strikes: ${alert['long_strike']} / ${alert['short_strike']}
- Expiry: {alert.get('expiry', 'N/A')} ({alert['dte']} DTE)
- Net Debit/Credit: ${alert['spread_mid']} | Max Profit: ${alert['max_profit']} | R:R: {alert['rr']}:1
- IV (long/short): {alert['long_iv']}% / {alert['short_iv']}%
- Stock Price: ${alert.get('price', 'N/A')}
- ETF above EMA20: {alert.get('etf_above_ema20', 'unknown')}

MACRO:
- VIX: {vix}
- SPY change: {spy}%

RULES ALREADY PASSED:
- IV Rank ≤40%, IV Last ≤45%, OI ≥500 both legs, DTE 25-40, bid-ask ≤$0.10/leg
- Events calendar clear ±14 days, no known holds

SCORING CRITERIA:
Evaluate the following four standard factors (0-25 points each, total 100):
1. credit_floor: Premium collected relative to spread width / floor
2. delta: Safety margin of short strikes (conservative delta)
3. dte: Proximity to DTE sweet-spot (28-35 days)
4. macro_alignment: Volatility regime (VIX) and trend (EMA20) alignment

Respond ONLY with this JSON:
{{"score": 85, "pass": true, "reasoning": "one concise sentence", "factors": {{"credit_floor": 22, "delta": 23, "dte": 20, "macro_alignment": 20}}}}"""

        body = json.dumps({
            "model": model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 200,
            "temperature": 0.1
        }).encode()

        req = urllib.request.Request(
            api_url,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
        )

        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read().decode())

        raw_text = resp["choices"][0]["message"]["content"].strip()
        if raw_text.startswith('```'):
            raw_text = raw_text.split('```')[1].lstrip('json').strip()

        result = json.loads(raw_text)
        return {
            'symbol':    alert.get('symbol', '?'),
            'score':     int(result.get('score', 0)),
            'pass':      bool(result.get('pass', False)),
            'mode':      'api_tokenhub',
            'factors':   result.get('factors', {}),
            'reasoning': result.get('reasoning', ''),
        }
    except Exception as e:
        print(f'  ⚠️  TokenHub API scorer error: {e} — falling back')
        return None


# ─── Public API ───────────────────────────────────────────────────────────────

def score_conviction(alert: dict, macro: dict) -> dict:
    """
    Score a spread alert.
    Tries TokenHub first if TOKENHUB_API_KEY is present; falls back to Anthropic API; falls back to offline.

    alert: one qualifying spread dict from the scanner
    macro: macro dict from the scan snapshot

    Returns:
    {
      'symbol':    'NCLH',
      'score':     78,
      'pass':      True,
      'mode':      'offline' | 'api' | 'api_tokenhub',
      'factors':   {...},
      'reasoning': '...',
    }
    """
    if os.environ.get('TOKENHUB_API_KEY'):
        api_result = _score_tokenhub(alert, macro)
        if api_result is not None:
            return api_result

    if os.environ.get('ANTHROPIC_API_KEY'):
        api_result = _score_anthropic(alert, macro)
        if api_result is not None:
            return api_result

    return _score_offline(alert, macro)


def score_batch(alerts: list[dict], macro: dict) -> list[dict]:
    """Score a list of alerts, return list of result dicts."""
    results = []
    for alert in alerts:
        result = score_conviction(alert, macro)
        print(f"  🧠 {alert.get('symbol'):6s}  Score: {result['score']:3d}/100  "
              f"{'✅ PASS' if result['pass'] else '❌ FAIL'}  [{result['mode']}]  {result['reasoning']}")
        results.append(result)
    return results


# ─── Standalone test ──────────────────────────────────────────────────────────

if __name__ == '__main__':
    # Dummy alert for testing
    test_alert = {
        'symbol': 'NCLH',
        'spread_type': 'bull_call',
        'price': 17.36,
        'expiry': '2026-06-20',
        'dte': 27,
        'long_strike': 17.0,
        'short_strike': 19.0,
        'long_bid': 0.72, 'long_ask': 0.78,
        'short_bid': 0.27, 'short_ask': 0.31,
        'long_oi': 1250, 'short_oi': 820,
        'long_iv': 38.5, 'short_iv': 36.2,
        'spread_mid': 0.47,
        'max_profit': 1.53,
        'rr': 3.3,
        'iv_rank': 22,
        'etf_above_ema20': True,
    }
    test_macro = {'VIX': {'price': 14.2, 'change_pct': -1.1}}

    print(f'\n=== Conviction Scorer Test — {datetime.now().strftime("%Y-%m-%d %H:%M")} ===\n')
    result = score_conviction(test_alert, test_macro)
    print(f'Symbol:    {result["symbol"]}')
    print(f'Score:     {result["score"]} / 100  (threshold: {CONVICTION_MIN})')
    print(f'Pass:      {"✅ YES" if result["pass"] else "❌ NO"}')
    print(f'Mode:      {result["mode"]}')
    print(f'Reasoning: {result["reasoning"]}')
    print(f'Factors:   {result["factors"]}')
    print()
