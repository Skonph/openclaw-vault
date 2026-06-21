"""
Economic-calendar feed for the strategist (event-risk awareness).

Two backends, used in order with automatic fallback:
  1. Finnhub  — richest: event, impact (low/medium/high), estimate, prev, actual.
                Free tier sometimes 403s this endpoint; if so we fall through.
  2. FRED     — St. Louis Fed, free forever. No forecast/impact, but gives the
                RELEASE SCHEDULE of the high-impact US series (CPI, NFP, PCE, GDP,
                PPI, retail sales). Enough to flag "CPI drops today".

Read-only HTTP (stdlib), injectable getter for tests. A backend that errors or
returns nothing is skipped so the strategist still gets *something* (or a clean
"no calendar" note) rather than crashing.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from typing import Callable, List, Optional

HttpGet = Callable[[str], dict]


def _default_get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read().decode())


@dataclass
class EconEvent:
    date: str
    country: str
    event: str
    impact: Optional[str] = None       # "low"/"medium"/"high" (Finnhub) or "high" (FRED curated)
    estimate: Optional[float] = None
    previous: Optional[float] = None
    actual: Optional[float] = None
    time: Optional[str] = None


# --------------------------- Finnhub ---------------------------
class FinnhubCalendar:
    def __init__(self, token: str, http_get: HttpGet = _default_get):
        self.token = token
        self._get = http_get

    def events(self, session_date: str) -> List[EconEvent]:
        url = ("https://finnhub.io/api/v1/calendar/economic?"
               + urllib.parse.urlencode({"from": session_date, "to": session_date,
                                         "token": self.token}))
        resp = self._get(url)
        if not isinstance(resp, dict) or "economicCalendar" not in resp:
            # error payloads look like {"error": "..."} -> treat as unavailable
            raise RuntimeError(resp.get("error", "no economicCalendar in response")
                               if isinstance(resp, dict) else "bad response")
        out: List[EconEvent] = []
        for e in resp.get("economicCalendar") or []:
            if session_date not in str(e.get("time", "")):
                continue
            out.append(EconEvent(
                date=session_date, country=e.get("country", "?"),
                event=e.get("event", "?"), impact=e.get("impact"),
                estimate=e.get("estimate"), previous=e.get("prev"),
                actual=e.get("actual"), time=e.get("time")))
        return out


# --------------------------- FRED ---------------------------
# Curated high-impact US releases (substring match on FRED release_name).
FRED_HIGH_IMPACT = (
    "Consumer Price Index",
    "Employment Situation",         # NFP / unemployment
    "Personal Income and Outlays",  # PCE
    "Gross Domestic Product",
    "Producer Price Index",
    "Advance Monthly Sales for Retail",  # retail sales
    "Federal Open Market",          # FOMC, if present
)


class FredCalendar:
    def __init__(self, api_key: str, http_get: HttpGet = _default_get,
                 high_impact=FRED_HIGH_IMPACT):
        self.api_key = api_key
        self._get = http_get
        self.high_impact = high_impact

    def events(self, session_date: str) -> List[EconEvent]:
        url = ("https://api.stlouisfed.org/fred/releases/dates?"
               + urllib.parse.urlencode({
                   "api_key": self.api_key, "file_type": "json",
                   "realtime_start": session_date, "realtime_end": session_date,
                   "include_release_dates_with_no_data": "false",
                   "sort_order": "asc"}))
        resp = self._get(url)
        if not isinstance(resp, dict) or "release_dates" not in resp:
            raise RuntimeError(resp.get("error_message", "no release_dates")
                               if isinstance(resp, dict) else "bad response")
        out: List[EconEvent] = []
        for r in resp.get("release_dates") or []:
            if r.get("date") != session_date:
                continue
            name = r.get("release_name", "")
            if any(k.lower() in name.lower() for k in self.high_impact):
                out.append(EconEvent(date=session_date, country="US",
                                     event=name, impact="high"))
        return out


# --------------------------- factory ---------------------------
def from_config(cfg, session_date: str) -> Optional[Callable[[], dict]]:
    """Return a provider callable for context_builder, or None if no key set.
    The callable tries Finnhub then FRED and returns a JSON-able dict."""
    backends = []
    if getattr(cfg, "finnhub_api_key", None):
        backends.append(("finnhub", FinnhubCalendar(cfg.finnhub_api_key)))
    if getattr(cfg, "fred_api_key", None):
        backends.append(("fred", FredCalendar(cfg.fred_api_key)))
    if not backends:
        return None

    def provider() -> dict:
        errors = []
        for name, backend in backends:
            try:
                evs = backend.events(session_date)
                return {"source": name, "session_date": session_date,
                        "count": len(evs),
                        "events": [asdict(e) for e in evs],
                        "note": ("high-impact US releases scheduled today"
                                 if name == "fred" else
                                 "events with impact/estimate/actual")
                        + ("" if evs else " — none today")}
            except Exception as e:
                errors.append(f"{name}: {e}")
        return {"source": "none", "session_date": session_date, "events": [],
                "errors": errors,
                "note": "no calendar backend returned data — treat as unknown event risk"}

    return provider
