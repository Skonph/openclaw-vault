"""
Unit tests for IBKRMarketData.position_pnl and IBKRMarketData.implied_vol.

All tests are fully offline — no IBKR connection required. A MockIB class
stands in for the real IB object, returning pre-programmed FakeTicker results.
"""

from __future__ import annotations

import math
import sys
import types
import pytest
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Stub out ib_async before market_data is imported (or before its lazy
# `from ib_async import ...` lines execute in test runs).  The real package is
# only available on machines with a live IBKR installation; our MockIB below
# replaces every runtime call, so we just need the import to succeed.
# ---------------------------------------------------------------------------

def _build_fake_ib_async() -> types.ModuleType:
    mod = types.ModuleType("ib_async")

    class Stock:
        def __init__(self, symbol: str, exchange: str = "SMART", currency: str = "USD"):
            self.symbol = symbol
            self.exchange = exchange
            self.currency = currency
            self.conId: int = 0

    class Option:
        def __init__(
            self,
            symbol: str = "",
            lastTradeDateOrContractMonth: str = "",
            strike: float = 0.0,
            right: str = "C",
            exchange: str = "SMART",
            currency: str = "USD",
        ):
            self.symbol = symbol
            self.lastTradeDateOrContractMonth = lastTradeDateOrContractMonth
            self.strike = strike
            self.right = right
            self.exchange = exchange
            self.currency = currency
            self.conId: int = 0

    mod.Stock = Stock  # type: ignore[attr-defined]
    mod.Option = Option  # type: ignore[attr-defined]
    return mod


# Always ensure our Stock/Option stubs are present — another test file may have
# registered an ib_async stub that omits Stock (e.g. test_ibkr_paper_executor).
_fake_ib_mod = _build_fake_ib_async()
if "ib_async" not in sys.modules:
    sys.modules["ib_async"] = _fake_ib_mod
else:
    # Patch in any names our tests need that may be absent from the existing stub
    existing = sys.modules["ib_async"]
    existing.Stock = _fake_ib_mod.Stock  # type: ignore[attr-defined]
    existing.Option = _fake_ib_mod.Option  # type: ignore[attr-defined]


from schema import TradePlan
from guardrail import Guardrail
from risk_policy import MODERATE
from state import AccountState
from positions import Position
from market_data import IBKRMarketData


# ---------------------------------------------------------------------------
# Fake data structures
# ---------------------------------------------------------------------------

@dataclass
class FakeTicker:
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    close: float = 0.0
    impliedVolatility: Optional[float] = None

    def marketPrice(self) -> float:
        """Mimic ib_async Ticker.marketPrice() — return last if valid, else NaN."""
        if self.last and self.last == self.last and self.last > 0:
            return self.last
        return float("nan")


@dataclass
class FakeContract:
    symbol: str
    conId: int = 0


@dataclass
class FakePortfolioItem:
    contract: FakeContract
    unrealizedPNL: float


# ---------------------------------------------------------------------------
# MockIB
# ---------------------------------------------------------------------------

class MockIB:
    """
    Minimal IB mock. Tickers are keyed by conId (set during qualifyContracts).
    Each call to qualifyContracts assigns the next available conId (1, 2, 3, …)
    and returns the contract in a list. reqMktData returns the pre-programmed
    ticker for that conId (or a zero FakeTicker if not found).
    """

    def __init__(
        self,
        tickers: Optional[Dict[int, FakeTicker]] = None,
        portfolio_items: Optional[List[FakePortfolioItem]] = None,
    ):
        # conId -> FakeTicker
        self._tickers: Dict[int, FakeTicker] = tickers or {}
        self._portfolio_items: List[FakePortfolioItem] = portfolio_items or []
        self._next_con_id = 1
        self.qualify_calls: int = 0
        self.req_market_data_calls: int = 0
        self.sleep_calls: List[float] = []

    def qualifyContracts(self, *contracts):
        result = []
        for contract in contracts:
            contract.conId = self._next_con_id
            self._next_con_id += 1
            self.qualify_calls += 1
            result.append(contract)
        return result

    def reqMktData(self, contract, generic_tick_list="", snapshot=False):
        self.req_market_data_calls += 1
        return self._tickers.get(contract.conId, FakeTicker())

    def portfolio(self) -> List[FakePortfolioItem]:
        return list(self._portfolio_items)

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)


# ---------------------------------------------------------------------------
# Plan / Position helpers (mirrors test_tradier_market_data.py pattern)
# ---------------------------------------------------------------------------

def _plan(
    plan_id: str = "p1",
    symbol: str = "SPY",
    net_price: float = 2.10,
    qty: int = 5,
    strike_long: float = 535.0,
    strike_short: float = 540.0,
    expiry: str = "2026-06-19",
) -> TradePlan:
    return TradePlan.from_dict({
        "plan_id": plan_id,
        "symbol": symbol,
        "structure": "debit_call_spread",
        "legs": [
            {"symbol": symbol, "expiry": expiry, "strike": strike_long, "right": "C", "side": "BUY"},
            {"symbol": symbol, "expiry": expiry, "strike": strike_short, "right": "C", "side": "SELL"},
        ],
        "thesis": "test",
        "net_price": net_price,
        "max_loss_usd": 1000.0,
        "requested_qty": qty,
        "target_profit_usd": 1500.0,
        "invalidation": {"kind": "underlying_below", "value": 531.0},
    })


def _position(plan: TradePlan) -> Position:
    state = AccountState(
        equity=100000.0,
        day_anchor_equity=100000.0,
        week_anchor_equity=100000.0,
        day_key="2026-06-06",
        week_key="2026-W23",
    )
    g = Guardrail(MODERATE)
    res = g.evaluate(plan, state)
    return Position.from_execution(plan, res, entry_net_price=plan.net_price)


def _position_no_legs(symbol: str = "SPY", plan_id: str = "p-nolegs") -> Position:
    """A position with an empty legs list for the no-legs fallback path."""
    return Position(
        plan_id=plan_id,
        symbol=symbol,
        structure="unknown",
        qty=1,
        entry_net_price=0.0,
        max_loss_usd=500.0,
        target_profit_usd=None,
        invalidation=None,
        opened_at="2026-06-06T00:00:00+00:00",
        legs=[],
    )


# ---------------------------------------------------------------------------
# Tests: position_pnl
# ---------------------------------------------------------------------------

class TestIBKRPositionPnl:

    def test_ibkr_position_pnl_uses_bid_ask_mid(self):
        """
        Long leg: bid=4.5, ask=5.5 -> mid=5.0
        Short leg: bid=2.5, ask=3.5 -> mid=3.0
        entry=2.10, qty=5
        Expected: (5.0 - 3.0 - 2.10) * 100 * 5 = -0.10 * 500 = -50.0
        """
        plan = _plan(net_price=2.10, qty=5)
        pos = _position(plan)

        tickers = {
            1: FakeTicker(bid=4.5, ask=5.5),   # long leg -> conId=1
            2: FakeTicker(bid=2.5, ask=3.5),   # short leg -> conId=2
        }
        ib = MockIB(tickers=tickers)
        md = IBKRMarketData(ib)

        pnl = md.position_pnl(pos)
        assert pnl == pytest.approx(-50.0)

    def test_ibkr_position_pnl_uses_last_when_bid_ask_unavailable(self):
        """
        bid=0, ask=0 (invalid) -> falls through to last
        Long leg: last=4.8, short leg: last=2.8
        entry=2.0, qty=3
        Expected: (4.8 - 2.8 - 2.0) * 100 * 3 = 0.0 * 300 = 0.0
        """
        plan = _plan(net_price=2.0, qty=3)
        pos = _position(plan)

        tickers = {
            1: FakeTicker(bid=0.0, ask=0.0, last=4.8),   # long leg
            2: FakeTicker(bid=0.0, ask=0.0, last=2.8),   # short leg
        }
        ib = MockIB(tickers=tickers)
        md = IBKRMarketData(ib)

        pnl = md.position_pnl(pos)
        assert pnl == pytest.approx(0.0)

    def test_ibkr_position_pnl_uses_close_fallback(self):
        """
        bid/ask/last all invalid -> falls through to close
        Long leg: close=5.0, short leg: close=3.0
        entry=2.0, qty=2
        Expected: (5.0 - 3.0 - 2.0) * 100 * 2 = 0.0
        """
        plan = _plan(net_price=2.0, qty=2)
        pos = _position(plan)

        nan = float("nan")
        tickers = {
            1: FakeTicker(bid=0.0, ask=0.0, last=0.0, close=5.0),   # long leg
            2: FakeTicker(bid=0.0, ask=0.0, last=0.0, close=3.0),   # short leg
        }
        ib = MockIB(tickers=tickers)
        md = IBKRMarketData(ib)

        pnl = md.position_pnl(pos)
        assert pnl == pytest.approx(0.0)

    def test_ibkr_position_pnl_cross_check_logs_deviation(self, capsys):
        """
        Per-leg P&L = -50.0, but portfolio says unrealizedPNL=100.0.
        diff = 150.0 > 5.0 -> should print diagnostic to stderr containing 'diff='.
        """
        plan = _plan(net_price=2.10, qty=5)
        pos = _position(plan)

        tickers = {
            1: FakeTicker(bid=4.5, ask=5.5),   # long leg mid=5.0
            2: FakeTicker(bid=2.5, ask=3.5),   # short leg mid=3.0
        }
        # per_leg_pnl = -50.0; portfolio shows +100.0 -> diff = 150.0
        portfolio_items = [
            FakePortfolioItem(
                contract=FakeContract(symbol="SPY"),
                unrealizedPNL=100.0,
            )
        ]
        ib = MockIB(tickers=tickers, portfolio_items=portfolio_items)
        md = IBKRMarketData(ib)

        pnl = md.position_pnl(pos)

        # Per-leg is authoritative
        assert pnl == pytest.approx(-50.0)

        err = capsys.readouterr().err
        assert "diff=" in err

    def test_ibkr_position_pnl_no_legs_uses_portfolio(self):
        """
        Position with empty legs list -> fall back to portfolio unrealizedPNL.
        """
        pos = _position_no_legs(symbol="SPY", plan_id="p-nolegs")

        portfolio_items = [
            FakePortfolioItem(
                contract=FakeContract(symbol="SPY"),
                unrealizedPNL=-123.45,
            )
        ]
        ib = MockIB(portfolio_items=portfolio_items)
        md = IBKRMarketData(ib)

        pnl = md.position_pnl(pos)
        assert pnl == pytest.approx(-123.45)


# ---------------------------------------------------------------------------
# Tests: implied_vol
# ---------------------------------------------------------------------------

class MockTradierClient:
    """Simple stand-in for TradierClient used in implied_vol tests."""

    def __init__(self, return_value=None, raise_exc=None):
        self.return_value = return_value
        self.raise_exc = raise_exc
        self.calls: List[str] = []

    def atm_iv(self, symbol: str) -> Optional[float]:
        self.calls.append(symbol)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.return_value


class TestIBKRImpliedVol:

    def test_ibkr_implied_vol_uses_tradier_first(self):
        """
        Tradier returns 0.18 -> result is 0.18 and IBKR is NOT queried.
        """
        tradier = MockTradierClient(return_value=0.18)
        ib = MockIB()
        md = IBKRMarketData(ib, tradier_client=tradier)

        result = md.implied_vol("SPY")

        assert result == pytest.approx(0.18)
        assert tradier.calls == ["SPY"]
        # IBKR should not have been touched
        assert ib.req_market_data_calls == 0
        assert ib.qualify_calls == 0

    def test_ibkr_implied_vol_falls_back_to_ibkr_tick106_on_none(self):
        """
        Tradier returns None -> fall back to IBKR tick 106, which returns 0.22.
        """
        tradier = MockTradierClient(return_value=None)
        # The stock contract will get conId=1
        tickers = {1: FakeTicker(impliedVolatility=0.22)}
        ib = MockIB(tickers=tickers)
        md = IBKRMarketData(ib, tradier_client=tradier)

        result = md.implied_vol("SPY")

        assert result == pytest.approx(0.22)
        assert ib.req_market_data_calls == 1

    def test_ibkr_implied_vol_falls_back_to_ibkr_tick106_on_exception(self):
        """
        Tradier raises ValueError -> fall back to IBKR tick 106, which returns 0.19.
        """
        tradier = MockTradierClient(raise_exc=ValueError("no IV"))
        tickers = {1: FakeTicker(impliedVolatility=0.19)}
        ib = MockIB(tickers=tickers)
        md = IBKRMarketData(ib, tradier_client=tradier)

        result = md.implied_vol("SPY")

        assert result == pytest.approx(0.19)
        assert ib.req_market_data_calls == 1

    def test_ibkr_implied_vol_returns_none_when_both_fail(self):
        """
        Tradier raises; IBKR ticker.impliedVolatility is NaN -> return None.
        """
        tradier = MockTradierClient(raise_exc=ValueError("no IV"))
        nan = float("nan")
        tickers = {1: FakeTicker(impliedVolatility=nan)}
        ib = MockIB(tickers=tickers)
        md = IBKRMarketData(ib, tradier_client=tradier)

        result = md.implied_vol("SPY")

        assert result is None

    def test_ibkr_implied_vol_no_tradier_client(self):
        """
        No tradier_client passed -> only IBKR tick 106 path attempted; returns value.
        """
        tickers = {1: FakeTicker(impliedVolatility=0.31)}
        ib = MockIB(tickers=tickers)
        md = IBKRMarketData(ib)  # no tradier_client

        result = md.implied_vol("SPY")

        assert result == pytest.approx(0.31)
        assert ib.req_market_data_calls == 1
