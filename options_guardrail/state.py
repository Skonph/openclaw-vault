"""
Account / P&L state used by the kill-switch.

Persisted to a JSON file so halts survive a process restart — if you blew
through -5% today and the executor crashes and restarts, it must come back
HALTED, not flat. Day/week anchors roll automatically.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional


def _week_key(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


@dataclass
class AccountState:
    equity: float                 # current account equity (USD)
    day_anchor_equity: float      # equity at start of today
    week_anchor_equity: float     # equity at start of this ISO week
    day_key: str                  # "YYYY-MM-DD"
    week_key: str                 # "YYYY-Www"
    open_positions: int = 0
    deployed_usd: float = 0.0     # capital tied up in open defined-risk positions
    unrealized_pnl: float = 0.0   # open paper P&L (USD)

    # ---- drawdown views the kill-switch reads ----
    def get_day_drawdown_pct(self, use_marked: bool = False) -> float:
        eq = (self.equity + self.unrealized_pnl) if use_marked else self.equity
        if self.day_anchor_equity <= 0:
            return 0.0
        return (eq - self.day_anchor_equity) / self.day_anchor_equity

    def get_week_drawdown_pct(self, use_marked: bool = False) -> float:
        eq = (self.equity + self.unrealized_pnl) if use_marked else self.equity
        if self.week_anchor_equity <= 0:
            return 0.0
        return (eq - self.week_anchor_equity) / self.week_anchor_equity

    @property
    def day_drawdown_pct(self) -> float:
        return self.get_day_drawdown_pct(use_marked=False)

    @property
    def week_drawdown_pct(self) -> float:
        return self.get_week_drawdown_pct(use_marked=False)

    # ---- anchor rollover ----
    def roll_periods(self, now: Optional[datetime] = None) -> None:
        """Reset day/week anchors when the calendar advances."""
        today = (now or datetime.utcnow()).date()
        dk, wk = today.isoformat(), _week_key(today)
        if dk != self.day_key:
            self.day_anchor_equity = self.equity
            self.day_key = dk
        if wk != self.week_key:
            self.week_anchor_equity = self.equity
            self.week_key = wk

    # ---- persistence ----
    @staticmethod
    def load(path: str | Path, default_equity: float = 100_000.0) -> "AccountState":
        p = Path(path)
        if p.exists():
            d = json.loads(p.read_text())
            st = AccountState(**d)
            st.roll_periods()
            return st
        today = date.today()
        return AccountState(
            equity=default_equity,
            day_anchor_equity=default_equity,
            week_anchor_equity=default_equity,
            day_key=today.isoformat(),
            week_key=_week_key(today),
        )

    def save(self, path: str | Path | None) -> None:
        if path is None:
            return
        Path(path).write_text(json.dumps(asdict(self), indent=2))
