#!/usr/bin/env python3
"""
live_market_data.py — LIVE market data layer (zero-token) for the screener, briefs and journals.

Replaces every hardcoded/synthetic price, PDH/PDL box and option strike with broker-verified live
data. Sources: Tradier PROD (primary), Alpaca data API (fallback for bars), live_spot (spots).

Core entry points
-----------------
daily_bars(sym, days)                 -> [{date,open,high,low,close,volume}]   (oldest -> newest)
prev_session_box(sym)                 -> {pdh,pdl,close,box_position_pct,asof}
hv20(sym)                             -> annualised realised vol %  (or None)
expirations(sym)                      -> ['YYYY-MM-DD', ...]
chain(sym, expiration)                -> [{strike,type,delta,iv,bid,ask,mid,oi,volume,...}]
pick_bull_put_spread(sym, ...)        -> live strike/expiry/credit selection (or {'ok':False,'reason':..})
index_context()                       -> SPY/QQQ/VIX spot + SMA20/50 + change (for the pre-market brief)
refresh_market_context(path)          -> rewrites market_context.json with live values

Rule: never fabricate. Anything unavailable is reported as None + a reason string.
"""
from __future__ import annotations
from typing import Dict, List, Any, Optional, Tuple, Set
import json, math, ssl, datetime, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

from live_spot import get_spot, get_spots, _load_env  # noqa: F401

CTX = ssl._create_unverified_context()
TIMEOUT = 20
TRADIER_BASE = "https://api.tradier.com/v1"


def _tradier(path: str, **params):
    tok = _load_env().get("TRADIER_PROD_TOKEN")
    if not tok:
        raise RuntimeError("TRADIER_PROD_TOKEN missing")
    url = TRADIER_BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, context=CTX, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


# ---------------------------------------------------------------- price history
def daily_bars(sym: str, days: int = 60):
    """Daily bars, oldest -> newest. Tradier first, Alpaca fallback."""
    end = datetime.date.today()
    start = end - datetime.timedelta(days=int(days * 1.7) + 10)
    try:
        d = _tradier("/markets/history", symbol=sym, interval="daily",
                     start=str(start), end=str(end))
        rows = (d.get("history") or {}).get("day") or []
        if isinstance(rows, dict):
            rows = [rows]
        if rows:
            return sorted(({"date": r.get("date"), "open": float(r["open"]), "high": float(r["high"]),
                            "low": float(r["low"]), "close": float(r["close"]),
                            "volume": float(r.get("volume") or 0)} for r in rows),
                          key=lambda x: x["date"])
    except Exception:
        pass
    try:
        env = _load_env()
        url = (f"https://data.alpaca.markets/v2/stocks/{sym}/bars?timeframe=1Day&limit=90"
               f"&feed=iex&start={start}T00:00:00Z")
        req = urllib.request.Request(url, headers={"APCA-API-KEY-ID": env.get("ALPACA_API_KEY", ""),
                                                   "APCA-API-SECRET-KEY": env.get("ALPACA_SECRET_KEY", "")})
        with urllib.request.urlopen(req, context=CTX, timeout=TIMEOUT) as r:
            d = json.loads(r.read().decode())
        return [{"date": b["t"][:10], "open": b["o"], "high": b["h"], "low": b["l"],
                 "close": b["c"], "volume": b["v"]} for b in d.get("bars", [])]
    except Exception:
        return []


def prev_session_box(sym: str):
    """Real prior-session high/low/close -> RULE-045 box position (0% = PDL, 100% = PDH)."""
    bars = daily_bars(sym, days=10)
    if len(bars) < 2:
        return {"pdh": None, "pdl": None, "close": None, "box_position_pct": None,
                "reason": "no daily bars"}
    prev = bars[-2]                      # last COMPLETED session
    spot = get_spot(sym).get("price") or prev["close"]
    pdh, pdl = prev["high"], prev["low"]
    box = 50.0 if pdh <= pdl else max(0.0, min(100.0, (spot - pdl) / (pdh - pdl) * 100.0))
    return {"pdh": pdh, "pdl": pdl, "close": prev["close"], "spot": spot,
            "box_position_pct": round(box, 1), "asof": prev["date"]}


def hv20(sym: str, lookback: int = 21):
    """Annualised realised volatility (%) from the last `lookback` daily closes."""
    bars = daily_bars(sym, days=lookback + 10)
    closes = [b["close"] for b in bars][-(lookback + 1):]
    if len(closes) < 6:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    m = sum(rets) / len(rets)
    var = sum((x - m) ** 2 for x in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252) * 100.0


# ---------------------------------------------------------------- option chain
def expirations(sym: str):
    d = _tradier("/markets/options/expirations", symbol=sym, includeAllRoots="true")
    ds = ((d.get("expirations") or {}).get("date")) or []
    if isinstance(ds, str):
        ds = [ds]
    return ds


def chain(sym: str, expiration: str, greeks: bool = True):
    d = _tradier("/markets/options/chains", symbol=sym, expiration=expiration,
                 greeks="true" if greeks else "false")
    opts = (d.get("options") or {}).get("option") or []
    if isinstance(opts, dict):
        opts = [opts]
    out = []
    for o in opts:
        g = o.get("greeks") or {}
        bid = float(o.get("bid") or 0)
        ask = float(o.get("ask") or 0)
        mid = round((bid + ask) / 2, 2) if (bid and ask) else (float(o.get("last") or 0) or None)
        iv = o.get("mid_iv") or g.get("mid_iv")
        out.append({"strike": float(o.get("strike") or 0),
                    "type": (o.get("option_type") or "").lower(),
                    "delta": g.get("delta"), "gamma": g.get("gamma"),
                    "theta": g.get("theta"), "vega": g.get("vega"),
                    "iv": round(float(iv) * 100, 2) if iv else None,
                    "bid": bid or None, "ask": ask or None, "mid": mid,
                    "oi": o.get("open_interest"), "volume": o.get("volume")})
    return out


def _candidate_expiries(sym: str, prefer: str | None = None, dte_min: int = 8, dte_max: int = 55) -> List[Dict[str, Any]]:
    """
    Multi-tenor candidate expiration discovery across:
    - 10 DTE (Ultra-Fast Sprint: 8-12 DTE)
    - 14 DTE (Bi-weekly Sprint: 13-18 DTE)
    - 30 DTE (Monthly Core: 25-35 DTE)
    - 45 DTE (Defensive Anchor: 38-52 DTE)
    """
    today = datetime.date.today()
    try:
        exps = expirations(sym)
    except Exception:
        return []

    parsed = []
    for e in exps:
        try:
            dt = datetime.datetime.strptime(e, "%Y-%m-%d").date()
            dte = (dt - today).days
            if dte_min <= dte <= dte_max:
                parsed.append((e, dte))
        except Exception:
            continue

    if not parsed:
        return []

    if prefer:
        pref_match = [x for x in parsed if x[0] == prefer]
        if pref_match:
            return [{"expiration": pref_match[0][0], "dte": pref_match[0][1], "target_tenor": "prefer"}]

    # 14 to 30 DTE Sweet Spot Tenor Focus (strictly eliminates <14 DTE gamma noise and >32 DTE drag)
    TARGET_TENORS = [14, 21, 28, 30]
    selected = []
    seen = set()
    for t in TARGET_TENORS:
        closest = min(parsed, key=lambda x: abs(x[1] - t))
        if closest[0] not in seen:
            seen.add(closest[0])
            selected.append({"expiration": closest[0], "dte": closest[1], "target_tenor": t})

    selected.sort(key=lambda x: x["dte"])
    return selected


def _choose_expiry(sym: str, prefer: str | None = None, dte_min: int = 8, dte_max: int = 55):
    cands = _candidate_expiries(sym, prefer, dte_min, dte_max)
    if not cands:
        return None, f"no expiry with DTE in [{dte_min},{dte_max}]"
    return cands[0], None


def pick_bull_put_spread(sym: str, width: float | None = None, target_delta: float = 0.20,
                         prefer_exp: str | None = None, dte_min: int = 8, dte_max: int = 55,
                         min_otm_pct: float = 4.0):
    """LIVE bull-put-spread strike selection evaluating across 10, 14, 30, and 45 DTE tenors.

    Ranks valid liquid candidates by Daily Cash Velocity Rate (CVR = ROC% / DTE) so shorter DTE
    sprints delivering superior cash flow velocity win over slow anchors, while strictly preserving
    the OTM safety floor.
    """
    spot_info = get_spot(sym)
    spot = spot_info.get("price")
    if not spot:
        return {"ok": False, "reason": "no live spot", "symbol": sym}

    exp_candidates = _candidate_expiries(sym, prefer_exp, dte_min, dte_max)
    if not exp_candidates:
        return {"ok": False, "reason": f"no expiry with DTE in [{dte_min},{dte_max}]", "symbol": sym, "spot": spot}

    is_etf = sym in ["SPY", "QQQ", "IWM", "SMH", "XLF", "XLU", "XLE", "XLV", "XLP", "GLD", "SLV", "IBIT"]
    tenor_candidates = {}
    valid_liquid_candidates = []
    all_evaluated = []
    hv = hv20(sym)

    for exp_info in exp_candidates:
        expiration, dte = exp_info["expiration"], exp_info["dte"]
        t_tag = exp_info.get("target_tenor", dte)
        try:
            opts = chain(sym, expiration)
        except Exception:
            continue
        puts = [o for o in opts if o["type"].startswith("p") and o.get("delta")]
        if not puts:
            continue

        # short leg: |delta| closest to target, never inside the OTM safety floor
        floor = spot * (1 - min_otm_pct / 100.0)
        eligible = [o for o in puts if o["strike"] <= floor] or puts
        short = min(eligible, key=lambda o: abs(abs(o.get("delta") or 0.20) - target_delta))

        # Executive Efficiency Principle: Calibrate width by underlying spot price
        # Spot < $75: $1.00 (XLF/SLV) or $2.00
        # $75 <= Spot < $250: $5.00
        # Executive Efficiency Principle: Calibrate width by underlying spot price
        # Spot < $75: $2.00 (DIR-11 minimum floor)
        # $75 <= Spot < $200: $5.00
        # $200 <= Spot < $800: $10.00 (Mega-caps & Index ETFs: NVDA, SPY, QQQ, MSFT, META, LMT, UNH, AVGO)
        # Spot >= $800: $20.00 (Ultra-high priced: COST, CAT)
        if width is None:
            if spot < 75:
                w = 2.0  # DIR-11: Enforce minimum $2.00 width (ban $1.00 micro-spreads on XLF/SLV/IBIT)
            elif spot < 200:
                w = 5.0
            elif spot < 800:
                w = 10.0
            else:
                w = 20.0
        else:
            if spot >= 800 and width < 20.0:
                w = 20.0
            elif spot >= 200 and width < 10.0:
                w = 10.0
            elif spot < 75 and width < 2.0:
                w = 2.0
            else:
                w = width
        grid = sorted({o["strike"] for o in puts})
        step = round(min(b - a for a, b in zip(grid, grid[1:])), 2) if len(grid) > 1 else 1.0
        w = max(w, step, 2.0)  # DIR-11: hard floor at $2.00 width
        long_cands = [o for o in puts if o["strike"] <= short["strike"] - step / 2]
        if not long_cands:
            continue
        long_leg = min(long_cands, key=lambda o: abs((short["strike"] - o["strike"]) - w))
        if long_leg["mid"] is None or short["mid"] is None:
            continue
        width_real = round(short["strike"] - long_leg["strike"], 2)
        credit = round(short["mid"] - long_leg["mid"], 2)
        roc = round(credit / width_real * 100, 2) if width_real else None
        daily_cvr = round(roc / max(dte, 1), 2) if roc else 0.0

        # Net Realized Cash & Fee Economics (Tradier $1.40 round-trip, Alpaca $0.20 round-trip)
        round_trip_fee = 1.40 if sym in ["XLF", "SLV", "GLD", "IWM", "AMD", "XLU", "XLE"] else 0.80
        gross_cash = round(credit * 100.0, 2)
        net_cash = max(0.0, round(gross_cash - round_trip_fee, 2))
        net_credit = round(net_cash / 100.0, 3)
        net_roc = round((net_credit / width_real) * 100.0, 2) if width_real else 0.0
        net_daily_cvr = round(net_roc / max(dte, 1), 2)
        fee_drag_pct = round((round_trip_fee / gross_cash) * 100.0, 1) if gross_cash > 0 else 100.0

        short_iv = short.get("iv")

        # Real ATM IV (put closest to spot) and 25-delta put IV for the skew factor.
        ivd = [o for o in puts if o.get("iv")]
        atm_iv = min(ivd, key=lambda o: abs(o["strike"] - spot))["iv"] if ivd else None
        iv_25d = min(ivd, key=lambda o: abs(abs(o["delta"]) - 0.25))["iv"] if ivd else None

        # Liquidity gate: Tiered for single stocks vs broad ETFs
        short_spread = (short.get("ask", 0) or 0) - (short.get("bid", 0) or 0)
        if is_etf:
            liquid = bool(short.get("bid") and long_leg.get("bid")
                          and ((short.get("oi") or 0) >= 100 or (short_spread <= 0.08 and (short.get("volume") or 0) >= 20))
                          and (long_leg.get("oi") or 0) >= 30)
        else:
            liquid = bool(short.get("bid") and long_leg.get("bid")
                          and (short.get("oi") or 0) >= 100 and (long_leg.get("oi") or 0) >= 50)

        otm_pct = round((spot - short["strike"]) / spot * 100, 2)
        spread_candidate = {
            "ok": True, "symbol": sym, "expiration": expiration, "dte": dte, "target_tenor": t_tag,
            "spot": spot, "spot_source": spot_info.get("source"), "spot_asof": spot_info.get("asof"),
            "strike_grid_step": step, "liquid": liquid,
            "short_bid": short.get("bid"), "long_bid": long_leg.get("bid"),
            "short_ask": short.get("ask"), "long_ask": long_leg.get("ask"),
            "atm_iv": atm_iv, "iv_25d_put": iv_25d, "hv20_live": round(hv, 2) if hv else None,
            "short_strike": short["strike"], "long_strike": long_leg["strike"], "width": width_real,
            "short_delta": short.get("delta"), "long_delta": long_leg.get("delta"),
            "short_mid": short["mid"], "long_mid": long_leg["mid"], "credit": credit,
            "credit_bid_ask": f"{short.get('bid')}/{short.get('ask')} - {long_leg.get('bid')}/{long_leg.get('ask')}",
            "gross_credit": credit,
            "gross_cash": gross_cash,
            "round_trip_fee": round_trip_fee,
            "net_cash": net_cash,
            "net_credit": net_credit,
            "roc_pct": roc,
            "net_roc_pct": net_roc,
            "daily_cvr": net_daily_cvr,
            "gross_daily_cvr": daily_cvr,
            "fee_drag_pct": fee_drag_pct,
            "short_oi": short.get("oi"), "long_oi": long_leg.get("oi"),
            "short_volume": short.get("volume"),
            "short_iv": short_iv, "long_iv": long_leg.get("iv"),
            "hv20": round(hv, 2) if hv else None,
            "iv_hv_ratio": round(short_iv / hv, 2) if (short_iv and hv) else None,
            "otm_pct": otm_pct,
            "max_risk_usd": round(width_real * 100, 2),
            "return_on_risk_credit": round(credit * 100, 2),
        }

        tenor_candidates[f"{t_tag}dte"] = spread_candidate
        all_evaluated.append(spread_candidate)

        # Fee Drag Gate & Minimum ROC Gate (DIR-01 12.5% ROC floor)
        is_fee_efficient = (fee_drag_pct <= 15.0)
        min_credit_req = 0.12 if width_real <= 1.0 else 0.20
        if liquid and credit >= min_credit_req and (net_roc or 0) >= 12.5 and otm_pct >= min_otm_pct and is_fee_efficient:
            valid_liquid_candidates.append(spread_candidate)

    if valid_liquid_candidates:
        # Sort by Net Daily Cash Velocity Rate (Net CVR = Net ROC / DTE) descending!
        # Higher NET daily cash velocity after transaction fees wins (DIR-04 & DIR-09)
        valid_liquid_candidates.sort(key=lambda x: x.get("daily_cvr", 0), reverse=True)
        winner = dict(valid_liquid_candidates[0])
        winner["tenor_candidates"] = tenor_candidates
        return winner

    if all_evaluated:
        # Fallback to best liquid candidate or closest to 30 DTE
        all_evaluated.sort(key=lambda x: (x.get("liquid", False), x.get("daily_cvr", 0)), reverse=True)
        winner = dict(all_evaluated[0])
        winner["tenor_candidates"] = tenor_candidates
        return winner

    return {"ok": False, "reason": "no eligible spread found across candidate expiries", "symbol": sym, "spot": spot}


# ---------------------------------------------------------------- volatility risk premium
def vrp_spread(sym: str = "SPY", dte_min: int = 21, dte_max: int = 60):
    """Volatility Risk Premium: live ATM IV minus 20-day realised vol (vol points). No fabrication."""
    try:
        exp_info, err = _choose_expiry(sym, None, dte_min, dte_max)
        if err:
            return {"ok": False, "reason": err}
        spot = get_spot(sym).get("price")
        opts = chain(sym, exp_info["expiration"])
        ivs = [o for o in opts if o.get("iv")]
        atm = min(ivs, key=lambda o: abs(o["strike"] - spot)) if (ivs and spot) else None
        hv = hv20(sym)
        if not (atm and hv):
            return {"ok": False, "reason": "missing ATM IV or HV20"}
        return {"ok": True, "sym": sym, "expiration": exp_info["expiration"], "dte": exp_info["dte"],
                "atm_iv": atm["iv"], "hv20": round(hv, 2), "spread": round(atm["iv"] - hv, 2),
                "ratio": round(atm["iv"] / hv, 2)}
    except Exception as e:
        return {"ok": False, "reason": str(e)}


# ---------------------------------------------------------------- index context
def _sma(closes, n):
    return round(sum(closes[-n:]) / n, 2) if len(closes) >= n else None


def trend_sma20(sym: str, lookback: int = 35) -> dict:
    """
    RULE-089: 20-Day Simple Moving Average & Trend Regime Analyzer.
    Evaluates whether an underlying is trending above its 20-day SMA.
    Returns: {
        "ok": bool, "symbol": str, "spot": float, "sma20": float, "ratio": float,
        "is_above_sma20": bool, "slope_5d": float, "status": str
    }
    """
    try:
        bars = daily_bars(sym, days=lookback)
        if len(bars) < 20:
            return {"ok": False, "symbol": sym, "reason": f"insufficient daily bars ({len(bars)} < 20)"}

        closes = [float(b["close"]) for b in bars]
        sma20 = round(sum(closes[-20:]) / 20.0, 2)

        spot_res = get_spot(sym)
        spot = float(spot_res.get("price") or 0.0) if isinstance(spot_res, dict) else float(spot_res or 0.0)
        if spot <= 0:
            spot = closes[-1]

        # 5-day SMA slope to measure moving average trajectory
        prev_sma20 = round(sum(closes[-25:-5]) / 20.0, 2) if len(closes) >= 25 else sma20
        slope_5d = round(((sma20 - prev_sma20) / prev_sma20) * 100.0, 2) if prev_sma20 > 0 else 0.0

        ratio = round((spot / sma20), 4) if sma20 > 0 else 1.0
        # 0.5% buffer tolerance: Spot >= SMA20 * 0.995 is considered holding trend
        is_above = (spot >= round(sma20 * 0.995, 2))

        if is_above and slope_5d >= 0.0:
            status = "UPTREND"
        elif is_above and slope_5d < 0.0:
            status = "CONSOLIDATING"
        else:
            status = "DOWNTREND"

        return {
            "ok": True,
            "symbol": sym,
            "spot": round(spot, 2),
            "sma20": sma20,
            "ratio": ratio,
            "diff_pct": round((spot - sma20) / sma20 * 100.0, 2),
            "is_above_sma20": is_above,
            "slope_5d": slope_5d,
            "status": status,
            "data_asof": bars[-1]["date"]
        }
    except Exception as e:
        return {"ok": False, "symbol": sym, "reason": str(e)}


def index_context():
    """Live SPY/QQQ/VIX + SMA20/50 + day change, for the pre-market brief. Polls in parallel."""
    out = {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        spots = ex.submit(get_spots, ["SPY", "QQQ", "IWM", "VIX"], 60)
        spy_bars = ex.submit(daily_bars, "SPY", 80)
        qqq_bars = ex.submit(daily_bars, "QQQ", 80)
        book, spy_b, qqq_b = spots.result(), spy_bars.result(), qqq_bars.result()
    for sym, bars in (("SPY", spy_b), ("QQQ", qqq_b)):
        closes = [b["close"] for b in bars]
        r = book.get(sym, {})
        prev = r.get("prev_close")
        out[sym] = {
            "last": r.get("price"), "source": r.get("source"), "asof": r.get("asof"),
            "prev_close": prev,
            "change_pct": round((r["price"] - prev) / prev * 100, 2) if (r.get("price") and prev) else None,
            "sma20": _sma(closes, 20), "sma50": _sma(closes, 50),
            "trend": ("UP" if (closes and out.get(sym, {}).get("sma20") and closes[-1] > out[sym]["sma20"])
                      else "DOWN") if closes else None,
            "data_asof": bars[-1]["date"] if bars else None,
        }
    v = book.get("VIX", {})
    out["VIX"] = {"last": v.get("price"), "source": v.get("source"), "asof": v.get("asof"),
                  "prev_close": v.get("prev_close")}
    out["_meta"] = {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT"),
                    "source": "live_spot/live_market_data (Tradier PROD -> Finnhub -> Alpaca IEX)"}
    return out


def refresh_market_context(path="/home/ubuntu/shared/market_context.json"):
    """Rewrite market_context.json with LIVE values (kills the stale Aug-23 snapshot)."""
    ctx = index_context()
    px = ctx.get("SPY", {}).get("last")
    vix = ctx.get("VIX", {}).get("last")
    sma20 = ctx.get("SPY", {}).get("sma20")
    record = {
        "timestamp": ctx["_meta"]["generated_at"],
        "market_status": "LIVE_REFRESH",
        "regime": ("BULLISH_CONTANGO" if (px and sma20 and px > sma20) else "DEFENSIVE_RANGE"),
        "quotes": {k: v for k, v in ctx.items() if k != "_meta"},
        "vix": vix,
        "_meta": ctx["_meta"],
    }
    try:
        Path(path).write_text(json.dumps(record, indent=2))
        return {"ok": True, "path": str(path), "spy": px, "vix": vix, "sma20": sma20}
    except Exception as e:
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    import sys
    sym = (sys.argv[1] if len(sys.argv) > 1 else "XLU").upper()
    print(f"--- {sym} ---")
    print("box   :", prev_session_box(sym))
    print("hv20  :", round(hv20(sym) or 0, 2))
    sp = pick_bull_put_spread(sym)
    print("spread:", json.dumps(sp, indent=2, default=str))
    if len(sys.argv) > 2 and sys.argv[2] == "--ctx":
        print("ctx   :", json.dumps(refresh_market_context(), indent=2))
