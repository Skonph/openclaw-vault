"""
Tests for flatten_all + context_builder (offline).

Run:  pytest -q
"""

from datetime import date

import pytest

from state import AccountState
from positions import Position, PositionStore
from market_data import MockMarketData
from flatten_all import flatten
from context_builder import build_context, account_from_local, NOT_CONFIGURED


def _state(equity=100_000.0):
    return AccountState(equity=equity, day_anchor_equity=equity,
                        week_anchor_equity=equity, day_key=date.today().isoformat(),
                        week_key="2026-W23", open_positions=0, deployed_usd=0.0)


def _pos(plan_id, max_loss=1000.0):
    return Position(plan_id=plan_id, symbol="SPY", structure="debit_call_spread",
                    qty=5, entry_net_price=2.0, max_loss_usd=max_loss,
                    target_profit_usd=1500.0, invalidation=None,
                    opened_at="2026-06-01T14:00:00+00:00")


# ----------------------------- flatten -----------------------------
def test_flatten_closes_all_and_books_pnl(tmp_path):
    store = PositionStore(tmp_path / "p.json")
    store.add(_pos("A")); store.add(_pos("B"))
    state = _state(); state.open_positions = 2; state.deployed_usd = 2000.0
    market = MockMarketData(pnls={"A": 300.0, "B": -150.0})
    closed_ids = []
    rep = flatten(store, state, market, state_path=None,
                  closer=lambda pos, pnl: closed_ids.append(pos.plan_id))

    assert set(rep.closed) == {"A", "B"}
    assert rep.total_pnl == pytest.approx(150.0)
    assert set(closed_ids) == {"A", "B"}            # broker closer called for each
    assert store.open_positions() == []
    assert state.equity == pytest.approx(100_150.0)
    assert state.open_positions == 0
    assert state.deployed_usd == pytest.approx(0.0)


def test_flatten_is_idempotent_when_flat(tmp_path):
    store = PositionStore(tmp_path / "p.json")
    rep = flatten(store, _state(), MockMarketData(), state_path=None)
    assert rep.closed == [] and rep.total_pnl == 0.0


def test_flatten_records_errors_without_stranding(tmp_path):
    store = PositionStore(tmp_path / "p.json")
    store.add(_pos("A")); store.add(_pos("B"))
    state = _state(); state.open_positions = 2

    class PartialMarket(MockMarketData):
        def position_pnl(self, position):
            if position.plan_id == "A":
                raise RuntimeError("no quote")
            return 100.0

    rep = flatten(store, state, PartialMarket(), state_path=None)
    assert "A" in rep.errors[0]
    assert rep.closed == ["B"]                       # B still closed despite A failing


# ----------------------------- context builder -----------------------------
def test_account_from_local_snapshot(tmp_path):
    store = PositionStore(tmp_path / "p.json")
    store.add(_pos("A"))
    state = _state(); state.open_positions = 1
    acct = account_from_local(state, store)
    assert acct["equity"] == 100_000.0
    assert acct["open_positions"][0]["plan_id"] == "A"


def test_build_context_marks_unconfigured_feeds():
    ctx = build_context(session_date="2026-06-02", watchlist=["SPY"],
                        account={"equity": 100_000})
    assert ctx["overnight_flow"] == NOT_CONFIGURED
    assert ctx["iv"] == NOT_CONFIGURED
    assert ctx["economic_calendar"] == NOT_CONFIGURED
    assert ctx["watchlist"] == ["SPY"]


def test_build_context_uses_providers():
    ctx = build_context(
        session_date="2026-06-02", watchlist=["SPY"], account={"equity": 1},
        overnight_flow_provider=lambda: "ES +0.4% above VWAP",
        iv_provider=lambda: {"vix": 14.2, "iv_rank": 0.25},
        calendar_provider=lambda: "10:00 ET ISM",
    )
    assert ctx["overnight_flow"] == "ES +0.4% above VWAP"
    assert ctx["iv"]["vix"] == 14.2
    assert "ISM" in ctx["economic_calendar"]


def test_build_context_provider_error_is_contained():
    def boom():
        raise ValueError("feed down")
    ctx = build_context(session_date="2026-06-02", watchlist=["SPY"],
                        account={}, iv_provider=boom)
    assert "provider error" in ctx["iv"]
