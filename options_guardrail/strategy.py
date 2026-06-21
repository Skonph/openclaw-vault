"""
Pluggable backtest strategies.

A strategy is just: (Context) -> list[TradePlan]. It decides WHAT to trade; the
guardrail decides whether/how much, and the exit monitor decides when to close.
The default below is a simple momentum vertical-spread strategy — deliberately
ordinary, so the backtest measures the *system*, not a magic edge.

You'll replace this with output from your Opus strategist (same TradePlan schema).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, List

from schema import TradePlan
from backtest_data import BacktestMarketData, CONTRACT_MULT


@dataclass
class Context:
    market: BacktestMarketData
    now: datetime
    equity: float
    history: Dict[str, List[float]]   # symbol -> trailing closes (oldest..newest)
    symbols: List[str]

    def momentum(self, symbol: str, lookback: int) -> float:
        h = self.history.get(symbol, [])
        if len(h) <= lookback or h[-lookback - 1] == 0:
            return 0.0
        return (h[-1] - h[-lookback - 1]) / h[-lookback - 1]


Strategy = Callable[[Context], List[TradePlan]]


def _round_to(x: float, step: float = 5.0) -> float:
    return round(x / step) * step


def default_momentum_strategy(
    ctx: Context,
    lookback: int = 5,
    threshold: float = 0.005,
    dte: int = 30,
    width: float = 5.0,
    requested_qty: int = 50,     # intentionally large -> guardrail resizes to 2% cap
    reward_mult: float = 1.5,
    invalidation_pct: float = 0.02,
) -> List[TradePlan]:
    plans: List[TradePlan] = []
    expiry = (ctx.now + timedelta(days=dte)).date().isoformat()

    for sym in ctx.symbols:
        if sym not in ctx.market.prices:
            continue
        mom = ctx.momentum(sym, lookback)
        if abs(mom) < threshold:
            continue

        spot = ctx.market.underlying_price(sym)
        k0 = _round_to(spot, width)
        bullish = mom > 0
        if bullish:
            legs = [
                {"symbol": sym, "expiry": expiry, "strike": k0, "right": "C", "side": "BUY"},
                {"symbol": sym, "expiry": expiry, "strike": k0 + width, "right": "C", "side": "SELL"},
            ]
            structure = "debit_call_spread"
            inval = {"kind": "underlying_below", "value": round(spot * (1 - invalidation_pct), 2)}
        else:
            legs = [
                {"symbol": sym, "expiry": expiry, "strike": k0, "right": "P", "side": "BUY"},
                {"symbol": sym, "expiry": expiry, "strike": k0 - width, "right": "P", "side": "SELL"},
            ]
            structure = "debit_put_spread"
            inval = {"kind": "underlying_above", "value": round(spot * (1 + invalidation_pct), 2)}

        # price the structure at current state to set defined risk
        draft = TradePlan.from_dict({
            "plan_id": f"{sym}-{ctx.now.date().isoformat()}",
            "symbol": sym, "structure": structure, "legs": legs,
            "thesis": f"{lookback}d momentum {mom:+.2%}",
            "max_loss_usd": 1.0, "requested_qty": requested_qty,
            "invalidation": inval,
        })
        debit = ctx.market.structure_value(draft.legs, sym)  # per-unit, USD price terms
        if debit <= 0:
            continue  # not a net-debit spread at these marks; skip
        max_loss_total = debit * CONTRACT_MULT * requested_qty
        target_total = reward_mult * max_loss_total

        plans.append(TradePlan.from_dict({
            "plan_id": draft.plan_id,
            "symbol": sym, "structure": structure, "legs": legs,
            "thesis": draft.thesis, "regime": "trend" if bullish else "downtrend",
            "net_price": round(debit, 4),
            "max_loss_usd": round(max_loss_total, 2),
            "target_profit_usd": round(target_total, 2),
            "requested_qty": requested_qty,
            "invalidation": inval,
        }))
    return plans
