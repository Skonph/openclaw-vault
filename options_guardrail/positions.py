"""
Open-position model + persistence.

When the guardrail approves a plan and the executor fills it, we record a
Position. The exit monitor reads these every tick and closes them when an
invalidation level, profit target, or stop is hit. Risk numbers are scaled to
the *approved* qty (which may be smaller than what the strategist requested).

Persisted to JSON so the monitor can be restarted without losing track of what's
open — critical when it's running unattended overnight.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from schema import TradePlan, Invalidation, OptionLeg
from guardrail import GuardrailResult


@dataclass
class Position:
    plan_id: str
    symbol: str
    structure: str
    qty: int                      # units actually opened (approved qty)
    entry_net_price: float        # per unit: debit (+) / credit (-)
    max_loss_usd: float           # total defined loss for this qty
    target_profit_usd: Optional[float]  # total target for this qty, if any
    invalidation: Optional[dict]  # {"kind":..., "value":...} or None
    opened_at: str                # ISO8601 UTC
    regime: str = "unknown"
    status: str = "OPEN"          # OPEN | CLOSED
    closed_at: Optional[str] = None
    realized_pnl_usd: Optional[float] = None
    close_reason: Optional[str] = None
    legs: List[dict] = field(default_factory=list)

    # ---- construction from an approved execution ----
    @staticmethod
    def from_execution(plan: TradePlan, result: GuardrailResult,
                       entry_net_price: Optional[float] = None) -> "Position":
        qty = result.approved_qty
        # target scales with the fraction of requested size we actually took
        target = None
        if plan.target_profit_usd is not None and plan.requested_qty > 0:
            per_unit_target = plan.target_profit_usd / plan.requested_qty
            target = per_unit_target * qty
        inv = None
        if plan.invalidation is not None:
            inv = {"kind": plan.invalidation.kind.value,
                   "value": plan.invalidation.value}
        legs_dict = [
            {
                "symbol": leg.symbol,
                "expiry": leg.expiry,
                "strike": leg.strike,
                "right": leg.right.value,
                "side": leg.side.value,
                "ratio": leg.ratio,
            }
            for leg in plan.legs
        ]
        return Position(
            plan_id=plan.plan_id,
            symbol=plan.symbol,
            structure=plan.structure,
            qty=qty,
            entry_net_price=(entry_net_price if entry_net_price is not None
                             else (plan.net_price or 0.0)),
            max_loss_usd=result.per_unit_max_loss * qty,
            target_profit_usd=target,
            invalidation=inv,
            opened_at=datetime.now(timezone.utc).isoformat(),
            regime=plan.regime,
            legs=legs_dict,
        )

    @property
    def is_open(self) -> bool:
        return self.status == "OPEN"

    @property
    def legs_obj(self) -> List[OptionLeg]:
        return [OptionLeg.from_dict(l) for l in self.legs]

    def invalidation_obj(self) -> Optional[Invalidation]:
        if self.invalidation is None:
            return None
        return Invalidation.from_dict(self.invalidation)


class PositionStore:
    """JSON-backed list of positions. Survives process restarts."""

    def __init__(self, path: str | Path | None):
        # path=None -> in-memory only (used by the backtest harness for speed).
        self.path = Path(path) if path is not None else None
        self._positions: List[Position] = []
        self._load()

    def _load(self) -> None:
        if self.path is not None and self.path.exists():
            data = json.loads(self.path.read_text())
            self._positions = [Position(**d) for d in data]

    def save(self) -> None:
        if self.path is None:
            return
        self.path.write_text(
            json.dumps([asdict(p) for p in self._positions], indent=2)
        )

    def reload(self) -> None:
        """Re-read positions from disk, discarding the in-memory copy, so a
        long-running monitor observes out-of-band closes (e.g. flatten_all.py).
        Without this the session loop holds a stale in-memory book, never sees the
        position go flat, and hangs forever holding session.lock. Keeps the current
        in-memory copy if the file is mid-write/corrupt (concurrent flatten write)."""
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            self._positions = [Position(**d) for d in data]
        except (json.JSONDecodeError, OSError):
            pass

    # ---- queries ----
    def open_positions(self) -> List[Position]:
        return [p for p in self._positions if p.is_open]

    def all(self) -> List[Position]:
        return list(self._positions)

    def get(self, plan_id: str) -> Optional[Position]:
        return next((p for p in self._positions if p.plan_id == plan_id), None)

    # ---- mutations ----
    def add(self, pos: Position) -> None:
        if self.get(pos.plan_id) is not None:
            raise ValueError(f"position {pos.plan_id} already tracked")
        self._positions.append(pos)
        self.save()

    def mark_closed(self, plan_id: str, realized_pnl_usd: float,
                    reason: str) -> Position:
        pos = self.get(plan_id)
        if pos is None:
            raise KeyError(plan_id)
        pos.status = "CLOSED"
        pos.closed_at = datetime.now(timezone.utc).isoformat()
        pos.realized_pnl_usd = realized_pnl_usd
        pos.close_reason = reason
        self.save()
        return pos
