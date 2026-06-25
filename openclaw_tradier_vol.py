#!/usr/bin/env python3
"""
openclaw_tradier_vol.py
=======================
Items 1-3 (IV regime, term structure, skew, flow) implemented on TRADIER,
the API the OpenClaw scanner actually authenticates to. Replaces the IBKR-MCP
prototype with a server-reachable backend.

Why Tradier and not IBKR
------------------------
openclaw_scanner.py talks to api.tradier.com with a Bearer token and already
pulls option chains with greeks=true, reading ORATS smv_vol per contract. The
server has no programmatic IBKR session. So items 1-3 are built on the same
Tradier endpoints/conventions the scanner already uses.

What it produces (per ticker), natively & day-one:
  - spot, atm_iv (from smv_vol)
  - iv_hv_ratio        : ATM IV vs self-computed 30d realized vol  -> "is vol cheap"
  - term_slope/state   : back-expiry ATM IV minus front, contango/backwardation
  - skew_25d/state     : OTM-put IV minus OTM-call IV in the tradeable expiry
  - pcr_oi             : put/call open-interest ratio in tradeable expiry
  - iv_rank            : OPTIONAL, accumulates from a nightly rolling-IV log
                         (Tradier doesn't serve IV percentile). Emits
                         iv_rank=None + "accumulating" until ~40+ samples exist.

Interface
---------
build_tradier_extractor(token, base) returns an object with .extract(symbol)
and .run_batch-compatible output, so openclaw_signals.py can use it exactly
like the IBKR FeatureExtractor — same field names, same signal-block shape.

Conventions matched to openclaw_scanner.py:
  - smv_vol read as float, *100 for percent  (line 359 etc.)
  - greeks='true' on /markets/options/chains   (line 276)
  - TRADIER_BASE = https://api.tradier.com/v1
"""

from __future__ import annotations

import os
import json
import math
import time
import statistics
import datetime as dt
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, Any

import requests

# --------------------------------------------------------------------------- #
# Config — aligned to OpenClaw ruleset v4.0 (scanner constants)
# --------------------------------------------------------------------------- #

DTE_TARGET_LOW = 25
DTE_TARGET_HIGH = 50            # scanner DTE_MAX is 50
STRIKE_WINDOW_PCT = 0.15        # +/-15% of spot for skew legs
HV_LOOKBACK_DAYS = 30           # realized-vol window
IV_RANK_MIN_SAMPLES = 40        # need this many history points before ranking
HTTP_TIMEOUT = 20
MAX_RETRIES = 3
RETRY_BACKOFF_S = 1.5

# rolling IV log — one JSON file, {symbol: [{date, atm_iv}, ...]} capped per symbol
IV_LOG_PATH = os.environ.get(
    "OPENCLAW_IV_LOG",
    "/home/ubuntu/openclaw/logs/iv_history.json")
IV_LOG_CAP = 252                # ~1 trading year per symbol


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _f(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _smv_iv_pct(opt: dict) -> Optional[float]:
    """ATM/leg IV from Tradier greeks.smv_vol, as percent — matches scanner."""
    g = opt.get("greeks") or {}
    v = _f(g.get("smv_vol"))
    if v is None or v <= 0:
        return None
    return round(v * 100, 2)


def _dte(yyyy_mm_dd: str, today: dt.date) -> int:
    d = dt.datetime.strptime(yyyy_mm_dd, "%Y-%m-%d").date()
    return (d - today).days


# --------------------------------------------------------------------------- #
# Feature container — SAME field names as openclaw_features.ForecastFeatures
# so the merge layer treats both backends identically.
# --------------------------------------------------------------------------- #

@dataclass
class VolFeatures:
    symbol: str
    asof: str
    spot: Optional[float] = None

    iv_annual: Optional[float] = None          # ATM IV (percent)
    hv_annual: Optional[float] = None          # 30d realized (percent)
    iv_hv_ratio: Optional[float] = None
    iv_percentile_13w: Optional[float] = None  # from rolling log (0..1) or None
    iv_rank_ok: Optional[bool] = None

    term_front_dte: Optional[int] = None
    term_back_dte: Optional[int] = None
    term_front_iv: Optional[float] = None
    term_back_iv: Optional[float] = None
    term_slope: Optional[float] = None
    term_state: Optional[str] = None

    atm_iv: Optional[float] = None
    skew_25d: Optional[float] = None
    skew_state: Optional[str] = None

    pcr_oi: Optional[float] = None
    tradeable_dte_found: Optional[bool] = None

    notes: list = field(default_factory=list)

    def to_signal_block(self) -> dict:
        out = {}
        for k, v in asdict(self).items():
            if v is None:
                continue
            out[k] = round(v, 4) if isinstance(v, float) else v
        return out


# --------------------------------------------------------------------------- #
# Rolling IV log (gives us IV-Rank that Tradier won't serve directly)
# --------------------------------------------------------------------------- #

def _load_iv_log() -> dict:
    try:
        return json.loads(Path(IV_LOG_PATH).read_text())
    except Exception:  # noqa: BLE001
        return {}


def _save_iv_log(log: dict) -> None:
    try:
        p = Path(IV_LOG_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(log))
        tmp.replace(p)
    except Exception as e:  # noqa: BLE001
        print(f"[tradier_vol] could not write IV log: {e}")


def _update_and_rank(symbol: str, atm_iv: Optional[float],
                     today: dt.date, log: dict) -> Optional[float]:
    """Append today's ATM IV, return IV percentile (0..1) if enough history."""
    if atm_iv is None:
        hist = log.get(symbol, [])
        vals = [h["atm_iv"] for h in hist]
        return None if len(vals) < IV_RANK_MIN_SAMPLES else None
    hist = log.setdefault(symbol, [])
    iso = today.isoformat()
    if not hist or hist[-1].get("date") != iso:   # one sample per day
        hist.append({"date": iso, "atm_iv": atm_iv})
        del hist[:-IV_LOG_CAP]
    vals = [h["atm_iv"] for h in hist]
    if len(vals) < IV_RANK_MIN_SAMPLES:
        return None
    below = sum(1 for v in vals if v <= atm_iv)
    return below / len(vals)


# --------------------------------------------------------------------------- #
# Tradier client
# --------------------------------------------------------------------------- #

class TradierVolExtractor:
    def __init__(self, token: str, base: str = "https://api.tradier.com/v1",
                 today: Optional[dt.date] = None):
        self.base = base.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self.today = today or dt.date.today()
        self._iv_log = _load_iv_log()

    def _get(self, path: str, params: dict) -> Optional[dict]:
        url = f"{self.base}{path}"
        for attempt in range(MAX_RETRIES):
            try:
                r = requests.get(url, headers=self.headers, params=params,
                                 timeout=HTTP_TIMEOUT)
                if r.status_code == 200:
                    return r.json()
                if r.status_code in (429, 502, 503) and attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BACKOFF_S * (2 ** attempt))
                    continue
                print(f"[tradier_vol] {path} -> HTTP {r.status_code}")
                return None
            except Exception as e:  # noqa: BLE001
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BACKOFF_S * (2 ** attempt))
                    continue
                print(f"[tradier_vol] {path} failed: {e}")
        return None

    # ---- spot ----------------------------------------------------------- #

    def _spot(self, symbol: str) -> Optional[float]:
        d = self._get("/markets/quotes", {"symbols": symbol, "greeks": "false"})
        try:
            q = d["quotes"]["quote"]
            if isinstance(q, list):
                q = q[0]
            return _f(q.get("last")) or _f(q.get("close"))
        except Exception:  # noqa: BLE001
            return None

    # ---- realized vol from history -------------------------------------- #

    def _hv(self, symbol: str) -> Optional[float]:
        end = self.today
        start = end - dt.timedelta(days=HV_LOOKBACK_DAYS * 2 + 10)  # cal days buffer
        d = self._get("/markets/history", {
            "symbol": symbol, "interval": "daily",
            "start": start.isoformat(), "end": end.isoformat()})
        try:
            days = d["history"]["day"]
            if isinstance(days, dict):
                days = [days]
            closes = [_f(x.get("close")) for x in days if _f(x.get("close"))]
        except Exception:  # noqa: BLE001
            return None
        if len(closes) < 10:
            return None
        closes = closes[-(HV_LOOKBACK_DAYS + 1):]
        rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        if len(rets) < 5:
            return None
        daily_sd = statistics.pstdev(rets)
        return round(daily_sd * math.sqrt(252) * 100, 2)   # annualized percent

    # ---- expirations ---------------------------------------------------- #

    def _expirations(self, symbol: str) -> list[str]:
        d = self._get("/markets/options/expirations",
                      {"symbol": symbol, "includeAllRoots": "true"})
        try:
            dates = d["expirations"]["date"]
            return dates if isinstance(dates, list) else [dates]
        except Exception:  # noqa: BLE001
            return []

    # ---- chain ---------------------------------------------------------- #

    def _chain(self, symbol: str, expiry: str) -> list[dict]:
        d = self._get("/markets/options/chains",
                      {"symbol": symbol, "expiration": expiry, "greeks": "true"})
        try:
            opts = d["options"]["option"]
            return opts if isinstance(opts, list) else [opts]
        except Exception:  # noqa: BLE001
            return []

    # ---- per-expiry ATM IV ---------------------------------------------- #

    def _atm_iv(self, chain: list[dict], spot: float) -> Optional[float]:
        cands = [(o, abs(_f(o.get("strike")) - spot))
                 for o in chain if _f(o.get("strike"))]
        if not cands:
            return None
        cands.sort(key=lambda t: t[1])
        # average call+put IV at nearest strike
        near_strike = _f(cands[0][0].get("strike"))
        ivs = [_smv_iv_pct(o) for o in chain
               if _f(o.get("strike")) == near_strike and _smv_iv_pct(o)]
        return round(sum(ivs) / len(ivs), 2) if ivs else None

    # ---- skew + OI within an expiry ------------------------------------- #

    def _skew(self, f: VolFeatures, chain: list[dict], spot: float) -> None:
        put_t, call_t = spot * 0.85, spot * 1.15
        puts = [o for o in chain if (o.get("option_type") == "put") and _f(o.get("strike"))]
        calls = [o for o in chain if (o.get("option_type") == "call") and _f(o.get("strike"))]
        if not puts or not calls:
            return
        otm_put = min(puts, key=lambda o: abs(_f(o["strike"]) - put_t))
        otm_call = min(calls, key=lambda o: abs(_f(o["strike"]) - call_t))
        put_iv, call_iv = _smv_iv_pct(otm_put), _smv_iv_pct(otm_call)
        if put_iv and call_iv:
            f.skew_25d = round(put_iv - call_iv, 2)
            if f.skew_25d > 3.0:
                f.skew_state = "steep_put"
            elif f.skew_25d < -1.0:
                f.skew_state = "call_skew"
            else:
                f.skew_state = "normal"
        # put/call OI ratio across the expiry
        oi_p = sum(_f(o.get("open_interest")) or 0 for o in puts)
        oi_c = sum(_f(o.get("open_interest")) or 0 for o in calls)
        if oi_c > 0:
            f.pcr_oi = round(oi_p / oi_c, 3)

    # ---- orchestration -------------------------------------------------- #

    def extract(self, symbol: str) -> VolFeatures:
        f = VolFeatures(symbol=symbol, asof=self.today.isoformat())
        f.spot = self._spot(symbol)
        if not f.spot:
            f.notes.append("no spot — vol features skipped")
            return f

        f.hv_annual = self._hv(symbol)

        exps = [e for e in self._expirations(symbol) if _dte(e, self.today) > 0]
        exps.sort()
        if not exps:
            f.notes.append("no expirations")
            return f

        # tradeable expiry inside DTE window, else nearest
        tradeable = next((e for e in exps
                          if DTE_TARGET_LOW <= _dte(e, self.today) <= DTE_TARGET_HIGH), None)
        f.tradeable_dte_found = tradeable is not None
        front = tradeable or exps[0]
        front_dte = _dte(front, self.today)
        back = next((e for e in exps if _dte(e, self.today) >= front_dte + 25), None)

        front_chain = self._chain(symbol, front)
        if front_chain:
            f.term_front_dte = front_dte
            f.term_front_iv = self._atm_iv(front_chain, f.spot)
            f.atm_iv = f.term_front_iv
            f.iv_annual = f.term_front_iv
            self._skew(f, front_chain, f.spot)

        if back:
            back_chain = self._chain(symbol, back)
            if back_chain:
                f.term_back_dte = _dte(back, self.today)
                f.term_back_iv = self._atm_iv(back_chain, f.spot)
                if f.term_front_iv and f.term_back_iv:
                    f.term_slope = round(f.term_back_iv - f.term_front_iv, 2)
                    if f.term_slope > 1.0:
                        f.term_state = "contango"
                    elif f.term_slope < -1.0:
                        f.term_state = "backwardation"
                    else:
                        f.term_state = "flat"

        # IV/HV ratio
        if f.iv_annual and f.hv_annual:
            f.iv_hv_ratio = round(f.iv_annual / f.hv_annual, 4)

        # IV rank from rolling log (None until enough history)
        pct = _update_and_rank(symbol, f.atm_iv, self.today, self._iv_log)
        f.iv_percentile_13w = round(pct, 4) if pct is not None else None
        if f.iv_percentile_13w is not None:
            f.iv_rank_ok = f.iv_percentile_13w <= 0.40
        else:
            n = len(self._iv_log.get(symbol, []))
            f.notes.append(f"IV-rank accumulating ({n}/{IV_RANK_MIN_SAMPLES} samples)")

        self._interpret(f)
        return f

    @staticmethod
    def _interpret(f: VolFeatures) -> None:
        if f.iv_hv_ratio is not None:
            if f.iv_hv_ratio < 0.85:
                f.notes.append("IV below realized vol — options cheap vs recent movement")
            elif f.iv_hv_ratio > 1.2:
                f.notes.append("IV rich vs realized — premium-selling favourable")
        if f.skew_state == "steep_put":
            f.notes.append("steep put skew — downside protection bid up")
        if f.term_state == "backwardation":
            f.notes.append("term backwardation — near-term stress/event priced in")
        if f.tradeable_dte_found is False:
            f.notes.append("no expiration in 25-50 DTE window")

    def persist(self) -> None:
        """Call once after a batch to save the rolling IV log."""
        _save_iv_log(self._iv_log)


# --------------------------------------------------------------------------- #
# Batch + factory (interface openclaw_signals.py expects)
# --------------------------------------------------------------------------- #

def run_batch(symbols, extractor: TradierVolExtractor) -> dict:
    out = {}
    for sym in symbols:
        try:
            out[sym] = extractor.extract(sym).to_signal_block()
        except Exception as e:  # noqa: BLE001
            out[sym] = {"symbol": sym, "error": str(e)}
        time.sleep(0.2)
    extractor.persist()
    return out


def build_tradier_extractor(token: Optional[str] = None,
                            base: str = "https://api.tradier.com/v1",
                            today: Optional[dt.date] = None) -> Optional[TradierVolExtractor]:
    token = token or os.environ.get("TRADIER_API_KEY") or os.environ.get("TRADIER_TOKEN")
    if not token:
        print("[tradier_vol] no TRADIER_API_KEY — vol axis unavailable")
        return None
    return TradierVolExtractor(token, base, today)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="OpenClaw Tradier vol/skew features (items 1-3)")
    p.add_argument("symbols", nargs="*", default=["F"])
    args = p.parse_args()
    ex = build_tradier_extractor()
    if ex:
        print(json.dumps(run_batch(args.symbols or ["F"], ex), indent=2))
