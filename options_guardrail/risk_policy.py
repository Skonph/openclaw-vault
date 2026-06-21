"""
Risk policy — the hard limits the guardrail layer enforces.

These numbers are the WHOLE POINT of running execution autonomously while you
sleep. The strategist proposes; the policy disposes. Nothing downstream
(including the Haiku executor) is allowed to widen these.

Selected profile: MODERATE
    - 2% max loss per trade  (fraction of account equity)
    - daily kill-switch at  -5%
    - weekly kill-switch at -10%
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import FrozenSet


# Structures whose maximum loss is NOT bounded at order time.
# The guardrail refuses these outright in autonomous mode — a single gap can
# blow past any stop, and there is no human awake to intervene.
UNDEFINED_RISK_STRUCTURES: FrozenSet[str] = frozenset(
    {
        "naked_call",
        "naked_put",
        "short_straddle",
        "short_strangle",
        "ratio_spread",  # back-ratio with more shorts than longs
    }
)

# Structures we accept: every one has a mathematically capped max loss.
DEFINED_RISK_STRUCTURES: FrozenSet[str] = frozenset(
    {
        "long_call",
        "long_put",
        "debit_call_spread",
        "debit_put_spread",
        "credit_call_spread",
        "credit_put_spread",
        "iron_condor",
        "iron_butterfly",
        "calendar_spread",
        "diagonal_spread",
    }
)

# IBKR options-permission tiers (what each level lets you actually place).
#   Level 2 = long options + DEBIT spreads only.
#   Level 3 = adds short/credit spreads, iron condors/butterflies, calendars, diagonals.
# Controlled by env OPTIONS_LEVEL ("2" or "3") so you can flip it without code edits
# the moment IBKR upgrades you. Defaults to 2 (the level we're requesting first).
LEVEL2_STRUCTURES: FrozenSet[str] = frozenset(
    {"long_call", "long_put", "debit_call_spread", "debit_put_spread"}
)
LEVEL3_STRUCTURES: FrozenSet[str] = DEFINED_RISK_STRUCTURES  # the full defined-risk set


def allowed_for_level(level: str | int) -> FrozenSet[str]:
    return LEVEL2_STRUCTURES if str(level).strip() == "2" else LEVEL3_STRUCTURES


_OPTIONS_LEVEL = os.getenv("OPTIONS_LEVEL", "2")
_ALLOWED = allowed_for_level(_OPTIONS_LEVEL)

# Marked-equity kill-switch: when true, unrealized P&L is included in the
# drawdown calculation that triggers the day/week halt. Default ON.
# Set MARKED_EQUITY_KILLSWITCH=false in .env to revert to realized-only mode.
_MARKED_KILLSWITCH: bool = os.getenv("MARKED_EQUITY_KILLSWITCH", "true").lower() != "false"


@dataclass(frozen=True)
class RiskPolicy:
    """Immutable risk limits. Frozen so nothing can mutate it at runtime."""

    # --- per-trade ---
    max_loss_per_trade_pct: float = 0.02       # 2% of equity at risk on any one trade
    min_reward_risk_ratio: float = 0.0         # set >0 to require defined edge; 0 = off
    min_premium_threshold: float = 0.0         # minimum acceptable net premium (credit/debit per contract) to prevent fee drag

    # --- contract scaling ---
    max_contracts_default: int = 999           # default max contracts when VIX <= threshold
    max_contracts_vix_high: int = 999          # max contracts when VIX > threshold (high volatility/high conviction)
    min_vix_for_two_contracts: float = 20.0     # VIX threshold to scale to 2 contracts

    # --- portfolio ---
    max_concurrent_positions: int = 5
    max_total_deployed_pct: float = 0.25       # at most 25% of equity in defined-risk at once

    # --- kill-switches (drawdown from the period's starting equity) ---
    daily_halt_pct: float = 0.05               # halt new entries for the day at -5%
    weekly_halt_pct: float = 0.10              # halt new entries for the week at -10%
    use_marked_drawdown: bool = True           # check drawdown based on marked (realized + unrealized) equity

    # --- structural rules ---
    allowed_structures: FrozenSet[str] = field(default=DEFINED_RISK_STRUCTURES)
    forbidden_structures: FrozenSet[str] = field(default=UNDEFINED_RISK_STRUCTURES)
    require_invalidation: bool = True          # every plan must carry an invalidation level
    require_defined_max_loss: bool = True      # every plan must carry a positive max_loss_usd

    @property
    def name(self) -> str:
        return "MODERATE"


# Named profiles. allowed_structures is gated by OPTIONS_LEVEL (default 2 = debit-only)
# so the strategist/guardrail never act on a structure IBKR hasn't approved.
CONSERVATIVE = RiskPolicy(
    max_loss_per_trade_pct=0.01,
    max_concurrent_positions=4,
    max_total_deployed_pct=0.15,
    daily_halt_pct=0.03,
    weekly_halt_pct=0.06,
    allowed_structures=_ALLOWED,
    min_premium_threshold=0.50 if _OPTIONS_LEVEL == "3" else 0.0,
    max_contracts_default=1 if _OPTIONS_LEVEL == "3" else 999,
    max_contracts_vix_high=2 if _OPTIONS_LEVEL == "3" else 999,
    use_marked_drawdown=_MARKED_KILLSWITCH,
)

MODERATE = RiskPolicy(
    allowed_structures=_ALLOWED,
    min_premium_threshold=0.50 if _OPTIONS_LEVEL == "3" else 0.0,
    max_contracts_default=1 if _OPTIONS_LEVEL == "3" else 999,
    max_contracts_vix_high=2 if _OPTIONS_LEVEL == "3" else 999,
    use_marked_drawdown=_MARKED_KILLSWITCH,
)

AGGRESSIVE = RiskPolicy(
    max_loss_per_trade_pct=0.03,
    max_concurrent_positions=8,
    max_total_deployed_pct=0.40,
    daily_halt_pct=0.08,
    weekly_halt_pct=0.15,
    allowed_structures=_ALLOWED,
    min_premium_threshold=0.50 if _OPTIONS_LEVEL == "3" else 0.0,
    max_contracts_default=1 if _OPTIONS_LEVEL == "3" else 999,
    max_contracts_vix_high=2 if _OPTIONS_LEVEL == "3" else 999,
    use_marked_drawdown=_MARKED_KILLSWITCH,
)

# The active policy for this system.
ACTIVE_POLICY = MODERATE
