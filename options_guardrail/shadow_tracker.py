"""
Shadow-performance tracker — a forward-running, broker-free record of how the
strategist's would-trade plans actually perform.

Flow:
  - shadow_report.py (pre-session) OPENS each approved plan as a shadow position,
    marked at entry with Black-Scholes.
  - daily_report.py (next morning) MARKS every open shadow position against the
    latest real Tradier close (BS), and CLOSES any that hit the same exit rules the
    live monitor uses: invalidation level, stop (% of max loss), profit target, or
    expiry. Realized P&L accrues into a running hypothetical equity.

This is the backtest harness running forward on live signals — the evidence you
want before trusting paper execution. Daily granularity (one mark/day), so treat
the P&L as indicative, not fill-accurate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from bs import bs_price
from schema import InvalidationKind

CONTRACT_MULT = 100
RISK_FREE = 0.04
STOP_FRACTION = 0.85   # close a shadow position at 85% of its defined max loss


def _leg_dicts(plan) -> List[dict]:
    return [{"symbol": l.symbol, "expiry": l.expiry, "strike": l.strike,
             "right": l.right.value, "side": l.side.value, "ratio": l.ratio}
            for l in plan.legs]


def _expiry_dt(expiry: str) -> datetime:
    if "T" in expiry:
        dt = datetime.fromisoformat(expiry)
    else:
        dt = datetime.fromisoformat(expiry + "T16:00:00")
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def structure_value(legs: List[dict], spot: float, iv: float,
                    now: datetime) -> float:
    """Net value of the structure to the holder, per 1 unit (long +, short -)."""
    total = 0.0
    for leg in legs:
        T = max(0.0, (_expiry_dt(leg["expiry"]) - now).total_seconds()
                / (365.0 * 24 * 3600))
        px = bs_price(spot, leg["strike"], T, RISK_FREE, iv, leg["right"])
        sign = 1.0 if leg["side"] == "BUY" else -1.0
        total += sign * leg.get("ratio", 1) * px
    return total


@dataclass
class ShadowPosition:
    plan_id: str
    symbol: str
    structure: str
    qty: int
    legs: List[dict]
    entry_date: str
    entry_spot: float
    entry_iv: float
    entry_unit_value: float
    max_loss_usd: float
    target_profit_usd: Optional[float]
    invalidation: Optional[dict]
    status: str = "OPEN"
    close_date: Optional[str] = None
    realized_pnl_usd: Optional[float] = None
    close_reason: Optional[str] = None
    regime: str = "unknown"


class ShadowTracker:
    def __init__(self, path, starting_equity: float = 100_000.0):
        self.path = Path(path)
        self.starting_equity = starting_equity
        self._positions: List[ShadowPosition] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            d = json.loads(self.path.read_text())
            self._positions = [ShadowPosition(**p) for p in d.get("positions", [])]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(
            {"positions": [asdict(p) for p in self._positions]}, indent=2))

    # ---- queries ----
    def get(self, plan_id: str) -> Optional[ShadowPosition]:
        return next((p for p in self._positions if p.plan_id == plan_id), None)

    def open_positions(self) -> List[ShadowPosition]:
        return [p for p in self._positions if p.status == "OPEN"]

    def closed(self) -> List[ShadowPosition]:
        return [p for p in self._positions if p.status == "CLOSED"]

    # ---- open (called from shadow_report) ----
    def open_from_decisions(self, decisions, snapshot: Dict[str, dict],
                            now: Optional[datetime] = None) -> List[str]:
        """snapshot: {symbol: {"last": float, "atm_iv": float}}."""
        now = now or datetime.now(timezone.utc)
        opened: List[str] = []
        for d in decisions:
            if not d.result.tradeable:
                continue
            plan = d.plan
            if self.get(plan.plan_id) is not None:
                continue
            row = snapshot.get(plan.symbol, {})
            spot, iv = row.get("last"), row.get("atm_iv")
            if spot is None or iv is None:
                continue
            legs = _leg_dicts(plan)
            qty = d.result.approved_qty
            target = None
            if plan.target_profit_usd is not None and plan.requested_qty > 0:
                target = plan.target_profit_usd / plan.requested_qty * qty
            inv = None
            if plan.invalidation is not None:
                inv = {"kind": plan.invalidation.kind.value,
                       "value": plan.invalidation.value}
            self._positions.append(ShadowPosition(
                plan_id=plan.plan_id, symbol=plan.symbol, structure=plan.structure,
                qty=qty, legs=legs, entry_date=now.date().isoformat(),
                entry_spot=float(spot), entry_iv=float(iv),
                entry_unit_value=structure_value(legs, float(spot), float(iv), now),
                max_loss_usd=d.result.per_unit_max_loss * qty,
                target_profit_usd=target, invalidation=inv, regime=plan.regime))
            opened.append(plan.plan_id)
        self.save()
        return opened

    # ---- mark + close (called from daily_report) ----
    def mark_and_close(self, get_spot: Callable[[str], Optional[float]],
                       get_iv: Callable[[str], Optional[float]],
                       now: Optional[datetime] = None) -> List[ShadowPosition]:
        now = now or datetime.now(timezone.utc)
        closed: List[ShadowPosition] = []
        for p in self.open_positions():
            spot = get_spot(p.symbol)
            if spot is None:
                continue
            iv = get_iv(p.symbol)
            iv = float(iv) if iv is not None else p.entry_iv
            cur = structure_value(p.legs, float(spot), iv, now)
            pnl = (cur - p.entry_unit_value) * p.qty * CONTRACT_MULT
            reason = self._exit_reason(p, float(spot), pnl, now)
            if reason:
                p.status = "CLOSED"
                p.close_date = now.date().isoformat()
                p.realized_pnl_usd = round(pnl, 2)
                p.close_reason = reason
                closed.append(p)
        self.save()
        return closed

    def _exit_reason(self, p: ShadowPosition, spot: float, pnl: float,
                     now: datetime) -> Optional[str]:
        inv = p.invalidation
        if inv:
            kind, val = inv["kind"], inv["value"]
            if kind == InvalidationKind.UNDERLYING_BELOW.value and spot <= float(val):
                return "INVALIDATION"
            if kind == InvalidationKind.UNDERLYING_ABOVE.value and spot >= float(val):
                return "INVALIDATION"
            if kind == InvalidationKind.TIME_STOP.value:
                t = datetime.fromisoformat(str(val))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                if now >= t:
                    return "INVALIDATION"
        # expiry (defined-risk settles at intrinsic, already reflected in pnl)
        if p.legs and now >= min(_expiry_dt(l["expiry"]) for l in p.legs):
            return "EXPIRY"
        if p.max_loss_usd > 0 and pnl <= -STOP_FRACTION * p.max_loss_usd:
            return "STOP"
        if p.target_profit_usd is not None and pnl >= p.target_profit_usd:
            return "TARGET"
        return None

    # ---- track record ----
    def summary(self) -> dict:
        cl = self.closed()
        pnls = [p.realized_pnl_usd or 0.0 for p in cl]
        wins = [x for x in pnls if x > 0]
        return {
            "closed": len(cl),
            "open": len(self.open_positions()),
            "win_rate": (len(wins) / len(cl)) if cl else 0.0,
            "total_pnl": round(sum(pnls), 2),
            "equity": round(self.starting_equity + sum(pnls), 2),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
            "avg_loss": round(sum(x for x in pnls if x <= 0)
                              / max(1, len(pnls) - len(wins)), 2) if cl else 0.0,
        }
