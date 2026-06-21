"""
Regression test for the session-hang fix (2026-06-19).

The live session holds an in-memory PositionStore; flatten_all.py closes the
position in a SEPARATE process by rewriting session_positions.json. Before the
fix, the session's store never re-read the file, so it never saw the book go
flat and run_forever() hung indefinitely (holding session.lock). PositionStore
now has reload(), which run_forever() calls each tick.
"""
from positions import Position, PositionStore


def _pos(plan_id="X"):
    return Position(
        plan_id=plan_id, symbol="SPY", structure="debit_put_spread", qty=1,
        entry_net_price=1.3, max_loss_usd=1300.0, target_profit_usd=670.0,
        invalidation=None, opened_at="2026-06-18T14:15:00+00:00",
    )


def test_reload_sees_out_of_band_close(tmp_path):
    p = tmp_path / "session_positions.json"
    session_store = PositionStore(p)
    session_store.add(_pos())
    assert len(session_store.open_positions()) == 1

    # a separate process (e.g. flatten_all.py) closes it on disk
    PositionStore(p).mark_closed("X", -130.0, "EOD_FLATTEN")

    # stale in-memory view still shows it open (this caused the hang)
    assert len(session_store.open_positions()) == 1

    # after reload the session sees the out-of-band close -> book is flat
    session_store.reload()
    assert len(session_store.open_positions()) == 0
    assert session_store.get("X").status == "CLOSED"


def test_reload_keeps_copy_on_corrupt_file(tmp_path):
    p = tmp_path / "session_positions.json"
    store = PositionStore(p)
    store.add(_pos())
    p.write_text("{ this is not valid json")   # simulate mid-write by flatten
    store.reload()                              # must not raise
    assert len(store.open_positions()) == 1     # keeps last good in-memory copy


def _state(equity=100_000.0):
    from datetime import date
    from state import AccountState, _week_key
    today = date.today()
    return AccountState(equity=equity, day_anchor_equity=equity,
                        week_anchor_equity=equity, day_key=today.isoformat(),
                        week_key=_week_key(today), open_positions=1,
                        deployed_usd=1300.0)


def test_reconcile_books_out_of_band_close_once(tmp_path):
    """The crux: flatten closes the position in another process; the session must
    book the realized P&L into equity exactly once and zero the open counters."""
    from exit_monitor import ExitMonitor
    from market_data import MockMarketData

    p = tmp_path / "session_positions.json"
    store = PositionStore(p)
    store.add(_pos())
    state = _state(100_000.0)
    mon = ExitMonitor(store, MockMarketData(), state, state_path=tmp_path / "st.json")

    # another process (flatten_all) closes it on disk for -130
    PositionStore(p).mark_closed("X", -130.0, "EOD_FLATTEN")

    store.reload()
    mon.reconcile_from_store()
    assert state.equity == 99_870.0          # -130 booked into equity
    assert state.open_positions == 0
    assert state.deployed_usd == 0.0

    # idempotent: a second reconcile must NOT double-book
    mon.reconcile_from_store()
    assert state.equity == 99_870.0


def test_reconcile_does_not_rebook_preexisting_closes(tmp_path):
    """Closes already on disk at construction are baked into the loaded equity and
    must never be re-booked."""
    from exit_monitor import ExitMonitor
    from market_data import MockMarketData

    p = tmp_path / "session_positions.json"
    seed = PositionStore(p)
    seed.add(_pos("OLD"))
    seed.mark_closed("OLD", -50.0, "STOP")   # already closed before session starts

    store = PositionStore(p)                   # session loads with OLD already CLOSED
    state = _state(100_000.0)                   # equity already reflects the -50
    mon = ExitMonitor(store, MockMarketData(), state, state_path=tmp_path / "st.json")
    mon.reconcile_from_store()
    assert state.equity == 100_000.0           # unchanged — not re-booked
    assert state.open_positions == 0
