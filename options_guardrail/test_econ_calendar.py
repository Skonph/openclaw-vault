"""
Tests for the economic-calendar provider (offline, mock HTTP).

Run:  pytest -q
"""

from econ_calendar import (FinnhubCalendar, FredCalendar, from_config, EconEvent)


# --------------------------- Finnhub ---------------------------
def test_finnhub_parses_and_filters_by_date():
    resp = {"economicCalendar": [
        {"time": "2026-06-02 12:30:00", "country": "US", "event": "CPI MoM",
         "impact": "high", "estimate": 0.3, "prev": 0.2, "actual": None},
        {"time": "2026-06-03 12:30:00", "country": "US", "event": "PPI",
         "impact": "medium"},   # wrong day -> filtered out
    ]}
    c = FinnhubCalendar("tok", http_get=lambda url: resp)
    evs = c.events("2026-06-02")
    assert len(evs) == 1
    assert evs[0].event == "CPI MoM" and evs[0].impact == "high"


def test_finnhub_error_payload_raises():
    c = FinnhubCalendar("tok", http_get=lambda url: {"error": "no access"})
    try:
        c.events("2026-06-02")
        assert False, "should raise"
    except RuntimeError as e:
        assert "no access" in str(e)


# --------------------------- FRED ---------------------------
def test_fred_filters_high_impact_for_date():
    resp = {"release_dates": [
        {"release_id": 10, "release_name": "Consumer Price Index", "date": "2026-06-02"},
        {"release_id": 50, "release_name": "Sheep and Goats Inventory", "date": "2026-06-02"},  # not high-impact
        {"release_id": 11, "release_name": "Employment Situation", "date": "2026-06-05"},  # wrong day
    ]}
    c = FredCalendar("key", http_get=lambda url: resp)
    evs = c.events("2026-06-02")
    assert len(evs) == 1
    assert evs[0].event == "Consumer Price Index" and evs[0].impact == "high"


def test_fred_no_releases_returns_empty():
    c = FredCalendar("key", http_get=lambda url: {"release_dates": []})
    assert c.events("2026-06-02") == []


# --------------------------- factory + fallback ---------------------------
class _Cfg:
    finnhub_api_key = "fk"
    fred_api_key = "rk"


def test_factory_prefers_finnhub(monkeypatch):
    import econ_calendar as ec

    monkeypatch.setattr(ec.FinnhubCalendar, "events",
                        lambda self, d: [EconEvent(date=d, country="US",
                                                   event="CPI", impact="high")])
    monkeypatch.setattr(ec.FredCalendar, "events",
                        lambda self, d: (_ for _ in ()).throw(AssertionError("should not call FRED")))
    prov = from_config(_Cfg(), "2026-06-02")
    out = prov()
    assert out["source"] == "finnhub" and out["count"] == 1


def test_factory_falls_back_to_fred(monkeypatch):
    import econ_calendar as ec
    monkeypatch.setattr(ec.FinnhubCalendar, "events",
                        lambda self, d: (_ for _ in ()).throw(RuntimeError("403")))
    monkeypatch.setattr(ec.FredCalendar, "events",
                        lambda self, d: [EconEvent(date=d, country="US",
                                                   event="Employment Situation", impact="high")])
    prov = from_config(_Cfg(), "2026-06-02")
    out = prov()
    assert out["source"] == "fred" and out["events"][0]["event"] == "Employment Situation"


def test_factory_none_when_no_keys():
    class C:
        finnhub_api_key = None
        fred_api_key = None
    assert from_config(C(), "2026-06-02") is None


def test_factory_reports_all_errors(monkeypatch):
    import econ_calendar as ec
    monkeypatch.setattr(ec.FinnhubCalendar, "events",
                        lambda self, d: (_ for _ in ()).throw(RuntimeError("403")))
    monkeypatch.setattr(ec.FredCalendar, "events",
                        lambda self, d: (_ for _ in ()).throw(RuntimeError("500")))
    out = from_config(_Cfg(), "2026-06-02")()
    assert out["source"] == "none" and len(out["errors"]) == 2
