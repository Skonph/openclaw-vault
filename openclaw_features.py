#!/usr/bin/env python3
"""
openclaw_features.py
====================
Forecast-enhancement feature extractor for OpenClaw.

Covers the priority stack items 1-3:
  1. IV term-structure slope + skew  (from IBKR option chains)        -> FREE
  2. Underlying vol/positioning features (IV vs HV, IV percentile,    -> FREE
     option volume vs average, put/call ratio)
  3. Put/Call ratio + OI-change signals derived from chain data       -> FREE

Design principle
----------------
This module does NOT predict or place trades. It emits a STRUCTURED FEATURE
BLOCK (one compact dict/JSON object per underlying) for the strategist tier to
reason over. Clean features -> better conviction scores -> lower token cost.

It is transport-agnostic: it depends only on four callables that wrap the IBKR
MCP tools. In the cron/Nova environment, pass in your existing IBKR client
wrappers. For local testing, a stub harness is provided at the bottom.

The four injected callables (signatures mirror the IBKR MCP tools):
    search_contracts(query, security_type="STK")          -> dict
    get_price_snapshot(contract_id, market_data_names)     -> dict
    get_option_parameters(underlying_contract_id)          -> dict
    get_option_data(expiration_id, min_strike, max_strike) -> dict   (chain structure)
    # get_price_snapshot is reused for per-contract IV

Author: built for Skon / OpenClaw, June 2026
"""

from __future__ import annotations

import json
import math
import time
import datetime as dt
from dataclasses import dataclass, asdict, field
from typing import Callable, Optional, Any

# --------------------------------------------------------------------------- #
# Config — aligned to OpenClaw ruleset v4.0
# --------------------------------------------------------------------------- #

DTE_TARGET_LOW = 25
DTE_TARGET_HIGH = 40
IV_RANK_MAX = 0.40          # ruleset: IV Rank <= 40%
STRIKE_WINDOW_PCT = 0.15    # pull strikes within +/-15% of spot for skew calc
MAX_RETRIES = 3
RETRY_BACKOFF_S = 1.5       # transient IBKR errors: exponential backoff


# --------------------------------------------------------------------------- #
# Retry wrapper — IBKR MCP throws transient "try again later" errors
# --------------------------------------------------------------------------- #

def _retry(fn: Callable, *args, **kwargs) -> Any:
    """Call fn with exponential backoff on transient errors. Returns None on
    persistent failure rather than raising, so a single bad leg doesn't kill
    the whole nightly scan."""
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            result = fn(*args, **kwargs)
            # MCP error envelopes sometimes come back as dicts with 'error'
            if isinstance(result, dict) and result.get("error"):
                raise RuntimeError(result["error"])
            return result
        except Exception as e:  # noqa: BLE001 — deliberately broad for resilience
            last_exc = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_S * (2 ** attempt))
    print(f"[features] persistent failure after {MAX_RETRIES} tries: {last_exc}")
    return None


# --------------------------------------------------------------------------- #
# Small numeric helpers
# --------------------------------------------------------------------------- #

def _f(x) -> Optional[float]:
    """Coerce to float, tolerating strings and None."""
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _dte(yyyymmdd: str, today: Optional[dt.date] = None) -> int:
    today = today or dt.date.today()
    d = dt.datetime.strptime(yyyymmdd, "%Y%m%d").date()
    return (d - today).days


def _valid_iv(snapshot: dict) -> Optional[float]:
    """Extract a usable per-contract IV from a price snapshot.

    Per the IBKR tool contract: prefer option-midpoint-iv only when its
    isValid flag is true (invalid returns a negative sentinel); otherwise
    fall back to implied_vol. Response keys are hyphenated.
    """
    mid = snapshot.get("option-midpoint-iv")
    if isinstance(mid, dict) and mid.get("isValid") and _f(mid.get("value")) and _f(mid["value"]) > 0:
        return _f(mid["value"])
    iv = snapshot.get("implied-vol")
    if isinstance(iv, dict):
        v = _f(iv.get("value"))
        return v if (v and v > 0) else None
    v = _f(iv)
    return v if (v and v > 0) else None


# --------------------------------------------------------------------------- #
# Feature container
# --------------------------------------------------------------------------- #

@dataclass
class ForecastFeatures:
    symbol: str
    asof: str
    spot: Optional[float] = None

    # --- Item 1: underlying vol regime ---
    iv_annual: Optional[float] = None          # underlying IV
    hv_annual: Optional[float] = None          # 30d realized
    iv_hv_ratio: Optional[float] = None        # <1 = options cheap vs realized
    iv_percentile_13w: Optional[float] = None  # proxy for IV Rank
    iv_percentile_52w: Optional[float] = None

    # --- Item 1: term structure ---
    term_front_dte: Optional[int] = None
    term_back_dte: Optional[int] = None
    term_front_iv: Optional[float] = None
    term_back_iv: Optional[float] = None
    term_slope: Optional[float] = None         # back_iv - front_iv (annualized)
    term_state: Optional[str] = None           # contango / backwardation / flat

    # --- Item 1 & 3: skew (in the tradeable expiry) ---
    skew_25d: Optional[float] = None           # put_iv(~0.85 moneyness) - call_iv(~1.15)
    atm_iv: Optional[float] = None
    skew_state: Optional[str] = None           # steep_put / normal / call_skew

    # --- Item 3: flow / positioning ---
    pcr_volume: Optional[float] = None         # put/call volume ratio (underlying)
    pcr_oi: Optional[float] = None             # put/call OI ratio (tradeable expiry)
    opt_vol_vs_avg: Optional[float] = None     # today volume / avg volume (unusual activity)

    # --- ruleset gate flags (convenience for strategist) ---
    iv_rank_ok: Optional[bool] = None          # iv_percentile_13w <= IV_RANK_MAX
    tradeable_dte_found: Optional[bool] = None

    notes: list = field(default_factory=list)

    def to_signal_block(self) -> dict:
        """Compact, strategist-facing representation. Rounds floats, drops Nones."""
        raw = asdict(self)
        out = {}
        for k, v in raw.items():
            if v is None:
                continue
            if isinstance(v, float):
                out[k] = round(v, 4)
            else:
                out[k] = v
        return out


# --------------------------------------------------------------------------- #
# Extractor
# --------------------------------------------------------------------------- #

class FeatureExtractor:
    def __init__(
        self,
        search_contracts: Callable,
        get_price_snapshot: Callable,
        get_option_parameters: Callable,
        get_option_data: Callable,
        today: Optional[dt.date] = None,
    ):
        self.search_contracts = search_contracts
        self.get_price_snapshot = get_price_snapshot
        self.get_option_parameters = get_option_parameters
        self.get_option_data = get_option_data
        self.today = today or dt.date.today()

    # ---- contract resolution -------------------------------------------- #

    def resolve(self, symbol: str) -> Optional[int]:
        res = _retry(self.search_contracts, symbol, security_type="STK")
        if not res:
            return None
        for row in res.get("results", []):
            if row.get("symbol") == symbol and row.get("country_code") == "US":
                if any(s.get("security_type") == "OPT" for s in row.get("sections", [])):
                    return row.get("underlying_contract_id")
        return None

    # ---- item 1: underlying vol regime ---------------------------------- #

    def underlying_features(self, f: ForecastFeatures, cid: int) -> None:
        snap = _retry(
            self.get_price_snapshot,
            cid,
            ["last", "historical_vol", "implied_vol_underlying",
             "implied_volatility_percentile", "underlying_today_option_volume",
             "underlying_avg_option_volume"],
        )
        if not snap:
            f.notes.append("underlying snapshot unavailable")
            return

        last = snap.get("last") or {}
        f.spot = _f(last.get("price"))

        hv = snap.get("historical-vol") or {}
        f.hv_annual = _f(hv.get("annual_pct"))

        ivu = snap.get("implied-vol-underlying") or {}
        if ivu.get("is_valid"):
            f.iv_annual = _f(ivu.get("annual_iv"))

        if f.iv_annual and f.hv_annual:
            f.iv_hv_ratio = f.iv_annual / f.hv_annual

        pct = snap.get("implied-volatility-percentile") or {}
        f.iv_percentile_13w = _f(pct.get("high_13w"))
        f.iv_percentile_52w = _f(pct.get("high_52w"))
        if f.iv_percentile_13w is not None:
            f.iv_rank_ok = f.iv_percentile_13w <= IV_RANK_MAX

        # put/call VOLUME ratio + unusual activity (item 3, underlying level)
        tv = snap.get("underlying-today-option-volume") or {}
        av = snap.get("underlying-avg-option-volume") or {}
        cv, pv = _f(tv.get("callVolume")), _f(tv.get("putVolume"))
        if cv and pv is not None and cv > 0:
            f.pcr_volume = pv / cv
        tot_today = (cv or 0) + (pv or 0)
        tot_avg = (_f(av.get("avgCallVolume")) or 0) + (_f(av.get("avgPutVolume")) or 0)
        if tot_avg > 0:
            f.opt_vol_vs_avg = tot_today / tot_avg

    # ---- item 1: term structure ----------------------------------------- #

    def _atm_iv_for_expiration(self, exp_id: str, spot: float) -> tuple[Optional[float], dict]:
        """Return (atm_iv, chain_dict). Pulls strikes around spot, snapshots the
        nearest-to-money call & put, averages their IV."""
        lo = round(spot * (1 - STRIKE_WINDOW_PCT), 1)
        hi = round(spot * (1 + STRIKE_WINDOW_PCT), 1)
        chain = _retry(self.get_option_data, exp_id, min_strike=lo, max_strike=hi)
        if not chain:
            return None, {}
        rows = chain.get("rows") or chain.get("strikes") or chain  # tolerate shape
        if isinstance(rows, dict):
            rows = rows.get("rows", [])
        if not rows:
            return None, chain

        # find strike nearest spot
        def strike_of(r):
            return abs(_f(r.get("strike")) - spot) if _f(r.get("strike")) else 1e9
        rows_sorted = sorted(rows, key=strike_of)
        atm = rows_sorted[0]

        ivs = []
        for leg_key in ("call_contract_id", "put_contract_id"):
            ccid = atm.get(leg_key)
            if not ccid:
                continue
            s = _retry(self.get_price_snapshot, ccid,
                       ["option_midpoint_iv", "implied_vol"])
            if s:
                iv = _valid_iv(s)
                if iv:
                    ivs.append(iv)
        atm_iv = sum(ivs) / len(ivs) if ivs else None
        return atm_iv, chain

    def term_and_skew_features(self, f: ForecastFeatures, cid: int) -> None:
        params = _retry(self.get_option_parameters, cid)
        if not params or not f.spot:
            f.notes.append("term/skew skipped (no params or spot)")
            return

        exps = params.get("expirations", [])
        # only standard monthlies, future-dated
        monthlies = [
            e for e in exps
            if e.get("regular") and not e.get("trading_class", "F").startswith("2")
            and _dte(e["date"], self.today) > 0
        ]
        monthlies.sort(key=lambda e: e["date"])
        if not monthlies:
            f.notes.append("no regular monthly expirations")
            return

        # tradeable expiry = first monthly inside [25,40] DTE, else nearest monthly
        tradeable = next(
            (e for e in monthlies if DTE_TARGET_LOW <= _dte(e["date"], self.today) <= DTE_TARGET_HIGH),
            None,
        )
        f.tradeable_dte_found = tradeable is not None
        front = tradeable or monthlies[0]
        # back leg for term slope: first monthly at least ~30d beyond front
        front_dte = _dte(front["date"], self.today)
        back = next((e for e in monthlies if _dte(e["date"], self.today) >= front_dte + 25), None)

        # --- ATM IV per expiry -> term structure ---
        front_iv, front_chain = self._atm_iv_for_expiration(front["id"], f.spot)
        f.term_front_dte = front_dte
        f.term_front_iv = front_iv
        f.atm_iv = front_iv

        if back:
            back_iv, _ = self._atm_iv_for_expiration(back["id"], f.spot)
            f.term_back_dte = _dte(back["date"], self.today)
            f.term_back_iv = back_iv
            if front_iv and back_iv:
                f.term_slope = back_iv - front_iv
                if f.term_slope > 0.01:
                    f.term_state = "contango"
                elif f.term_slope < -0.01:
                    f.term_state = "backwardation"
                else:
                    f.term_state = "flat"

        # --- skew within tradeable expiry ---
        self._skew_from_chain(f, front_chain)

    def _skew_from_chain(self, f: ForecastFeatures, chain: dict) -> None:
        rows = chain.get("rows") or chain.get("strikes") or []
        if isinstance(rows, dict):
            rows = rows.get("rows", [])
        if not rows or not f.spot:
            return

        # OTM put ~ 0.85 * spot ; OTM call ~ 1.15 * spot
        put_target = f.spot * 0.85
        call_target = f.spot * 1.15

        def nearest(target):
            cands = [r for r in rows if _f(r.get("strike"))]
            if not cands:
                return None
            return min(cands, key=lambda r: abs(_f(r["strike"]) - target))

        put_row = nearest(put_target)
        call_row = nearest(call_target)

        put_iv = call_iv = None
        oi_calls = oi_puts = 0.0

        if put_row and put_row.get("put_contract_id"):
            s = _retry(self.get_price_snapshot, put_row["put_contract_id"],
                       ["option_midpoint_iv", "implied_vol", "option_open_interest"])
            if s:
                put_iv = _valid_iv(s)
                oi = s.get("option-open-interest") or {}
                oi_puts += _f(oi.get("put")) or 0
        if call_row and call_row.get("call_contract_id"):
            s = _retry(self.get_price_snapshot, call_row["call_contract_id"],
                       ["option_midpoint_iv", "implied_vol", "option_open_interest"])
            if s:
                call_iv = _valid_iv(s)
                oi = s.get("option-open-interest") or {}
                oi_calls += _f(oi.get("call")) or 0

        if put_iv and call_iv:
            f.skew_25d = put_iv - call_iv
            if f.skew_25d > 0.03:
                f.skew_state = "steep_put"      # downside fear priced in
            elif f.skew_25d < -0.01:
                f.skew_state = "call_skew"       # upside chase / squeeze risk
            else:
                f.skew_state = "normal"

        if oi_calls > 0:
            f.pcr_oi = oi_puts / oi_calls

    # ---- orchestration --------------------------------------------------- #

    def extract(self, symbol: str) -> ForecastFeatures:
        f = ForecastFeatures(symbol=symbol, asof=self.today.isoformat())
        cid = self.resolve(symbol)
        if not cid:
            f.notes.append("contract not resolved")
            return f
        self.underlying_features(f, cid)
        self.term_and_skew_features(f, cid)
        self._interpret(f)
        return f

    @staticmethod
    def _interpret(f: ForecastFeatures) -> None:
        """Attach plain-language flags the strategist can weight. These are
        observations, NOT trade signals — the strategist decides."""
        if f.iv_hv_ratio is not None:
            if f.iv_hv_ratio < 0.85:
                f.notes.append("IV below realized vol — options cheap vs recent movement")
            elif f.iv_hv_ratio > 1.2:
                f.notes.append("IV rich vs realized — premium-selling favourable")
        if f.iv_rank_ok is False:
            f.notes.append("IV percentile above ruleset cap (40%) — avoid per v4.0")
        if f.skew_state == "steep_put":
            f.notes.append("steep put skew — market paying up for downside protection")
        if f.opt_vol_vs_avg and f.opt_vol_vs_avg > 2.0:
            f.notes.append(f"unusual option activity ({f.opt_vol_vs_avg:.1f}x avg)")
        if f.term_state == "backwardation":
            f.notes.append("term backwardation — near-term event/stress priced in")


# --------------------------------------------------------------------------- #
# CLI / batch entry point
# --------------------------------------------------------------------------- #

def run_batch(symbols, extractor: FeatureExtractor) -> dict:
    blocks = {}
    for sym in symbols:
        try:
            feats = extractor.extract(sym)
            blocks[sym] = feats.to_signal_block()
        except Exception as e:  # noqa: BLE001
            blocks[sym] = {"symbol": sym, "error": str(e)}
    return blocks


if __name__ == "__main__":
    # In the OpenClaw cron environment you would import your IBKR MCP client
    # wrappers and pass them in. Example wiring (pseudocode):
    #
    #   from openclaw_ibkr import (search_contracts, get_price_snapshot,
    #                              get_option_parameters, get_option_data)
    #   ex = FeatureExtractor(search_contracts, get_price_snapshot,
    #                         get_option_parameters, get_option_data)
    #   watchlist = ["F", "FCX", "PLTR", "SOFI", "RIVN"]   # your $10-30 names
    #   out = run_batch(watchlist, ex)
    #   print(json.dumps(out, indent=2))
    #
    print(__doc__)
