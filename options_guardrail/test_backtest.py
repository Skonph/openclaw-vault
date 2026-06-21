"""
Tests for the backtest harness (deterministic, no network).

Run:  pytest -q
"""

import math
from datetime import datetime

import pytest

from bs import bs_price
from schema import TradePlan
from backtest_data import BacktestMarketData
from backtest import Backtester, BacktestResult, gbm_paths
from positions import Position


# ---------------- Black-Scholes sanity ----------------
def test_bs_intrinsic_at_expiry():
    assert bs_price(110, 100, 0.0, 0.04, 0.2, "C") == pytest.approx(10.0)
    assert bs_price(90, 100, 0.0, 0.04, 0.2, "C") == 0.0
    assert bs_price(90, 100, 0.0, 0.04, 0.2, "P") == pytest.approx(10.0)


def test_bs_call_put_parity():
    S, K, T, r, sig = 100, 100, 0.5, 0.04, 0.25
    c = bs_price(S, K, T, r, sig, "C")
    p = bs_price(S, K, T, r, sig, "P")
    # c - p = S - K e^{-rT}
    assert (c - p) == pytest.approx(S - K * math.exp(-r * T), abs=1e-6)


def test_bs_call_value_rises_with_spot():
    a = bs_price(100, 100, 0.3, 0.04, 0.2, "C")
    b = bs_price(105, 100, 0.3, 0.04, 0.2, "C")
    assert b > a


# ---------------- combo marking ----------------
def test_debit_call_spread_pnl_moves_right_way():
    md = BacktestMarketData(r=0.04, default_iv=0.20)
    now = datetime(2026, 1, 5, 16, 0)
    plan = TradePlan.from_dict({
        "plan_id": "x", "symbol": "SPY", "structure": "debit_call_spread",
        "legs": [
            {"symbol": "SPY", "expiry": "2026-02-20", "strike": 500, "right": "C", "side": "BUY"},
            {"symbol": "SPY", "expiry": "2026-02-20", "strike": 505, "right": "C", "side": "SELL"},
        ],
        "thesis": "t", "max_loss_usd": 1.0, "requested_qty": 1,
        "invalidation": {"kind": "underlying_below", "value": 490},
    })
    md.set_state(now, {"SPY": 500.0})
    entry = md.register(plan)
    assert entry > 0  # net debit

    pos = Position(plan_id="x", symbol="SPY", structure="debit_call_spread",
                   qty=10, entry_net_price=entry, max_loss_usd=entry * 1000,
                   target_profit_usd=None, invalidation=None,
                   opened_at=now.isoformat())

    # spot flat -> ~0 pnl (minus a touch of decay)
    assert md.position_pnl(pos) == pytest.approx(0.0, abs=1.0)
    # spot up -> positive pnl for a call debit spread
    md.set_state(now, {"SPY": 504.0})
    assert md.position_pnl(pos) > 0
    # spot down -> negative
    md.set_state(now, {"SPY": 496.0})
    assert md.position_pnl(pos) < 0


# ---------------- gbm determinism ----------------
def test_gbm_is_seeded():
    d1, p1 = gbm_paths({"SPY": 500.0}, 30, datetime(2026, 1, 5), seed=42)
    d2, p2 = gbm_paths({"SPY": 500.0}, 30, datetime(2026, 1, 5), seed=42)
    assert p1["SPY"] == p2["SPY"]


# ---------------- full run ----------------
def test_backtest_runs_and_reports():
    bt = Backtester(symbols=["SPY", "QQQ"], spot0={"SPY": 500.0, "QQQ": 440.0},
                    days=120, decision_every=5, seed=11)
    res = bt.run()
    assert isinstance(res, BacktestResult)
    assert res.n_trades > 0
    assert 0.0 <= res.win_rate <= 1.0
    # every trade has a realized P&L and a close reason
    assert all(t.realized_pnl_usd is not None for t in res.trades)
    assert all(t.close_reason for t in res.trades)
    # equity is internally consistent: start + sum(realized) == final
    assert res.starting_equity + sum(res.pnls) == pytest.approx(res.final_equity, abs=1e-6)
    # drawdown is non-positive
    assert res.max_drawdown <= 0.0
    # summary renders
    assert "win rate" in res.summary()


def test_per_trade_risk_respected():
    # No single realized LOSS should exceed the 2% per-trade cap by much.
    # (Marks can gap past a stop intraday in reality; in this daily BS sim the
    #  stop/invalidation bound losses near the defined max.)
    bt = Backtester(symbols=["SPY"], spot0={"SPY": 500.0}, days=150,
                    decision_every=5, seed=3)
    res = bt.run()
    cap = bt.starting_equity * bt.policy.max_loss_per_trade_pct
    worst = min(res.pnls) if res.pnls else 0.0
    # allow a modest buffer for one-day overshoot past the stop level
    assert worst >= -cap * 1.5
