"""
Unit tests for IBKRPaperExecutor P3 changes (_get_combo_mid + its use in
execute() and close_position()).

Run:  pytest test_ibkr_paper_executor.py -q
No IBKR / network required — MockIB is injected directly.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Stub out ib_async BEFORE any ibkr_paper_executor import so the lazy
# `from ib_async import ...` calls inside execute() / close_position() /
# _build_combo() resolve to our lightweight fakes.
# ---------------------------------------------------------------------------
import sys
import types

_ib_async_mod = types.ModuleType("ib_async")


class LimitOrder:
    def __init__(self, action, qty, lmtPrice):
        self.action = action
        self.totalQuantity = qty
        self.lmtPrice = lmtPrice


class MarketOrder:
    def __init__(self, action, qty):
        self.action = action
        self.totalQuantity = qty


class _Contract:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _ComboLeg:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _Option:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


_ib_async_mod.LimitOrder = LimitOrder
_ib_async_mod.MarketOrder = MarketOrder
_ib_async_mod.Contract = _Contract
_ib_async_mod.ComboLeg = _ComboLeg
_ib_async_mod.Option = _Option
_ib_async_mod.Stock = _Contract # Reuse Contract or a similar stub
_ib_async_mod.IB = object  # never called in unit tests

if "ib_async" not in sys.modules:
    sys.modules["ib_async"] = _ib_async_mod
else:
    # Patch in any missing stubs
    existing = sys.modules["ib_async"]
    existing.LimitOrder = LimitOrder
    existing.MarketOrder = MarketOrder
    existing.Contract = _Contract
    existing.ComboLeg = _ComboLeg
    existing.Option = _Option
    existing.Stock = getattr(existing, "Stock", _Contract)
    existing.IB = getattr(existing, "IB", object)


import pytest

from ibkr_paper_executor import IBKRPaperExecutor, ExecutionReport
from schema import TradePlan
from guardrail import Guardrail, GuardrailResult
from risk_policy import MODERATE
from state import AccountState, _week_key
from positions import Position

from datetime import datetime


# ---------------------------------------------------------------------------
# Helpers — mirrors test_exit_monitor._plan() / _state()
# ---------------------------------------------------------------------------

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


def _approved_result(plan):
    g = Guardrail(MODERATE)
    res = g.evaluate(plan, _state())
    assert res.tradeable, f"Plan not tradeable: {res.reasons}"
    return res


def _position_from(plan):
    res = _approved_result(plan)
    return Position.from_execution(plan, res, entry_net_price=plan.net_price), res


# ---------------------------------------------------------------------------
# MockIB / FakeTicker / FakeTrade
# ---------------------------------------------------------------------------

class FakeTicker:
    def __init__(self, bid=None, ask=None, raises=None):
        self._raises = raises
        self.bid = bid
        self.ask = ask


class FakeTrade:
    def __init__(self, orderId=111, status="Submitted"):
        self.order = type("FakeOrder", (), {"orderId": orderId})()
        self.orderStatus = type("FakeStatus", (), {"status": status})()


class MockIB:
    def __init__(self, ticker_bid=None, ticker_ask=None, reqMktData_raises=None):
        self._ticker_bid = ticker_bid
        self._ticker_ask = ticker_ask
        self._reqMktData_raises = reqMktData_raises
        self.placed = []          # list of (contract, order)
        self.mkt_data_calls = []  # list of bag contracts passed to reqMktData

    def qualifyContracts(self, contract):
        contract.conId = 99999
        return (contract,)

    def placeOrder(self, contract, order):
        self.placed.append((contract, order))
        return FakeTrade(orderId=111, status="Submitted")

    def reqMktData(self, bag, genericTickList="", snapshot=False):
        self.mkt_data_calls.append(bag)
        if self._reqMktData_raises is not None:
            raise self._reqMktData_raises
        return FakeTicker(bid=self._ticker_bid, ask=self._ticker_ask)

    def sleep(self, seconds):
        pass  # no-op

    def managedAccounts(self):
        return ["DU999999"]


def _executor(ticker_bid=None, ticker_ask=None, reqMktData_raises=None):
    """Return an IBKRPaperExecutor with a MockIB already injected."""
    exec_ = IBKRPaperExecutor(paper_only=False)
    exec_._ib = MockIB(
        ticker_bid=ticker_bid,
        ticker_ask=ticker_ask,
        reqMktData_raises=reqMktData_raises,
    )
    return exec_


# ---------------------------------------------------------------------------
# execute() tests
# ---------------------------------------------------------------------------

def test_execute_uses_mid_price_from_combo_quote():
    """bid=2.0, ask=3.0 -> mid=2.50; LimitOrder BUY at 2.50."""
    plan = _plan()
    result = _approved_result(plan)
    exec_ = _executor(ticker_bid=2.0, ticker_ask=3.0)

    report = exec_.execute(plan, result)

    assert report.submitted is True
    contract, order = exec_._ib.placed[0]
    assert order.__class__.__name__ == "LimitOrder"
    assert order.lmtPrice == pytest.approx(2.50)
    assert "2.5" in report.detail


def test_execute_falls_back_to_plan_net_price_when_no_mid():
    """bid=0, ask=0 -> invalid mid; falls back to plan.net_price=2.10."""
    plan = _plan(net_price=2.10)
    result = _approved_result(plan)
    exec_ = _executor(ticker_bid=0, ticker_ask=0)

    report = exec_.execute(plan, result)

    contract, order = exec_._ib.placed[0]
    assert order.__class__.__name__ == "LimitOrder"
    assert order.lmtPrice == pytest.approx(2.10)


def test_execute_uses_negative_mid_for_credit_combo():
    """
    bid=-1.20, ask=-1.00 (net-credit combo quote) -> mid=-1.10.
    Credit spreads / Iron Condors can legitimately quote a negative price on
    the BAG; this must NOT be treated as 'no data' and fall back to
    plan.net_price.
    """
    plan = _plan()
    result = _approved_result(plan)
    exec_ = _executor(ticker_bid=-1.20, ticker_ask=-1.00)

    report = exec_.execute(plan, result)

    contract, order = exec_._ib.placed[0]
    assert order.__class__.__name__ == "LimitOrder"
    assert order.lmtPrice == pytest.approx(-1.10)


def test_execute_explicit_limit_price_overrides_mid():
    """Caller supplies limit_price=1.80; mid (2.50) must NOT be used."""
    plan = _plan()
    result = _approved_result(plan)
    exec_ = _executor(ticker_bid=2.0, ticker_ask=3.0)

    report = exec_.execute(plan, result, limit_price=1.80)

    contract, order = exec_._ib.placed[0]
    assert order.__class__.__name__ == "LimitOrder"
    assert order.lmtPrice == pytest.approx(1.80)
    # reqMktData should NOT have been called (limit_price supplied -> skip quote)
    assert len(exec_._ib.mkt_data_calls) == 0


def test_execute_market_order_when_no_price_available():
    """bid/ask invalid AND plan.net_price=None -> MarketOrder."""
    plan = _plan(net_price=None)
    result = _approved_result(plan)
    exec_ = _executor(ticker_bid=0, ticker_ask=0)

    report = exec_.execute(plan, result)

    contract, order = exec_._ib.placed[0]
    assert order.__class__.__name__ == "MarketOrder"


# ---------------------------------------------------------------------------
# close_position() tests
# ---------------------------------------------------------------------------

def test_close_position_uses_mid_price():
    """bid=1.5, ask=2.5 -> mid=2.00; LimitOrder SELL at 2.00."""
    plan = _plan()
    pos, _ = _position_from(plan)
    exec_ = _executor(ticker_bid=1.5, ticker_ask=2.5)

    report = exec_.close_position(pos, pos.qty)

    assert report.submitted is True
    contract, order = exec_._ib.placed[0]
    assert order.__class__.__name__ == "LimitOrder"
    assert order.lmtPrice == pytest.approx(2.00)
    assert "2.0" in report.detail


def test_close_position_explicit_limit_overrides_mid():
    """Caller supplies limit_price=1.00; mid (2.00) must NOT be used."""
    plan = _plan()
    pos, _ = _position_from(plan)
    exec_ = _executor(ticker_bid=1.5, ticker_ask=2.5)

    report = exec_.close_position(pos, pos.qty, limit_price=1.00)

    contract, order = exec_._ib.placed[0]
    assert order.__class__.__name__ == "LimitOrder"
    assert order.lmtPrice == pytest.approx(1.00)
    # reqMktData should NOT have been called
    assert len(exec_._ib.mkt_data_calls) == 0


def test_close_position_falls_back_to_market_when_no_mid():
    """bid/ask invalid AND no limit_price -> MarketOrder SELL."""
    plan = _plan()
    pos, _ = _position_from(plan)
    exec_ = _executor(ticker_bid=0, ticker_ask=0)

    report = exec_.close_position(pos, pos.qty)

    contract, order = exec_._ib.placed[0]
    assert order.__class__.__name__ == "MarketOrder"
    assert "MKT" in report.detail


# ---------------------------------------------------------------------------
# _get_combo_mid() unit tests
# ---------------------------------------------------------------------------

def test_get_combo_mid_handles_negative_bid_ask_for_credit_combo():
    """bid=-1.20, ask=-1.00 -> mid=-1.10 (not None)."""
    exec_ = _executor(ticker_bid=-1.20, ticker_ask=-1.00)

    class FakeBag:
        pass

    result = exec_._get_combo_mid(FakeBag())
    assert result == pytest.approx(-1.10)


def test_get_combo_mid_treats_zero_zero_as_no_data():
    """bid=0, ask=0 -> still treated as 'no data' sentinel -> None."""
    exec_ = _executor(ticker_bid=0, ticker_ask=0)

    class FakeBag:
        pass

    result = exec_._get_combo_mid(FakeBag())
    assert result is None


def test_get_combo_mid_handles_exception_gracefully():
    """reqMktData raises RuntimeError; _get_combo_mid must return None silently."""
    exec_ = _executor(reqMktData_raises=RuntimeError("simulated data error"))

    # Build a minimal fake bag — _get_combo_mid only passes it to reqMktData
    class FakeBag:
        pass

    result = exec_._get_combo_mid(FakeBag())
    assert result is None  # no exception propagated
