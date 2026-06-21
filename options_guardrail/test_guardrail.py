"""
Tests for the guardrail core (no IBKR / network needed).

Run:  pytest -q   (from the options_guardrail/ directory)
"""

from datetime import date

import pytest

from risk_policy import MODERATE
from schema import TradePlan, SchemaError
from state import AccountState
from guardrail import Guardrail, Decision


def _state(equity=100_000.0, day_anchor=None, week_anchor=None,
           open_positions=0, deployed=0.0) -> AccountState:
    return AccountState(
        equity=equity,
        day_anchor_equity=day_anchor if day_anchor is not None else equity,
        week_anchor_equity=week_anchor if week_anchor is not None else equity,
        day_key=date.today().isoformat(),
        week_key="2026-W22",
        open_positions=open_positions,
        deployed_usd=deployed,
    )


def _plan(**over):
    base = dict(
        plan_id="p1",
        symbol="SPY",
        structure="debit_call_spread",
        legs=[
            {"symbol": "SPY", "expiry": "2026-06-19", "strike": 500, "right": "C", "side": "BUY"},
            {"symbol": "SPY", "expiry": "2026-06-19", "strike": 505, "right": "C", "side": "SELL"},
        ],
        thesis="bounce off support",
        max_loss_usd=1500.0,    # for requested_qty units total
        requested_qty=10,
        invalidation={"kind": "underlying_below", "value": 495.0},
        target_profit_usd=3500.0,
    )
    base.update(over)
    return TradePlan.from_dict(base)


# ---------------- kill-switch ----------------
def test_daily_killswitch_halts():
    g = Guardrail(MODERATE)
    st = _state(equity=94_000, day_anchor=100_000)  # -6% day, beyond -5%
    res = g.evaluate(_plan(), st)
    assert res.decision == Decision.HALTED
    assert res.approved_qty == 0


def test_weekly_killswitch_halts():
    g = Guardrail(MODERATE)
    st = _state(equity=89_000, day_anchor=90_000, week_anchor=100_000)  # -11% week
    res = g.evaluate(_plan(), st)
    assert res.decision == Decision.HALTED


def test_just_inside_daily_limit_allows():
    g = Guardrail(MODERATE)
    st = _state(equity=95_500, day_anchor=100_000)  # -4.5% day, inside -5%
    res = g.evaluate(_plan(max_loss_usd=150.0, requested_qty=1), st)
    assert res.tradeable


def test_marked_drawdown_halts_even_if_realized_does_not():
    # If use_marked_drawdown is True (default MODERATE has True), open unrealized losses will trigger the halt.
    from risk_policy import RiskPolicy
    policy_marked = RiskPolicy(use_marked_drawdown=True, daily_halt_pct=0.05, weekly_halt_pct=0.10)
    g = Guardrail(policy_marked)
    st = _state(equity=100_000, day_anchor=100_000)
    st.unrealized_pnl = -6000.0  # -6% marked drawdown
    res = g.evaluate(_plan(), st)
    assert res.decision == Decision.HALTED

    # If use_marked_drawdown is False, it should NOT halt.
    policy_unmarked = RiskPolicy(use_marked_drawdown=False, daily_halt_pct=0.05, weekly_halt_pct=0.10)
    g2 = Guardrail(policy_unmarked)
    res2 = g2.evaluate(_plan(max_loss_usd=150.0, requested_qty=1), st)
    assert res2.tradeable



# ---------------- structure rules ----------------
def test_undefined_risk_rejected():
    g = Guardrail(MODERATE)
    res = g.evaluate(_plan(structure="naked_put"), _state())
    assert res.decision == Decision.REJECTED
    assert "undefined-risk" in res.reasons[0]


def test_unknown_structure_rejected():
    g = Guardrail(MODERATE)
    res = g.evaluate(_plan(structure="quantum_butterfly"), _state())
    assert res.decision == Decision.REJECTED


# ---------------- invalidation ----------------
def test_missing_invalidation_rejected():
    g = Guardrail(MODERATE)
    res = g.evaluate(_plan(invalidation=None), _state())
    assert res.decision == Decision.REJECTED
    assert "invalidation" in res.reasons[0].lower()


# ---------------- defined max loss ----------------
def test_nonpositive_max_loss_rejected():
    g = Guardrail(MODERATE)
    res = g.evaluate(_plan(max_loss_usd=0.0), _state())
    assert res.decision == Decision.REJECTED


# ---------------- sizing ----------------
def test_resizes_down_to_per_trade_cap():
    # equity 100k -> 2% cap = $2,000. Plan asks 10 units @ $1,500 total => $150/unit.
    # Wait: that's only $1,500 total, under cap, so all 10 fit. Make per-unit bigger.
    g = Guardrail(MODERATE)
    # 10 units, $5,000 total => $500/unit. Cap $2,000 -> max 4 units.
    res = g.evaluate(_plan(max_loss_usd=5000.0, requested_qty=10), _state())
    assert res.decision == Decision.APPROVED_RESIZED
    assert res.approved_qty == 4  # floor(2000/500)


def test_single_unit_too_risky_rejected():
    g = Guardrail(MODERATE)
    # 1 unit risking $2,500 > $2,000 cap -> reject.
    res = g.evaluate(_plan(max_loss_usd=2500.0, requested_qty=1), _state())
    assert res.decision == Decision.REJECTED


def test_full_size_when_within_cap():
    g = Guardrail(MODERATE)
    # 10 units @ $150/unit = $1,500 total, under $2,000 cap -> all 10.
    res = g.evaluate(_plan(max_loss_usd=1500.0, requested_qty=10), _state())
    assert res.decision == Decision.APPROVED
    assert res.approved_qty == 10


# ---------------- portfolio caps ----------------
def test_max_concurrent_positions_rejected():
    g = Guardrail(MODERATE)
    st = _state(open_positions=MODERATE.max_concurrent_positions)
    res = g.evaluate(_plan(max_loss_usd=150.0, requested_qty=1), st)
    assert res.decision == Decision.REJECTED
    assert "concurrent" in res.reasons[0].lower()


def test_deployed_capital_cap_resizes():
    g = Guardrail(MODERATE)
    # deploy cap = 25% of 100k = 25,000. Already 24,500 used -> $500 room.
    st = _state(deployed=24_500.0)
    # per-unit $250, asks 10 -> per-trade cap allows 8, but deploy room allows floor(500/250)=2.
    res = g.evaluate(_plan(max_loss_usd=2500.0, requested_qty=10), st)
    assert res.decision == Decision.APPROVED_RESIZED
    assert res.approved_qty == 2


# ---------------- schema ----------------
def test_schema_missing_field_raises():
    with pytest.raises(SchemaError):
        TradePlan.from_dict({"plan_id": "x", "symbol": "SPY"})


def test_schema_empty_legs_raises():
    with pytest.raises(SchemaError):
        _plan(legs=[])


# ---------------- marked-equity kill-switch (env-configurable) ----------------

def test_marked_daily_unrealized_tips_over_halts():
    """Realized equity alone stays inside -5%, but unrealized loss pushes marked total past -5%."""
    from risk_policy import RiskPolicy
    policy = RiskPolicy(use_marked_drawdown=True, daily_halt_pct=0.05, weekly_halt_pct=0.10)
    g = Guardrail(policy)
    # Realized: -3% (96,700 equity vs 99,700 day_anchor) -- note: anchor must be close to 100k
    # Actually: equity=97_000, day_anchor=100_000 → realized=-3%, inside limit
    st = _state(equity=97_000, day_anchor=100_000)
    st.unrealized_pnl = -2_500.0   # marked = (97_000 - 2_500) / 100_000 = -5.5% → HALT
    res = g.evaluate(_plan(max_loss_usd=150.0, requested_qty=1), st)
    assert res.decision == Decision.HALTED, f"Expected HALTED, got {res.decision}: {res.reasons}"
    assert "DAILY" in res.reasons[0]


def test_marked_weekly_unrealized_tips_over_halts():
    """Realized within weekly limit; unrealized tips marked total past -10%."""
    from risk_policy import RiskPolicy
    policy = RiskPolicy(use_marked_drawdown=True, daily_halt_pct=0.05, weekly_halt_pct=0.10)
    g = Guardrail(policy)
    # equity=91_000, day_anchor=91_000 → daily realized=0%, daily marked=(91k-2.5k-91k)/91k=-2.75% → OK
    # week_anchor=100_000 → weekly marked=(91k-2.5k-100k)/100k=-11.5% → HALT WEEKLY
    st = _state(equity=91_000, day_anchor=91_000, week_anchor=100_000)
    st.unrealized_pnl = -2_500.0   # marked_weekly = (91_000 - 2_500 - 100_000) / 100_000 = -11.5% → HALT
    res = g.evaluate(_plan(max_loss_usd=150.0, requested_qty=1), st)
    assert res.decision == Decision.HALTED, f"Expected HALTED, got {res.decision}: {res.reasons}"
    assert "WEEKLY" in res.reasons[0]


def test_marked_killswitch_off_unrealized_ignored():
    """With use_marked_drawdown=False, large unrealized losses do NOT trigger the halt."""
    from risk_policy import RiskPolicy
    policy = RiskPolicy(use_marked_drawdown=False, daily_halt_pct=0.05, weekly_halt_pct=0.10)
    g = Guardrail(policy)
    st = _state(equity=97_000, day_anchor=100_000)
    st.unrealized_pnl = -2_500.0   # would be -5.5% marked, but mode is realized-only
    res = g.evaluate(_plan(max_loss_usd=150.0, requested_qty=1), st)
    assert res.tradeable, f"Expected tradeable with kill-switch off, got {res.decision}: {res.reasons}"


def test_marked_near_threshold_not_halted():
    """Combined marked drawdown just inside -5% threshold: -4.9% → should still be tradeable."""
    from risk_policy import RiskPolicy
    policy = RiskPolicy(use_marked_drawdown=True, daily_halt_pct=0.05, weekly_halt_pct=0.10)
    g = Guardrail(policy)
    st = _state(equity=97_000, day_anchor=100_000)
    st.unrealized_pnl = -1_900.0   # marked = (97_000 - 1_900 - 100_000) / 100_000 = -4.9% → inside limit
    res = g.evaluate(_plan(max_loss_usd=150.0, requested_qty=1), st)
    assert res.tradeable, f"Expected tradeable at -4.9% marked, got {res.decision}: {res.reasons}"
