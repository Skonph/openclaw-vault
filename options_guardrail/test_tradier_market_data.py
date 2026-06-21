"""
Tests for the TradierMarketData provider (offline).
"""

import pytest
from datetime import datetime, timezone

from schema import TradePlan
from guardrail import Guardrail
from risk_policy import MODERATE
from state import AccountState
from positions import Position
from market_data import TradierMarketData


class MockTradierClient:
    def __init__(self, quotes_data=None, iv_data=None):
        self.quotes_data = quotes_data or {}
        self.iv_data = iv_data or {}

    def quotes(self, symbols):
        out = []
        for s in symbols:
            if s in self.quotes_data:
                out.append(self.quotes_data[s])
        return out

    def atm_iv(self, symbol):
        if symbol in self.iv_data:
            return self.iv_data[symbol]
        raise ValueError("no mock IV")


def _plan():
    return TradePlan.from_dict({
        "plan_id": "p1", "symbol": "SPY", "structure": "debit_call_spread",
        "legs": [
            {"symbol": "SPY", "expiry": "2026-06-19", "strike": 535.0, "right": "C", "side": "BUY"},
            {"symbol": "SPY", "expiry": "2026-06-19", "strike": 540.0, "right": "C", "side": "SELL"},
        ],
        "thesis": "test", "net_price": 2.10, "max_loss_usd": 1000.0,
        "requested_qty": 5, "target_profit_usd": 1500.0,
        "invalidation": {"kind": "underlying_below", "value": 531.0}
    })


def _position(plan):
    state = AccountState(
        equity=100000.0, day_anchor_equity=100000.0, week_anchor_equity=100000.0,
        day_key="2026-06-06", week_key="2026-W23"
    )
    g = Guardrail(MODERATE)
    res = g.evaluate(plan, state)
    return Position.from_execution(plan, res, entry_net_price=plan.net_price)


def test_tradier_market_data_underlying_price():
    client = MockTradierClient(quotes_data={"SPY": {"symbol": "SPY", "last": 536.5}})
    md = TradierMarketData(client)
    assert md.underlying_price("SPY") == 536.5


def test_tradier_market_data_implied_vol():
    client = MockTradierClient(iv_data={"SPY": 0.125})
    md = TradierMarketData(client)
    assert md.implied_vol("SPY") == 0.125
    assert md.implied_vol("QQQ") is None  # fallback on error


def test_tradier_market_data_position_pnl():
    # SPY 2026-06-19 C 535 -> SPY260619C00535000
    # SPY 2026-06-19 C 540 -> SPY260619C00540000
    client = MockTradierClient(quotes_data={
        "SPY260619C00535000": {"symbol": "SPY260619C00535000", "bid": 4.5, "ask": 5.5},  # mid = 5.0
        "SPY260619C00540000": {"symbol": "SPY260619C00540000", "bid": 2.5, "ask": 3.5},  # mid = 3.0
    })
    md = TradierMarketData(client)
    pos = _position(_plan())

    # Entry net price was 2.10. Current combo price = 5.0 - 3.0 = 2.0.
    # Diff = 2.0 - 2.10 = -0.10.
    # Total P&L = -0.10 * 100 * 5 qty = -50.0.
    pnl = md.position_pnl(pos)
    assert pnl == pytest.approx(-50.0)
