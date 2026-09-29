#!/usr/bin/env python3
"""
stopout_ledger_reconciler.py — back-fill stopout_audit_ledger.json with CONFIRMED fill reality.

CSO ruling 2026-09-23 (Realized Fill Backfill, approved):

`log_stopout_audit_event()` records the closing debit it ESTIMATED at trigger time. For the XLU
stop-out that estimate was $2.00/share while the confirmed fills closed at $2.62/share, so the
ledger reported a $1,386 capital save when the true figure was ~$828. Estimation is fine AT trigger
time; leaving it uncorrected forever is not.

This module matches every ledger event to the broker fill log (trade_history) and adds:
    closing_debit_realized          realized close debit per share
    actual_loss_realized            realized P&L for the spread
    capital_saved_realized          max_loss - |realized loss|
    collateral_salvage_pct_realized 1 - |realized loss| / collateral
    fill_verified / fill_verified_at_ict / fills_source
ESTIMATED fields are PRESERVED (closing_debit_est, actual_loss_est, capital_saved_est).

Idempotent: re-running only refreshes the realized fields. CLI: python3 stopout_ledger_reconciler.py
"""
from __future__ import annotations
import datetime
import json
from pathlib import Path
from typing import Any, Dict, List

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trade_history import spread_round_trips  # noqa: E402

LEDGER = Path("/home/ubuntu/shared/stopout_audit_ledger.json")


def _match_event(ev: Dict[str, Any], trips: List[Dict[str, Any]]):
    for t in trips:
        if (t.get("root") == ev.get("symbol")
                and abs((t.get("short_strike") or 0) - (ev.get("short_strike") or 0)) < 0.1
                and abs((t.get("long_strike") or 0) - (ev.get("long_strike") or 0)) < 0.1
                and (t.get("exp") == ev.get("expiration"))):
            return t
    return None


def backfill(ledger_path: Path = LEDGER, verbose: bool = True) -> Dict[str, Any]:
    p = Path(ledger_path)
    if not p.exists():
        print(f"  ⚠️ ledger not found: {p}")
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    try:
        trips = spread_round_trips()
    except Exception as ex:
        print(f"  🔴 fill log unavailable ({ex}) — ledger left untouched")
        return data

    now_ict = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT")
    events = data.get("events", [])
    matched = 0
    for ev in events:
        # preserve the trigger-time estimates exactly once
        if "closing_debit_est" in ev and "actual_loss_est" not in ev:
            ev["actual_loss_est"] = ev.get("actual_loss_at_stopout")
        if "capital_saved_est" not in ev:
            ev["capital_saved_est"] = ev.get("capital_saved_vs_max_loss")

        trip = _match_event(ev, trips)
        if not trip:
            ev.setdefault("fill_verified", False)
            ev.setdefault("fill_verified_at_ict", now_ict)
            ev.setdefault("fills_source", "unmatched — no corresponding broker fills")
            continue

        collateral = float(ev.get("collateral_locked") or trip.get("max_risk") or 0)
        realized_loss = float(trip.get("realized_pnl") or 0)
        ev["closing_debit_realized"] = trip.get("debit_sh")
        ev["actual_loss_realized"] = realized_loss
        ev["capital_saved_realized"] = round(max(0.0, collateral - abs(realized_loss)), 2)
        ev["collateral_salvage_pct_realized"] = (round(max(0.0, (1.0 - abs(realized_loss) / collateral) * 100), 1)
                                                 if collateral else None)
        ev["realized_round_trip_id"] = trip.get("id")
        ev["fills_used"] = trip.get("fill_count")
        ev["fill_verified"] = True
        ev["fill_verified_at_ict"] = now_ict
        ev["fills_source"] = "trade_history.py -> Alpaca /v2/account/activities (FILL)"
        ev["reconciliation_note"] = (
            f"Trigger-time estimate debit ${ev.get('closing_debit_est')}/sh vs "
            f"realized ${ev.get('closing_debit_realized')}/sh — realized figures are authoritative.")
        matched += 1

    # cumulative stats: keep estimated totals, ADD realized totals (never overwrite history)
    cs = data.setdefault("cumulative_stats", {})
    cs["total_capital_saved_usd"] = round(sum(e.get("capital_saved_est") or e.get("capital_saved_vs_max_loss") or 0
                                              for e in events), 2)
    realized_events = [e for e in events if e.get("fill_verified")]
    cs["total_capital_saved_realized_usd"] = round(sum(e.get("capital_saved_realized") or 0
                                                       for e in realized_events), 2)
    cs["total_loss_realized_usd"] = round(sum(e.get("actual_loss_realized") or 0
                                              for e in realized_events), 2)
    cs["realized_collateral_salvage_pct_avg"] = (
        round(sum(e.get("collateral_salvage_pct_realized") or 0 for e in realized_events) /
              len(realized_events), 1) if realized_events else None)
    cs["est_vs_realized_delta_usd"] = round((cs.get("total_capital_saved_usd") or 0)
                                            - (cs.get("total_capital_saved_realized_usd") or 0), 2)
    data["updated_at_ict"] = now_ict
    data["fill_reconciliation"] = {
        "verified_events": matched,
        "unverified_events": len(events) - matched,
        "source": "trade_history.py (Alpaca FILL activities)",
        "policy": "estimates preserved as *_est; realized figures authoritative for P&L",
    }
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if verbose:
        print(f"  ✅ Stopout ledger reconciled: {matched}/{len(events)} event(s) matched to fills -> {p}")
    return data


def reconcile_if_needed(ledger_path: Path = LEDGER, verbose: bool = False) -> Dict[str, Any]:
    """Cheap no-op unless the ledger holds unverified events — safe for a 5-minute poller.

    Returns {} immediately when every event is already fill-verified, so steady-state cost is one
    small file read (no broker calls).
    """
    p = Path(ledger_path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    events = data.get("events", [])
    if events and all(e.get("fill_verified") for e in events):
        return {}
    return backfill(ledger_path, verbose=verbose)


if __name__ == "__main__":
    d = backfill()
    print(json.dumps(d.get("cumulative_stats", {}), indent=2))
    for e in d.get("events", []):
        print(f"\n{e.get('event_id')}:")
        for k in ("closing_debit_est", "closing_debit_realized", "actual_loss_est", "actual_loss_realized",
                  "capital_saved_est", "capital_saved_realized", "collateral_salvage_pct",
                  "collateral_salvage_pct_realized", "fill_verified", "realized_round_trip_id"):
            print(f"   {k:34}: {e.get(k)}")
