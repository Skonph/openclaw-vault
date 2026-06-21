"""
Tests for the daily report (pure, offline).

Run:  pytest -q
"""

from datetime import datetime, timezone, timedelta

from state import AccountState
from positions import Position, PositionStore
from daily_report import build_daily_report


def _state(equity=100_000.0, day=100_000.0, week=100_000.0):
    return AccountState(equity=equity, day_anchor_equity=day, week_anchor_equity=week,
                        day_key="2026-06-01", week_key="2026-W23")


def _closed(plan_id, pnl, reason, hours_ago, store_now):
    ts = (store_now - timedelta(hours=hours_ago)).isoformat()
    return Position(plan_id=plan_id, symbol="SPY", structure="debit_call_spread",
                    qty=5, entry_net_price=2.0, max_loss_usd=1000.0,
                    target_profit_usd=1500.0, invalidation=None,
                    opened_at=ts, status="CLOSED", closed_at=ts,
                    realized_pnl_usd=pnl, close_reason=reason)


def _open(plan_id, store_now, hours_ago=2):
    ts = (store_now - timedelta(hours=hours_ago)).isoformat()
    return Position(plan_id=plan_id, symbol="QQQ", structure="credit_put_spread",
                    qty=3, entry_net_price=-1.0, max_loss_usd=900.0,
                    target_profit_usd=400.0, invalidation=None, opened_at=ts)


def test_report_summarizes_closed_trades(tmp_path):
    now = datetime(2026, 6, 2, 1, 30, tzinfo=timezone.utc)
    store = PositionStore(tmp_path / "p.json")
    store._positions = [
        _closed("SPY-1", 600.0, "TAKE_PROFIT", 6, now),
        _closed("SPY-2", -400.0, "INVALIDATION", 4, now),
    ]
    txt = build_daily_report(store, _state(equity=100_200), now=now)
    assert "Closed:* 2" in txt
    assert "Realized P&L:* $200" in txt
    assert "Win rate:* 50%" in txt
    assert "SPY-1" in txt and "SPY-2" in txt


def test_report_ignores_old_trades(tmp_path):
    now = datetime(2026, 6, 2, 1, 30, tzinfo=timezone.utc)
    store = PositionStore(tmp_path / "p.json")
    store._positions = [_closed("OLD", 999.0, "TAKE_PROFIT", 48, now)]  # outside 24h
    txt = build_daily_report(store, _state(), now=now)
    assert "Closed:* 0" in txt
    assert "OLD" not in txt


def test_report_flags_weekly_killswitch(tmp_path):
    now = datetime(2026, 6, 2, 1, 30, tzinfo=timezone.utc)
    store = PositionStore(tmp_path / "p.json")
    txt = build_daily_report(store, _state(equity=89_000, week=100_000), now=now)
    assert "WEEKLY kill-switch ARMED" in txt


def test_report_flags_daily_killswitch(tmp_path):
    now = datetime(2026, 6, 2, 1, 30, tzinfo=timezone.utc)
    store = PositionStore(tmp_path / "p.json")
    txt = build_daily_report(store, _state(equity=94_000, day=100_000), now=now)
    assert "DAILY kill-switch ARMED" in txt


def test_report_lists_carried_open(tmp_path):
    now = datetime(2026, 6, 2, 1, 30, tzinfo=timezone.utc)
    store = PositionStore(tmp_path / "p.json")
    store._positions = [_open("QQQ-1", now)]
    txt = build_daily_report(store, _state(), now=now)
    assert "Still open:* 1" in txt
    assert "Carried open" in txt and "QQQ-1" in txt


def test_report_no_activity(tmp_path):
    now = datetime(2026, 6, 2, 1, 30, tzinfo=timezone.utc)
    store = PositionStore(tmp_path / "p.json")
    txt = build_daily_report(store, _state(), now=now)
    assert "No activity" in txt
    assert "Kill-switch clear" in txt
