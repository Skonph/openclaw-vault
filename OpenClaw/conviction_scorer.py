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
from pathlib import Path
from dotenv import load_dotenv

# Try multiple possible .env paths for development flexibility
for env_path in ['/home/ubuntu/openclaw/.env', str(Path(__file__).parent / '.env'), str(Path(__file__).parent / '.env.local')]:
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

CONVICTION_MIN = 75   # must match scanner constant
VIX_LOW        = 15   # VIX < this = strong market
VIX_MID        = 18   # VIX 15–18 = acceptable


# ─── Offline rule-based scorer ────────────────────────────────────────────────

def _score_offline(alert: dict, macro: dict) -> dict:
    """
    Score a spread alert using standardized quantitative rules.
    Factors (max 25 pts each, total 100):
      - credit_floor: Premium collected relative to spread width / floor
      - delta: Safety margin of short strikes (conservative delta)
      - dte: Proximity to DTE sweet-spot (28-35 days)
      - macro_alignment: Volatility regime (VIX) and trend (EMA20) alignment
    """
    factors = {
        'credit_floor': 0,
        'delta': 0,
        'dte': 0,
        'macro_alignment': 0
    }

    spread_type = alert.get('spread_type', 'bull_call')
    is_credit   = spread_type == 'iron_condor' or 'credit' in spread_type

    # 1. Credit Floor (max 25 pts)
    # Credit/width ratio evaluation
    ratio = 0.0
    try:
        long_strike = float(alert.get('long_strike', 0))
        short_strike = float(alert.get('short_strike', 0))
        width = abs(short_strike - long_strike)
        mid = float(alert.get('spread_mid', 0.0))
        
        if width > 0:
            if is_credit:
                # Credit spread: want high credit relative to width (e.g. >= 30%)
                ratio = abs(mid) / width
                if ratio >= 0.35:     factors['credit_floor'] = 25
                elif ratio >= 0.25:   factors['credit_floor'] = 20
                elif ratio >= 0.15:   factors['credit_floor'] = 10
                else:                 factors['credit_floor'] = 0
            else:
                # Debit spread: want low cost relative to width (e.g. <= 40%)
                ratio = mid / width
                if ratio <= 0.35:     factors['credit_floor'] = 25
                elif ratio <= 0.45:   factors['credit_floor'] = 20
                elif ratio <= 0.50:   factors['credit_floor'] = 10
                else:                 factors['credit_floor'] = 0
    except Exception:
        factors['credit_floor'] = 15  # default

    # 2. Delta (max 25 pts)
    # Target short delta (e.g. <= 0.15 is safest, up to 0.35 is acceptable)
    short_delta = alert.get('short_delta')
    if short_delta is not None:
        try:
            d = abs(float(short_delta))
            if d <= 0.15:      factors['delta'] = 25
            elif d <= 0.25:    factors['delta'] = 20
            elif d <= 0.35:    factors['delta'] = 10
            else:              factors['delta'] = 0
        except Exception:
            factors['delta'] = 15
    else:
        factors['delta'] = 15  # default/neutral when delta not present

    # 3. DTE Sweet-Spot (max 25 pts)
    # 28-35 DTE is optimal (max points), 25-40 DTE is acceptable
    try:
        dte = int(alert.get('dte', 0))
        if 28 <= dte <= 35:
            factors['dte'] = 25
        elif 25 <= dte <= 40:
            factors['dte'] = 15
        else:
            factors['dte'] = 5
    except Exception:
        dte = 0
        factors['dte'] = 15

    # 4. Macro Alignment (max 25 pts)
    # VIX level and ETF trend alignment
    try:
        etf_above_ema = alert.get('etf_above_ema20')  # True/False/None
        align_score = 0
        if is_credit:
            # Iron Condors are neutral, direction doesn't matter
            align_score += 7
        elif etf_above_ema is True and spread_type == 'bull_call':
            align_score += 12  # bullish trend matches strategy
        elif etf_above_ema is False and spread_type == 'bear_put':
            align_score += 12  # bearish trend matches strategy
        elif etf_above_ema is None:
            align_score += 5   # neutral/unknown
        
        # Volatility alignment (VIX)
        vix_price = float((macro.get('VIX') or {}).get('price') or 0)
        if vix_price > 0:
            if is_credit:
                # Credit spread: want higher VIX (more premium) but not extreme
                if 18 <= vix_price <= 30:  align_score += 13
                elif 15 <= vix_price < 18: align_score += 8
                else:                      align_score += 0
            else:
                # Debit spread: want lower VIX (cheaper premiums)
                if vix_price < 15:         align_score += 13
                elif vix_price <= 20:      align_score += 8
                else:                      align_score += 0
        else:
            align_score += 5  # default
        
        factors['macro_alignment'] = min(25, align_score)
    except Exception:
        factors['macro_alignment'] = 15

    score = sum(factors.values())
    passed = score >= CONVICTION_MIN
    reasoning = f"Credit Ratio: {ratio:.1%} | Delta: {short_delta or 'N/A'} | DTE: {dte}d | Macro Score: {factors['macro_alignment']}/25"

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
            "https://tokenhub.tencentmaas.com/v1/chat/completions",
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
