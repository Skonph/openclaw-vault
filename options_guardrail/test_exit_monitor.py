"""
Tests for the exit monitor (no IBKR / network).

Run:  pytest -q
"""

from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import pytest

from schema import TradePlan
from guardrail import Guardrail
from risk_policy import MODERATE
from state import AccountState, _week_key
from positions import Position, PositionStore
from market_data import MockMarketData
from exit_monitor import ExitMonitor, ExitConfig, ExitAction, ExitCode


# Derive date keys from the SAME clock the engine rolls on (UTC), so tests are
# timezone-independent. Using date.today() here breaks on non-UTC servers because
# roll_periods() compares against datetime.utcnow().
_UTC_TODAY = datetime.utcnow().date()


def _state(equity=100_000.0):
    return AccountState(
        equity=equity, day_anchor_equity=equity, week_anchor_equity=equity,
        day_key=_UTC_TODAY.isoformat(), week_key=_week_key(_UTC_TODAY),
        open_positions=1, deployed_usd=2000.0,
    )


def _plan(**over):
    base = dict(
        plan_id="p1", symbol="SPY", structure="debit_call_spread",
        legs=[
            {"symbol": "SPY", "expiry": "2026-06-19", "strike": 535, "right": "C", "side": "BUY"},
            {"symbol": "SPY", "expiry": "2026-06-19", "strike": 540, "right": "C", "side": "SELL"},
        ],
        thesis="continuation", net_price=2.10,
        max_loss_usd=2000.0, requested_qty=10, target_profit_usd=3000.0,
        invalidation={"kind": "underlying_below", "value": 531.0},
    )
    base.update(over)
    return TradePlan.from_dict(base)


def _position_from(plan, equity=100_000.0):
    g = Guardrail(MODERATE)
    res = g.evaluate(plan, _state(equity))
    assert res.tradeable
    return Position.from_execution(plan, res, entry_net_price=plan.net_price), res


def _monitor(tmp_path, store, market, state, config=ExitConfig()):
    return ExitMonitor(store, market, state, tmp_path / "state.json", config=config)


# ---------- scaling on entry ----------
def test_position_scales_risk_to_approved_qty(tmp_path):
    # 10 units @ $500/unit = $5,000 -> resized to 4 by 2% cap on $100k.
    plan = _plan(max_loss_usd=5000.0, requested_qty=10, target_profit_usd=10000.0)
    pos, res = _position_from(plan)
    assert res.approved_qty == 4
    assert pos.qty == 4
    assert pos.max_loss_usd == pytest.approx(2000.0)       # 4 * $500
    assert pos.target_profit_usd == pytest.approx(4000.0)  # 4 * $1000/unit


# ---------- invalidation: underlying ----------
def test_underlying_below_triggers_close(tmp_path):
    plan = _plan(max_loss_usd=1000.0, requested_qty=5)
    pos, _ = _position_from(plan)
    store = PositionStore(tmp_path / "pos.json"); store.add(pos)
    market = MockMarketData(prices={"SPY": 530.0}, pnls={"p1": -300.0})  # below 531
    mon = _monitor(tmp_path, store, market, _state())
    d = mon.evaluate(pos)
    assert d.action == ExitAction.CLOSE and d.code == ExitCode.INVALIDATION


def test_underlying_above_holds_when_safe(tmp_path):
    plan = _plan(max_loss_usd=1000.0, requested_qty=5)
    pos, _ = _position_from(plan)
    market = MockMarketData(prices={"SPY": 537.0}, pnls={"p1": 200.0})  # above 531
    mon = _monitor(tmp_path, PositionStore(tmp_path / "p.json"), market, _state())
    d = mon.evaluate(pos)
    assert d.action == ExitAction.HOLD


def test_underlying_above_invalidation(tmp_path):
    plan = _plan(invalidation={"kind": "underlying_above", "value": 545.0},
                 max_loss_usd=1000.0, requested_qty=5)
    pos, _ = _position_from(plan)
    market = MockMarketData(prices={"SPY": 546.0}, pnls={"p1": 0.0})
    mon = _monitor(tmp_path, PositionStore(tmp_path / "p.json"), market, _state())
    assert mon.evaluate(pos).code == ExitCode.INVALIDATION


# ---------- invalidation: IV + time ----------
def test_iv_above_invalidation(tmp_path):
    plan = _plan(invalidation={"kind": "iv_above", "value": 0.35},
                 max_loss_usd=1000.0, requested_qty=5)
    pos, _ = _position_from(plan)
    market = MockMarketData(prices={"SPY": 537.0}, ivs={"SPY": 0.40}, pnls={"p1": 0.0})
    mon = _monitor(tmp_path, PositionStore(tmp_path / "p.json"), market, _state())
    assert mon.evaluate(pos).code == ExitCode.INVALIDATION


def test_time_stop_invalidation(tmp_path):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    plan = _plan(invalidation={"kind": "time_stop", "value": past},
                 max_loss_usd=1000.0, requested_qty=5)
    pos, _ = _position_from(plan)
    market = MockMarketData(prices={"SPY": 537.0}, pnls={"p1": 50.0})
    mon = _monitor(tmp_path, PositionStore(tmp_path / "p.json"), market, _state())
    assert mon.evaluate(pos).code == ExitCode.INVALIDATION


# ---------- stop + take profit ----------
def test_stop_loss_triggers(tmp_path):
    plan = _plan(max_loss_usd=1000.0, requested_qty=5)  # pos max loss $1000
    pos, _ = _position_from(plan)
    # stop fraction 0.85 -> stop at -$850. Price safe (above 531) so only stop can fire.
    market = MockMarketData(prices={"SPY": 537.0}, pnls={"p1": -900.0})
    mon = _monitor(tmp_path, PositionStore(tmp_path / "p.json"), market, _state())
    d = mon.evaluate(pos)
    assert d.action == ExitAction.CLOSE and d.code == ExitCode.STOP


def test_take_profit_triggers(tmp_path):
    plan = _plan(max_loss_usd=1000.0, requested_qty=5, target_profit_usd=1500.0)
    pos, _ = _position_from(plan)
    market = MockMarketData(prices={"SPY": 538.0}, pnls={"p1": 1600.0})
    mon = _monitor(tmp_path, PositionStore(tmp_path / "p.json"), market, _state())
    d = mon.evaluate(pos)
    assert d.action == ExitAction.CLOSE and d.code == ExitCode.TAKE_PROFIT


def test_invalidation_precedes_take_profit(tmp_path):
    # In profit but underlying broke the invalidation -> still close as INVALIDATION.
    plan = _plan(max_loss_usd=1000.0, requested_qty=5, target_profit_usd=100.0)
    pos, _ = _position_from(plan)
    market = MockMarketData(prices={"SPY": 530.0}, pnls={"p1": 500.0})
    mon = _monitor(tmp_path, PositionStore(tmp_path / "p.json"), market, _state())
    assert mon.evaluate(pos).code == ExitCode.INVALIDATION


# ---------- run_once + state feedback ----------
def test_run_once_books_pnl_and_frees_capital(tmp_path):
    plan = _plan(max_loss_usd=1000.0, requested_qty=5)
    pos, _ = _position_from(plan)
    store = PositionStore(tmp_path / "pos.json"); store.add(pos)
    state = _state(equity=100_000.0)
    state.deployed_usd = pos.max_loss_usd
    market = MockMarketData(prices={"SPY": 530.0}, pnls={"p1": -400.0})  # invalidated, -$400
    closed = {"hit": False}
    mon = ExitMonitor(store, market, state, tmp_path / "state.json",
                      closer=lambda p, pnl: closed.__setitem__("hit", True))
    decisions = mon.run_once()

    assert decisions[0].action == ExitAction.CLOSE
    assert closed["hit"] is True                       # broker closer called
    assert store.open_positions() == []                # no longer open
    assert state.equity == pytest.approx(99_600.0)     # 100k - 400 realized
    assert state.open_positions == 0
    assert state.deployed_usd == pytest.approx(0.0)


def test_killswitch_arms_after_losses(tmp_path):
    # Two losing closes drive day P&L past -5% -> guardrail would now HALT.
    state = _state(equity=100_000.0)
    state.open_positions = 2
    store = PositionStore(tmp_path / "pos.json")
    for pid in ("a", "b"):
        plan = _plan(plan_id=pid, max_loss_usd=3000.0, requested_qty=10)
        pos, _ = _position_from(plan)  # resized to $2000 max loss
        store.add(pos)
    market = MockMarketData(prices={"SPY": 530.0},
                            pnls={"a": -2700.0, "b": -2700.0})  # -5,400 total
    mon = _monitor(tmp_path, store, market, state)
    mon.run_once()
    assert state.day_drawdown_pct <= -0.05
    # confirm the guardrail now halts new entries
    new = _plan(plan_id="c", max_loss_usd=150.0, requested_qty=1)
    res = Guardrail(MODERATE).evaluate(new, state)
    from guardrail import Decision
    assert res.decision == Decision.HALTED


def test_position_persists_legs():
    plan = _plan()
    pos, res = _position_from(plan)
    assert len(pos.legs) == 2
    assert pos.legs[0]["symbol"] == "SPY"
    assert pos.legs[0]["strike"] == 535
    assert pos.legs[1]["strike"] == 540

    legs_obj = pos.legs_obj
    assert len(legs_obj) == 2
    assert legs_obj[0].symbol == "SPY"
    assert legs_obj[0].strike == 535


def test_executor_close_supports_position():
    class MockIB:
        def __init__(self):
            self.placed = []
        def qualifyContracts(self, contract):
            contract.conId = 12345
            return (contract,)
        def placeOrder(self, contract, order):
            self.placed.append((contract, order))
            class FakeTrade:
                order = type('FakeOrder', (), {'orderId': 999})
                orderStatus = type('FakeStatus', (), {'status': 'Submitted'})
            return FakeTrade()
        def sleep(self, seconds):
            pass

    from ibkr_paper_executor import IBKRPaperExecutor
    exec = IBKRPaperExecutor()
    exec._ib = MockIB()
    exec.paper_only = False

    plan = _plan()
    pos, res = _position_from(plan)

    rep = exec.close_position(pos, pos.qty)
    assert rep.submitted is True
    assert rep.broker_order_id == 999
    assert len(exec._ib.placed) == 1
    contract, order = exec._ib.placed[0]
    assert contract.secType == "BAG"
    assert len(contract.comboLegs) == 2
    assert contract.comboLegs[0].conId == 12345
    assert contract.comboLegs[1].conId == 12345


def test_marked_drawdown_killswitch_closes_all_positions(tmp_path):
    state = _state(equity=100_000.0)
    state.open_positions = 2
    store = PositionStore(tmp_path / "pos.json")

    for pid in ("a", "b"):
        plan = _plan(plan_id=pid, max_loss_usd=2000.0, requested_qty=10)
        pos, _ = _position_from(plan)
        store.add(pos)

    # Open paper losses of -$5,500 total (> -5% daily limit of 100k)
    market = MockMarketData(prices={"SPY": 535.0},
                            pnls={"a": -2800.0, "b": -2700.0})

    config = ExitConfig(use_marked_drawdown=True)
    mon = _monitor(tmp_path, store, market, state, config=config)

    decisions = mon.run_once()
    assert len(decisions) == 2
    assert decisions[0].action == ExitAction.CLOSE
    assert decisions[0].code == ExitCode.STOP
    assert "MARKED DAILY KILL-SWITCH BREACHED" in decisions[0].reason
    assert decisions[1].action == ExitAction.CLOSE
    assert decisions[1].code == ExitCode.STOP
    assert "MARKED DAILY KILL-SWITCH BREACHED" in decisions[1].reason

    assert len(store.open_positions()) == 0

    # Confirm guardrail halts new entries
    new = _plan(plan_id="c", max_loss_usd=150.0, requested_qty=1)
    res = Guardrail(MODERATE).evaluate(new, state)
    from guardrail import Decision
    assert res.decision == Decision.HALTED
