"""
Backtest harness.

Replays a price path through the SAME guardrail + exit monitor that run live, so
what you measure here is what you'll trade. Positions are marked leg-by-leg with
Black-Scholes (time decay + moves + vol), realized P&L flows into equity, and the
kill-switch is active during the backtest exactly as in production.

Outputs a BacktestResult with the metrics that actually matter for deciding
whether to go live: win rate, expectancy per trade, profit factor, total return,
and — most importantly — max drawdown.

    result = Backtester(symbols=["SPY","QQQ"], strategy=default_momentum_strategy).run()
    print(result.summary())
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from risk_policy import ACTIVE_POLICY, RiskPolicy
from schema import TradePlan
from state import AccountState
from positions import Position, PositionStore
from guardrail import Guardrail
from exit_monitor import ExitMonitor, ExitConfig, ExitAction
from backtest_data import BacktestMarketData
from strategy import Context, Strategy, default_momentum_strategy


# ----------------------------- price paths -----------------------------
def gbm_paths(symbols: Dict[str, float], days: int, start: datetime,
              mu: float = 0.06, sigma: float = 0.20, seed: int = 7
              ) -> Tuple[List[datetime], Dict[str, List[float]]]:
    """Geometric Brownian Motion daily closes. Zero-dependency synthetic data so
    the harness runs with no external feed. Swap in real closes for real tests."""
    rng = random.Random(seed)
    dt = 1.0 / 252.0
    dates = [start + timedelta(days=i) for i in range(days)]
    paths: Dict[str, List[float]] = {}
    for sym, s0 in symbols.items():
        s = s0
        series = [s]
        for _ in range(days - 1):
            z = rng.gauss(0, 1)
            s = s * math.exp((mu - 0.5 * sigma * sigma) * dt + sigma * math.sqrt(dt) * z)
            series.append(s)
        paths[sym] = series
    return dates, paths


# ----------------------------- results -----------------------------
@dataclass
class BacktestResult:
    starting_equity: float
    final_equity: float
    trades: List[Position]
    equity_curve: List[Tuple[str, float, float]]  # (iso_date, marked, realized)
    policy_name: str

    # ---- trade stats ----
    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def pnls(self) -> List[float]:
        return [t.realized_pnl_usd or 0.0 for t in self.trades]

    @property
    def wins(self) -> List[float]:
        return [p for p in self.pnls if p > 0]

    @property
    def losses(self) -> List[float]:
        return [p for p in self.pnls if p <= 0]

    @property
    def win_rate(self) -> float:
        return len(self.wins) / self.n_trades if self.n_trades else 0.0

    @property
    def avg_win(self) -> float:
        return sum(self.wins) / len(self.wins) if self.wins else 0.0

    @property
    def avg_loss(self) -> float:
        return sum(self.losses) / len(self.losses) if self.losses else 0.0

    @property
    def expectancy(self) -> float:
        return sum(self.pnls) / self.n_trades if self.n_trades else 0.0

    @property
    def profit_factor(self) -> float:
        gross_loss = abs(sum(self.losses))
        if gross_loss == 0:
            return float("inf") if self.wins else 0.0
        return sum(self.wins) / gross_loss

    @property
    def total_return(self) -> float:
        return (self.final_equity - self.starting_equity) / self.starting_equity

    @property
    def max_drawdown(self) -> float:
        """Worst peak-to-trough on the marked equity curve (negative)."""
        peak = -math.inf
        mdd = 0.0
        for _, marked, _ in self.equity_curve:
            peak = max(peak, marked)
            if peak > 0:
                mdd = min(mdd, (marked - peak) / peak)
        return mdd

    def reason_counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for t in self.trades:
            out[t.close_reason or "?"] = out.get(t.close_reason or "?", 0) + 1
        return out

    # ---- reporting ----
    def summary(self) -> str:
        rc = ", ".join(f"{k}:{v}" for k, v in sorted(self.reason_counts().items()))
        pf = "inf" if self.profit_factor == float("inf") else f"{self.profit_factor:.2f}"
        return (
            f"Backtest ({self.policy_name})\n"
            f"  trades:        {self.n_trades}\n"
            f"  win rate:      {self.win_rate:.1%}\n"
            f"  avg win/loss:  ${self.avg_win:,.0f} / ${self.avg_loss:,.0f}\n"
            f"  expectancy:    ${self.expectancy:,.0f} / trade\n"
            f"  profit factor: {pf}\n"
            f"  total return:  {self.total_return:+.2%} "
            f"(${self.starting_equity:,.0f} -> ${self.final_equity:,.0f})\n"
            f"  max drawdown:  {self.max_drawdown:.2%}\n"
            f"  exits:         {rc}"
        )


# ----------------------------- engine -----------------------------
class Backtester:
    def __init__(
        self,
        symbols: List[str],
        strategy: Strategy = default_momentum_strategy,
        spot0: Optional[Dict[str, float]] = None,
        days: int = 120,
        start: Optional[datetime] = None,
        decision_every: int = 5,
        history_window: int = 20,
        starting_equity: float = 100_000.0,
        policy: RiskPolicy = ACTIVE_POLICY,
        exit_config: ExitConfig = ExitConfig(),
        iv: float = 0.20,
        r: float = 0.04,
        seed: int = 7,
        dates: Optional[List[datetime]] = None,
        paths: Optional[Dict[str, List[float]]] = None,
    ):
        self.symbols = symbols
        self.strategy = strategy
        self.decision_every = decision_every
        self.history_window = history_window
        self.starting_equity = starting_equity
        self.policy = policy
        self.exit_config = exit_config
        self.iv = iv
        self.r = r

        start = start or datetime(2026, 1, 5, 16, 0, 0)
        if dates is not None and paths is not None:
            self.dates, self.paths = dates, paths
        else:
            spot0 = spot0 or {s: 100.0 + 50 * i for i, s in enumerate(symbols)}
            self.dates, self.paths = gbm_paths(spot0, days, start, sigma=iv, seed=seed)

    def run(self) -> BacktestResult:
        market = BacktestMarketData(r=self.r, default_iv=self.iv)
        state = AccountState(
            equity=self.starting_equity,
            day_anchor_equity=self.starting_equity,
            week_anchor_equity=self.starting_equity,
            day_key=self.dates[0].date().isoformat(),
            week_key="",  # forces a roll on first tick
        )
        store = PositionStore(None)  # in-memory
        plan_registry: Dict[str, TradePlan] = {}
        guard = Guardrail(self.policy)
        mon = ExitMonitor(store, market, state, state_path=None,
                          config=self.exit_config,
                          closer=lambda pos, pnl: market.deregister(pos.plan_id))

        curve: List[Tuple[str, float, float]] = []

        for i, now in enumerate(self.dates):
            prices = {s: self.paths[s][i] for s in self.symbols}
            market.set_state(now, prices, {s: self.iv for s in self.symbols})

            # 1. manage open positions first
            mon.run_once(now=now)

            # 2. new entries on decision days (kill-switch checked inside guardrail)
            if i % self.decision_every == 0:
                lo = max(0, i - self.history_window)
                history = {s: self.paths[s][lo:i + 1] for s in self.symbols}
                ctx = Context(market=market, now=now, equity=state.equity,
                              history=history, symbols=self.symbols)
                for plan in self.strategy(ctx):
                    if store.get(plan.plan_id) is not None:
                        continue  # one position per plan_id
                    res = guard.evaluate(plan, state)
                    if not res.tradeable:
                        continue
                    market.register(plan)
                    pos = Position.from_execution(plan, res, entry_net_price=plan.net_price)
                    store.add(pos)
                    plan_registry[plan.plan_id] = plan
                    state.open_positions += 1
                    state.deployed_usd += res.per_unit_max_loss * res.approved_qty

            # 3. record marked equity
            marked = state.equity + sum(market.position_pnl(p)
                                        for p in store.open_positions())
            curve.append((now.date().isoformat(), marked, state.equity))

        # close anything still open at the final marks
        last = self.dates[-1]
        for pos in list(store.open_positions()):
            pnl = market.position_pnl(pos)
            state.equity += pnl
            state.open_positions = max(0, state.open_positions - 1)
            state.deployed_usd = max(0.0, state.deployed_usd - pos.max_loss_usd)
            store.mark_closed(pos.plan_id, pnl, "END_OF_TEST")
            market.deregister(pos.plan_id)
        curve.append((last.date().isoformat(), state.equity, state.equity))

        return BacktestResult(
            starting_equity=self.starting_equity,
            final_equity=state.equity,
            trades=[p for p in store.all() if not p.is_open],
            equity_curve=curve,
            policy_name=self.policy.name,
        )
