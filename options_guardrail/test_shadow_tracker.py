"""
Tests for the shadow-performance tracker (offline, BS marking).

Run:  pytest -q
"""

from datetime import datetime, timezone, date

import pytest

from schema import TradePlan
from guardrail import Guardrail
from risk_policy import RiskPolicy, LEVEL2_STRUCTURES
from state import AccountState
from strategist_bridge import parse_strategist_output, evaluate_envelope, PlanDecision
from shadow_tracker import ShadowTracker, structure_value


def _state():
    return AccountState(equity=100_000, day_anchor_equity=100_000,
                        week_anchor_equity=100_000, day_key=date.today().isoformat(),
                        week_key="2026-W23")


def _decisions(structure="debit_call_spread", strike=535, width=5, qty=5,
               max_loss=1000.0, target=1500.0, inval_below=531.0):
    raw = {
        "session_date": "2026-06-03", "regime": "uptrend", "no_trade": False,
        "reasoning": "x", "plans": [{
            "plan_id": "SPY-1", "symbol": "SPY", "structure": structure,
            "thesis": "trend",
            "legs": [
                {"symbol": "SPY", "expiry": "2026-06-19", "strike": strike, "right": "C", "side": "BUY"},
                {"symbol": "SPY", "expiry": "2026-06-19", "strike": strike + width, "right": "C", "side": "SELL"},
            ],
            "net_price": 2.1, "max_loss_usd": max_loss, "target_profit_usd": target,
            "requested_qty": qty,
            "invalidation": {"kind": "underlying_below", "value": inval_below},
        }]}
    env = parse_strategist_output(__import__("json").dumps(raw))
    g = Guardrail(RiskPolicy(allowed_structures=LEVEL2_STRUCTURES))
    return env, evaluate_envelope(env, _state(), g)


def test_structure_value_debit_spread_positive_and_rises():
    legs = [{"symbol": "SPY", "expiry": "2026-06-19", "strike": 535, "right": "C", "side": "BUY", "ratio": 1},
            {"symbol": "SPY", "expiry": "2026-06-19", "strike": 540, "right": "C", "side": "SELL", "ratio": 1}]
    now = datetime(2026, 6, 3, tzinfo=timezone.utc)
    v_lo = structure_value(legs, 535, 0.12, now)
    v_hi = structure_value(legs, 539, 0.12, now)
    assert v_lo > 0 and v_hi > v_lo            # debit, and gains as spot rises


def test_open_from_decisions(tmp_path):
    env, decisions = _decisions()
    t = ShadowTracker(tmp_path / "s.json")
    opened = t.open_from_decisions(decisions, {"SPY": {"last": 536.0, "atm_iv": 0.12}},
                                   now=datetime(2026, 6, 3, tzinfo=timezone.utc))
    assert opened == ["SPY-1"]
    p = t.get("SPY-1")
    assert p.qty >= 1 and p.entry_unit_value > 0
    # re-opening same plan id is a no-op
    assert t.open_from_decisions(decisions, {"SPY": {"last": 536.0, "atm_iv": 0.12}}) == []


def test_mark_closes_on_invalidation(tmp_path):
    env, decisions = _decisions(inval_below=531.0)
    t = ShadowTracker(tmp_path / "s.json")
    t.open_from_decisions(decisions, {"SPY": {"last": 536.0, "atm_iv": 0.12}},
                          now=datetime(2026, 6, 3, tzinfo=timezone.utc))
    closed = t.mark_and_close(get_spot=lambda s: 530.0, get_iv=lambda s: 0.12,
                              now=datetime(2026, 6, 4, tzinfo=timezone.utc))
    assert len(closed) == 1 and closed[0].close_reason == "INVALIDATION"
    assert closed[0].realized_pnl_usd < 0      # spot fell below entry -> loss


def test_mark_closes_on_target(tmp_path):
    env, decisions = _decisions(target=200.0)
    t = ShadowTracker(tmp_path / "s.json")
    t.open_from_decisions(decisions, {"SPY": {"last": 536.0, "atm_iv": 0.12}},
                          now=datetime(2026, 6, 3, tzinfo=timezone.utc))
    # big favorable move -> spread near max value -> target hit
    closed = t.mark_and_close(get_spot=lambda s: 545.0, get_iv=lambda s: 0.12,
                              now=datetime(2026, 6, 10, tzinfo=timezone.utc))
    assert len(closed) == 1 and closed[0].close_reason in ("TARGET", "EXPIRY")
    assert closed[0].realized_pnl_usd > 0


def test_expiry_closes_position(tmp_path):
    env, decisions = _decisions()
    t = ShadowTracker(tmp_path / "s.json")
    t.open_from_decisions(decisions, {"SPY": {"last": 536.0, "atm_iv": 0.12}},
                          now=datetime(2026, 6, 3, tzinfo=timezone.utc))
    # mark after expiry with spot between strikes
    closed = t.mark_and_close(get_spot=lambda s: 537.0, get_iv=lambda s: 0.12,
                              now=datetime(2026, 6, 20, tzinfo=timezone.utc))
    assert len(closed) == 1 and closed[0].close_reason in ("EXPIRY", "TARGET")


def test_summary_track_record(tmp_path):
    t = ShadowTracker(tmp_path / "s.json", starting_equity=100_000)
    # one win, one loss booked directly
    from shadow_tracker import ShadowPosition
    t._positions = [
        ShadowPosition(plan_id="a", symbol="SPY", structure="debit_call_spread", qty=5,
                       legs=[], entry_date="2026-06-03", entry_spot=1, entry_iv=0.1,
                       entry_unit_value=1, max_loss_usd=1000, target_profit_usd=1500,
                       invalidation=None, status="CLOSED", realized_pnl_usd=600,
                       close_reason="TARGET"),
        ShadowPosition(plan_id="b", symbol="SPY", structure="debit_call_spread", qty=5,
                       legs=[], entry_date="2026-06-03", entry_spot=1, entry_iv=0.1,
                       entry_unit_value=1, max_loss_usd=1000, target_profit_usd=1500,
                       invalidation=None, status="CLOSED", realized_pnl_usd=-400,
                       close_reason="INVALIDATION"),
    ]
    s = t.summary()
    assert s["closed"] == 2 and s["win_rate"] == 0.5
    assert s["total_pnl"] == 200 and s["equity"] == 100_200


def test_persistence_roundtrip(tmp_path):
    env, decisions = _decisions()
    p = tmp_path / "s.json"
    t = ShadowTracker(p)
    t.open_from_decisions(decisions, {"SPY": {"last": 536.0, "atm_iv": 0.12}},
                          now=datetime(2026, 6, 3, tzinfo=timezone.utc))
    t2 = ShadowTracker(p)        # reload from disk
    assert t2.get("SPY-1") is not None and len(t2.open_positions()) == 1
