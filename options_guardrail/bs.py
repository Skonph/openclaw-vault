"""
Minimal Black-Scholes pricer for marking option legs over a backtest path.

Used only inside the backtest to turn an underlying price path into realistic
position P&L (time decay + moves + vol). Not a trading model — good enough to
stress-test the guardrail/exit logic, not to quote live.
"""

from __future__ import annotations

import math


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S: float, K: float, T: float, r: float, sigma: float,
             right: str) -> float:
    """
    European option price.
        S     spot
        K     strike
        T     time to expiry in YEARS
        r     risk-free rate (annual, decimal)
        sigma implied vol (annual, decimal)
        right "C" or "P"
    At/après expiry (T<=0) or zero vol, returns intrinsic value.
    """
    right = right.upper()[:1]
    if T <= 0 or sigma <= 0:
        intrinsic = (S - K) if right == "C" else (K - S)
        return max(0.0, intrinsic)

    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if right == "C":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)
