"""
Tests for the session orchestrator (offline, mock market data).

Run:  pytest -q
"""

from datetime import date

import pytest

from state import AccountState
from positions import PositionStore
from market_data import MockMarketData
from pipeline import SessionOrchestrator


def _state(equity=100_000.0):
    return AccountState(
        equity=equity, day_anchor_equity=equity, week_anchor_equity=equity,
        day_key=date.today().isoformat(), week_key="2026-W22",
    )


STRAT = """{
  "session_date":"2026-06-01","regime":"trend","reasoning":"ES held VWAP.",
  "no_trade":false,
  "plans":[
    {"plan_id":"SPY-1","symbol":"SPY","structure":"debit_call_spread","thesis":"cont",
     "legs":[{"symbol":"SPY","expiry":"2026-06-19","strike":535,"right":"C","side":"BUY"},
             {"symbol":"SPY","expiry":"2026-06-19","strike":540,"right":"C","side":"SELL"}],
     "net_price":2.10,"max_loss_usd":1000.0,"target_profit_usd":1500.0,"requested_qty":5,
     "invalidation":{"kind":"underlying_below","value":531.0}},
    {"plan_id":"BAD-naked","symbol":"TSLA","structure":"naked_put","thesis":"wheel",
     "legs":[{"symbol":"TSLA","expiry":"2026-06-19","strike":300,"right":"P","side":"SELL"}],
     "max_loss_usd":500.0,"requested_qty":1,
     "invalidation":{"kind":"underlying_below","value":290.0}}
  ]
}"""


def _orch(market):
    return SessionOrchestrator(market, _state(), PositionStore(None), executor=None)


def test_opens_only_approved_plans():
    market = MockMarketData(prices={"SPY": 536.0}, pnls={"SPY-1": 0.0})
    orch = _orch(market)
    env, rep = orch.open_from_strategist(STRAT)
    assert rep.opened == ["SPY-1"]          # naked put rejected by guardrail
    assert "BAD-naked" in rep.skipped
    assert orch.state.open_positions == 1


def test_session_closes_on_invalidation_and_books_pnl():
    market = MockMarketData(prices={"SPY": 536.0}, pnls={"SPY-1": -200.0})
    orch = _orch(market)
    orch.open_from_strategist(STRAT)
    # market turns against the position -> invalidation
    market.set_price("SPY", 530.0)
    orch.run_session(max_ticks=3)
    assert orch.store.open_positions() == []
    assert orch.state.equity == pytest.approx(99_800.0)   # 100k - 200
    assert orch.state.open_positions == 0


def test_session_take_profit():
    market = MockMarketData(prices={"SPY": 538.0}, pnls={"SPY-1": 1600.0})
    orch = _orch(market)
    orch.open_from_strategist(STRAT)
    orch.run_session(max_ticks=2)
    closed = [p for p in orch.store.all() if not p.is_open]
    assert closed and closed[0].close_reason == "TAKE_PROFIT"


def test_live_mode_calls_executor_close():
    # Fake executor records execute/close calls; market drives an invalidation.
    class FakeExec:
        def __init__(self):
            self.opened, self.closed = [], []

        def execute(self, plan, result):
            self.opened.append(plan.plan_id)
            from ibkr_paper_executor import ExecutionReport
            return ExecutionReport(submitted=True, plan_id=plan.plan_id,
                                   qty=result.approved_qty, status="Filled")

        def close_position(self, plan, qty):
            self.closed.append(plan.plan_id)

    market = MockMarketData(prices={"SPY": 536.0}, pnls={"SPY-1": -100.0})
    fake = FakeExec()
    orch = SessionOrchestrator(market, _state(), PositionStore(None), executor=fake)
    orch.open_from_strategist(STRAT)
    assert fake.opened == ["SPY-1"]
    market.set_price("SPY", 530.0)        # break invalidation
    orch.run_session(max_ticks=2)
    assert fake.closed == ["SPY-1"]
