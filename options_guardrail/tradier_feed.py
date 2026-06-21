"""
Tradier market-data feed for the strategist context.

Tradier (sandbox or prod) gives real quotes, option chains with Greeks/IV, and a
market clock — enough to feed the strategist real numbers while IBKR handles
execution.

SAFETY: this module is DATA-ONLY. It calls only GET /markets/* endpoints (quotes,
option chains, clock) and has no method that can place, modify, or cancel an
order. That matters because a Tradier *prod* token can trade your live account —
but it can only do so through trading endpoints this module never touches. The
executor is IBKR-only and never receives the Tradier token. Keep it that way:
never add an order method here, and never pass cfg.tradier_token to a broker.

Stdlib HTTP, injectable getter for tests. Returns are normalized (Tradier wraps
single results as a dict and multiples as a list; we always hand back lists).
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional

HttpGet = Callable[[str, dict], dict]


def _default_get(token: str) -> HttpGet:
    def get(url: str, params: dict) -> dict:
        full = url + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(full, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    return get


def _as_list(node: Any) -> List[dict]:
    if node is None:
        return []
    if isinstance(node, list):
        return node
    return [node]


class TradierClient:
    def __init__(self, token: str, base_url: str = "https://sandbox.tradier.com/v1",
                 http_get: Optional[HttpGet] = None):
        self.base_url = base_url.rstrip("/")
        self._get = http_get or _default_get(token)

    # ---------- raw endpoints ----------
    def quotes(self, symbols: List[str]) -> List[dict]:
        resp = self._get(f"{self.base_url}/markets/quotes",
                         {"symbols": ",".join(symbols), "greeks": "false"})
        return _as_list((resp.get("quotes") or {}).get("quote"))

    def expirations(self, symbol: str) -> List[str]:
        resp = self._get(f"{self.base_url}/markets/options/expirations",
                         {"symbol": symbol})
        dates = (resp.get("expirations") or {}).get("date")
        return _as_list(dates) if isinstance(dates, list) else ([dates] if dates else [])

    def option_chain(self, symbol: str, expiration: str,
                     greeks: bool = True) -> List[dict]:
        resp = self._get(f"{self.base_url}/markets/options/chains",
                         {"symbol": symbol, "expiration": expiration,
                          "greeks": "true" if greeks else "false"})
        return _as_list((resp.get("options") or {}).get("option"))

    def clock(self) -> dict:
        resp = self._get(f"{self.base_url}/markets/clock", {})
        return resp.get("clock") or {}

    def history(self, symbol: str, start: str, end: str, interval: str = "daily") -> List[dict]:
        resp = self._get(f"{self.base_url}/markets/history",
                         {"symbol": symbol, "start": start, "end": end, "interval": interval})
        return _as_list((resp.get("history") or {}).get("day"))

    # ---------- derived helpers ----------
    def quote_summary(self, symbols: List[str]) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for q in self.quotes(symbols):
            sym = q.get("symbol")
            if not sym:
                continue
            last = q.get("last")
            prev = q.get("prevclose")
            chg_pct = None
            if last is not None and prev:
                try:
                    chg_pct = round((float(last) - float(prev)) / float(prev), 4)
                except (TypeError, ZeroDivisionError, ValueError):
                    chg_pct = None
            out[sym] = {"last": last, "prev_close": prev, "change_pct": chg_pct,
                        "bid": q.get("bid"), "ask": q.get("ask"),
                        "volume": q.get("volume")}
        return out

    def atm_iv(self, symbol: str, expiration: Optional[str] = None
               ) -> Optional[float]:
        """Implied vol of the strike nearest the underlying, from the nearest
        (or given) expiration. None if data is unavailable."""
        exps = [expiration] if expiration else self.expirations(symbol)
        if not exps:
            return None
        und = self.quote_summary([symbol]).get(symbol, {})
        spot = und.get("last")
        if spot is None:
            return None
        spot = float(spot)
        chain = self.option_chain(symbol, exps[0], greeks=True)
        best, best_dist = None, float("inf")
        for opt in chain:
            strike = opt.get("strike")
            greeks = opt.get("greeks") or {}
            iv = greeks.get("mid_iv") or greeks.get("smv_vol")
            if strike is None or iv is None:
                continue
            dist = abs(float(strike) - spot)
            if dist < best_dist:
                best, best_dist = float(iv), dist
        return best


# ----------------------------- context providers -----------------------------
def make_tradier_providers(client: TradierClient, symbols: List[str]) -> dict:
    """Return {flow, iv, calendar} callables for context_builder."""

    def flow():
        q = client.quote_summary(symbols)
        return {"source": "tradier", "quotes": q,
                "note": "last + % change vs prior close (Tradier may be delayed in sandbox)"}

    def iv():
        out = {}
        for s in symbols:
            try:
                out[s] = client.atm_iv(s)
            except Exception as e:
                out[s] = f"error: {e}"
        return {"source": "tradier", "atm_iv": out}

    def calendar():
        # Tradier exposes a market clock, NOT an econ-event calendar.
        try:
            c = client.clock()
            return {"source": "tradier_clock", "state": c.get("state"),
                    "description": c.get("description"),
                    "note": "market status only — econ events not provided by Tradier"}
        except Exception as e:
            return f"clock error: {e}"

    return {"flow": flow, "iv": iv, "calendar": calendar}


def from_config(cfg, symbols: List[str]) -> Optional[dict]:
    """Build providers from Config, or None if no Tradier token is set."""
    if not cfg.tradier_token:
        return None
    client = TradierClient(cfg.tradier_token, cfg.tradier_base_url)
    return make_tradier_providers(client, symbols)


def _self_check(symbols: List[str]) -> int:
    """CLI: confirm the configured Tradier token pulls data. Returns exit code.
        python3 tradier_feed.py SPY QQQ
    Prints quotes, ATM IV, and market clock using whatever TRADIER_ENV is set."""
    from config import Config
    cfg = Config.load()
    if not cfg.tradier_token:
        print("No Tradier token configured (set TRADIER_SANDBOX_TOKEN or "
              "TRADIER_PROD_TOKEN + TRADIER_ENV=prod).")
        return 2
    client = TradierClient(cfg.tradier_token, cfg.tradier_base_url)
    print(f"env={cfg.tradier_env}  base={cfg.tradier_base_url}")
    try:
        clock = client.clock()
        print(f"clock: {clock.get('state')} — {clock.get('description')}")
        q = client.quote_summary(symbols)
        for s in symbols:
            row = q.get(s, {})
            iv = None
            try:
                iv = client.atm_iv(s)
            except Exception as e:
                iv = f"err: {e}"
            print(f"  {s}: last={row.get('last')} "
                  f"chg={row.get('change_pct')} atm_iv={iv}")
        # liveness hint: sandbox is delayed; prod is real-time if entitled
        print("NOTE: prod token = real-time if your account is entitled; "
              "sandbox is delayed.")
        return 0
    except Exception as e:
        print(f"Tradier request failed: {e}")
        return 1


if __name__ == "__main__":
    import sys
    syms = [s.upper() for s in sys.argv[1:]] or ["SPY", "QQQ", "IWM"]
    raise SystemExit(_self_check(syms))
