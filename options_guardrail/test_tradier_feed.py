"""
Tests for the Tradier feed (offline, mock HTTP).

Run:  pytest -q
"""

import pytest

from tradier_feed import TradierClient, make_tradier_providers, _as_list


class FakeTradier:
    """Serves canned Tradier responses keyed by endpoint substring."""
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params):
        self.calls.append((url, params))
        for key, resp in self.routes.items():
            if key in url:
                return resp
        return {}


def _client(routes):
    fake = FakeTradier(routes)
    return TradierClient("tok", "https://sandbox.tradier.com/v1", http_get=fake.get), fake


def test_as_list_normalizes():
    assert _as_list(None) == []
    assert _as_list({"a": 1}) == [{"a": 1}]
    assert _as_list([1, 2]) == [1, 2]


def test_quote_summary_computes_change():
    c, _ = _client({"markets/quotes": {"quotes": {"quote": [
        {"symbol": "SPY", "last": 535.0, "prevclose": 530.0, "bid": 534.9, "ask": 535.1},
        {"symbol": "QQQ", "last": 440.0, "prevclose": 444.0},
    ]}}})
    s = c.quote_summary(["SPY", "QQQ"])
    assert s["SPY"]["change_pct"] == pytest.approx(0.0094, abs=1e-3)
    assert s["QQQ"]["change_pct"] == pytest.approx(-0.009, abs=1e-3)


def test_quote_summary_single_result_normalized():
    # Tradier returns a dict (not list) for a single symbol
    c, _ = _client({"markets/quotes": {"quotes": {"quote":
        {"symbol": "SPY", "last": 500.0, "prevclose": 500.0}}}})
    s = c.quote_summary(["SPY"])
    assert s["SPY"]["change_pct"] == pytest.approx(0.0)


def test_atm_iv_picks_nearest_strike():
    routes = {
        "expirations": {"expirations": {"date": ["2026-06-19", "2026-07-17"]}},
        "markets/quotes": {"quotes": {"quote": {"symbol": "SPY", "last": 502.0,
                                                "prevclose": 500.0}}},
        "options/chains": {"options": {"option": [
            {"strike": 495, "greeks": {"mid_iv": 0.20}},
            {"strike": 500, "greeks": {"mid_iv": 0.18}},   # nearest to 502
            {"strike": 510, "greeks": {"mid_iv": 0.22}},
        ]}},
    }
    c, _ = _client(routes)
    assert c.atm_iv("SPY") == pytest.approx(0.18)


def test_atm_iv_none_when_no_expirations():
    c, _ = _client({"expirations": {"expirations": {"date": None}},
                    "markets/quotes": {"quotes": {"quote": {"symbol": "SPY",
                                                            "last": 500}}}})
    assert c.atm_iv("SPY") is None


def test_providers_shape_and_safety():
    routes = {
        "markets/quotes": {"quotes": {"quote": {"symbol": "SPY", "last": 500.0,
                                                "prevclose": 498.0}}},
        "expirations": {"expirations": {"date": ["2026-06-19"]}},
        "options/chains": {"options": {"option": [
            {"strike": 500, "greeks": {"mid_iv": 0.19}}]}},
        "markets/clock": {"clock": {"state": "open", "description": "Market is open"}},
    }
    c, _ = _client(routes)
    p = make_tradier_providers(c, ["SPY"])
    assert p["flow"]()["quotes"]["SPY"]["change_pct"] == pytest.approx(0.004, abs=1e-3)
    assert p["iv"]()["atm_iv"]["SPY"] == pytest.approx(0.19)
    assert p["calendar"]()["state"] == "open"


def test_iv_provider_contains_errors():
    class Boom(TradierClient):
        def atm_iv(self, symbol, expiration=None):
            raise RuntimeError("feed down")
    c = Boom("tok", "https://x", http_get=lambda u, p: {})
    p = make_tradier_providers(c, ["SPY"])
    assert "error" in str(p["iv"]()["atm_iv"]["SPY"])
