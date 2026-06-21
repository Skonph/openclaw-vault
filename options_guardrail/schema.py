"""
Strategist -> Executor handoff contract.

The Opus strategist emits a TradePlan (one per idea). The guardrail validates it
and only then does the IBKR executor see it. Mandatory risk fields mean a
malformed or risk-incomplete plan is rejected *before* any capital moves —
the executor never has to "decide" anything about risk.

Pure dataclasses + a validator, so there's no hard pydantic dependency for the
core logic. `TradePlan.from_dict` is the single entry point for parsing model
output (JSON).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class RightType(str, Enum):
    CALL = "C"
    PUT = "P"


class InvalidationKind(str, Enum):
    # Where the thesis is wrong. The executor exits / refuses if breached.
    UNDERLYING_BELOW = "underlying_below"
    UNDERLYING_ABOVE = "underlying_above"
    IV_ABOVE = "iv_above"
    IV_BELOW = "iv_below"
    TIME_STOP = "time_stop"  # value is an ISO date/time


class SchemaError(ValueError):
    """Raised when model output does not satisfy the contract."""


@dataclass
class OptionLeg:
    symbol: str          # underlying, e.g. "SPY"
    expiry: str          # "YYYY-MM-DD"
    strike: float
    right: RightType
    side: Side
    ratio: int = 1       # contracts per 1 unit of the structure (usually 1)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "OptionLeg":
        try:
            return OptionLeg(
                symbol=str(d["symbol"]).upper(),
                expiry=str(d["expiry"]),
                strike=float(d["strike"]),
                right=RightType(str(d["right"]).upper()[:1]),
                side=Side(str(d["side"]).upper()),
                ratio=int(d.get("ratio", 1)),
            )
        except (KeyError, ValueError) as e:
            raise SchemaError(f"bad leg {d!r}: {e}") from e


@dataclass
class Invalidation:
    kind: InvalidationKind
    value: float | str   # price/IV level, or ISO timestamp for TIME_STOP

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Invalidation":
        try:
            kind = InvalidationKind(str(d["kind"]))
        except (KeyError, ValueError) as e:
            raise SchemaError(f"bad invalidation.kind {d!r}: {e}") from e
        if "value" not in d:
            raise SchemaError("invalidation.value is required")
        return Invalidation(kind=kind, value=d["value"])


@dataclass
class TradePlan:
    # identity / intent
    plan_id: str
    symbol: str
    structure: str                       # must be in policy.allowed_structures
    legs: List[OptionLeg]
    thesis: str

    # risk — all REQUIRED by the moderate policy
    max_loss_usd: float                  # capped, defined dollar loss for 1 unit * requested_qty
    requested_qty: int                   # units (spreads/contracts) the strategist wants
    invalidation: Optional[Invalidation]

    # context (optional but recommended)
    regime: str = "unknown"              # e.g. "low_iv_grind", "iv_spike", "trend", "chop"
    target_profit_usd: Optional[float] = None
    net_price: Optional[float] = None    # debit (+) or credit (-) per unit, for reference
    notes: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def reward_risk_ratio(self) -> Optional[float]:
        if self.target_profit_usd is None or self.max_loss_usd <= 0:
            return None
        return self.target_profit_usd / self.max_loss_usd

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "TradePlan":
        """Parse and structurally validate raw model JSON. Raises SchemaError."""
        required = ["plan_id", "symbol", "structure", "legs", "thesis",
                    "max_loss_usd", "requested_qty"]
        missing = [k for k in required if k not in d]
        if missing:
            raise SchemaError(f"missing required field(s): {missing}")

        legs = [OptionLeg.from_dict(l) for l in d["legs"]]
        if not legs:
            raise SchemaError("a plan needs at least one leg")

        inv = None
        if d.get("invalidation") is not None:
            inv = Invalidation.from_dict(d["invalidation"])

        try:
            max_loss = float(d["max_loss_usd"])
            qty = int(d["requested_qty"])
        except (TypeError, ValueError) as e:
            raise SchemaError(f"max_loss_usd / requested_qty not numeric: {e}") from e

        return TradePlan(
            plan_id=str(d["plan_id"]),
            symbol=str(d["symbol"]).upper(),
            structure=str(d["structure"]),
            legs=legs,
            thesis=str(d["thesis"]),
            max_loss_usd=max_loss,
            requested_qty=qty,
            invalidation=inv,
            regime=str(d.get("regime", "unknown")),
            target_profit_usd=(float(d["target_profit_usd"])
                               if d.get("target_profit_usd") is not None else None),
            net_price=(float(d["net_price"]) if d.get("net_price") is not None else None),
            notes=str(d.get("notes", "")),
            raw=d,
        )
