"""
Backtest market-data provider.

Implements the SAME MarketDataProvider protocol the live exit monitor uses, so
the exact guardrail + exit code runs unchanged in the backtest. The difference
is only the source of truth: here, an in-memory clock + price/IV state, and
position P&L marked leg-by-leg with Black-Scholes.

Workflow:
    bt = BacktestMarketData(r=0.04)
    bt.set_state(now, {"SPY": 535.0}, {"SPY": 0.18})
    bt.register(plan)              # records legs + entry mark at current state
    ...advance clock/prices each tick...
    bt.position_pnl(position)      # marked-to-model uPnL in USD
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from bs import bs_price
from schema import TradePlan, Side, OptionLeg
from positions import Position

CONTRACT_MULT = 100  # US equity options


@dataclass
class _Registered:
    legs: List[OptionLeg]
    entry_unit_value: float   # net structure value to holder, per 1 unit, at entry


class BacktestMarketData:
    def __init__(self, r: float = 0.04, default_iv: float = 0.20):
        self.r = r
        self.default_iv = default_iv
        self.now: Optional[datetime] = None
        self.prices: Dict[str, float] = {}
        self.ivs: Dict[str, float] = {}
        self._reg: Dict[str, _Registered] = {}

    # ---- state the harness updates each tick ----
    def set_state(self, now: datetime, prices: Dict[str, float],
                  ivs: Optional[Dict[str, float]] = None) -> None:
        self.now = now
        self.prices.update(prices)
        if ivs:
            self.ivs.update(ivs)

    # ---- MarketDataProvider protocol ----
    def underlying_price(self, symbol: str) -> float:
        if symbol not in self.prices:
            raise KeyError(f"no backtest price for {symbol}")
        return self.prices[symbol]

    def implied_vol(self, symbol: str) -> Optional[float]:
        return self.ivs.get(symbol, self.default_iv)

    def position_pnl(self, position: Position) -> float:
        reg = self._reg.get(position.plan_id)
        if reg is None:
            return 0.0
        cur_unit_value = self._structure_value(reg.legs, position.symbol)
        # P&L to the holder = (current value - entry value) per unit * qty * 100
        return (cur_unit_value - reg.entry_unit_value) * position.qty * CONTRACT_MULT

    # ---- registration (called by harness at fill time) ----
    def register(self, plan: TradePlan) -> float:
        """Record legs and the entry per-unit structure value. Returns net entry
        debit(+)/credit(-) per unit (in option price terms, *not* *100)."""
        entry_value = self._structure_value(plan.legs, plan.symbol)
        self._reg[plan.plan_id] = _Registered(legs=list(plan.legs),
                                              entry_unit_value=entry_value)
        # net the holder pays: positive value = debit
        return entry_value

    def deregister(self, plan_id: str) -> None:
        self._reg.pop(plan_id, None)

    def structure_value(self, legs: List[OptionLeg], symbol: str) -> float:
        """Public: net per-unit value of a structure to the holder at current
        state (debit positive, credit negative). Used by strategies to price."""
        return self._structure_value(legs, symbol)

    # ---- internals ----
    def _structure_value(self, legs: List[OptionLeg], symbol: str) -> float:
        """Value of the structure to the holder, per 1 unit (sum of leg marks,
        long +, short -). Uses current clock for time-to-expiry."""
        assert self.now is not None, "set_state() before pricing"
        total = 0.0
        for leg in legs:
            S = self.prices[leg.symbol]
            iv = self.ivs.get(leg.symbol, self.default_iv)
            expiry = datetime.fromisoformat(leg.expiry) if "T" in leg.expiry \
                else datetime.fromisoformat(leg.expiry + "T16:00:00")
            T = max(0.0, (expiry - self.now).total_seconds() / (365.0 * 24 * 3600))
            px = bs_price(S, leg.strike, T, self.r, iv, leg.right.value)
            sign = 1.0 if leg.side == Side.BUY else -1.0
            total += sign * leg.ratio * px
        return total
