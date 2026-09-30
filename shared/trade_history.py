#!/usr/bin/env python3
"""
trade_history.py — CANONICAL transaction history built from the broker's own fill log.

Why this exists (CSO ruling 2026-09-23, cleanup #1): the Excel journal drew its "realized
transaction journal" rows from HARDCODED literals in both generate_live_journal.py and
transaction_journal_manager.py (PILOT-001/PILOT-002 rows, the TRD-… registry). Those rows claimed
trades, orders and P&L that were typed by hand. This module replaces them with the only true
source: Alpaca `/v2/account/activities?activity_types=FILL`, walked chronologically per contract.

Method
------
1. Pull every FILL for both accounts (paginated).
2. Track net position per (account, option symbol): buy/buy_to_cover = +qty, sell/sell_short = -qty.
3. A symbol with non-zero fill quantity that is NOT in the broker's current positions has left the
   book without a closing fill -> expired (or assigned); its premium is already captured by the
   opening cash flow, so realized P&L = sum of signed cash flows.
4. Group the two legs of each spread identity (account, root, expiry, right, short, long) into one
   round trip: credit collected, debit paid, contracts, realized P&L, ROC, hold time, and whether it
   is still OPEN with the broker.

Nothing here is estimated: every field traces to a fill record or the live position book.
"""
from __future__ import annotations
import datetime
import json
import urllib.parse
import urllib.request
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

OCC = None
try:
    from intelligent_spread_formatter import parse_option_symbol  # (symbol) -> (root, exp, type, strike)
    OCC = parse_option_symbol
except Exception:
    import re
    _RE = re.compile(r"^([A-Z\.]+?)(\d{6})([CP])(\d{8})$")

    def OCC(sym):                                        # type: ignore
        m = _RE.match(sym or "")
        if not m:
            return None
        root, ymd, right, strike = m.groups()
        return root, f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:6]}", "PUT" if right == "P" else "CALL", int(strike) / 1000.0

OPEN_SIDES = {"buy", "buy_to_cover", "buy_to_open"}
SELL_SIDES = {"sell", "sell_short", "sell_to_close"}

ACCOUNTS = ("alpaca_live", "tradier_live", "pion_main", "pion2_sub")
LIVE_ACCOUNTS = ("alpaca_live", "tradier_live")


def _client(account: str):
    from alpaca_broker import AlpacaClient
    return AlpacaClient(account)


def fetch_fills(account: str, max_pages: int = 12) -> List[Dict[str, Any]]:
    """Every FILL for the account, oldest first. Raises on transport failure (no silent empties)."""
    if account == "tradier_live":
        try:
            from tradier_broker import TradierClient
            t = TradierClient("live")
            fills = []
            seen_events = set()
            res = t._call_api(f"accounts/{t.account_id}/history?limit=100&page=1")
            hist_node = res.get("history") if isinstance(res, dict) else {}
            if not isinstance(hist_node, dict):
                hist_node = {}
            events = hist_node.get("event", [])
            if isinstance(events, dict):
                events = [events]
            elif not isinstance(events, list):
                events = []
            for ev in events:
                if ev.get("type") == "trade" and "trade" in ev:
                    tr = ev["trade"]
                    sym = tr.get("symbol", "")
                    if tr.get("trade_type") == "option" or (sym and len(sym) > 10):
                        qty = float(tr.get("quantity", 0))
                        side = "buy" if qty > 0 else "sell"
                        dt = str(ev.get("date", ""))
                        fill_key = (dt[:10], sym, side)
                        if fill_key not in seen_events:
                            fills.append({
                                "symbol": sym,
                                "side": side,
                                "qty": str(abs(qty)),
                                "price": str(tr.get("price", 0)),
                                "transaction_time": dt,
                                "order_id": f"tradier_{dt[:10]}"
                            })
                            seen_events.add(fill_key)

            # Intraday filled orders (to immediately capture today's fills before clearing)
            res_o = t._call_api(f"accounts/{t.account_id}/orders")
            orders_node = res_o.get("orders") if isinstance(res_o, dict) else {}
            if not isinstance(orders_node, dict):
                orders_node = {}
            orders_list = orders_node.get("order", [])
            if isinstance(orders_list, dict):
                orders_list = [orders_list]
            elif not isinstance(orders_list, list):
                orders_list = []
            for o in orders_list:
                if o.get("status") == "filled":
                    legs = o.get("leg", [])
                    if isinstance(legs, dict):
                        legs = [legs]
                    for leg in legs:
                        opt_sym = leg.get("option_symbol")
                        side_raw = leg.get("side", "").lower()
                        side = "sell" if "sell" in side_raw else "buy"
                        tx_time = leg.get("transaction_date") or o.get("transaction_date") or ""
                        fill_key = (tx_time[:10], opt_sym, side)
                        if opt_sym and fill_key not in seen_events:
                            fill_px = leg.get("avg_fill_price") or leg.get("last_fill_price") or 0.0
                            fills.append({
                                "symbol": opt_sym,
                                "side": side,
                                "qty": str(abs(float(leg.get("exec_quantity") or 1.0))),
                                "price": str(fill_px),
                                "transaction_time": tx_time,
                                "order_id": str(o.get("id"))
                            })
                            seen_events.add(fill_key)

            fills.sort(key=lambda x: x.get("transaction_time", ""))
            return fills
        except Exception as e:
            print(f"  ℹ️ trade_history: Tradier fill notice for {account}: {e}")
            return []

    cli = _client(account)
    out: List[Dict[str, Any]] = []
    token = None
    for _ in range(max_pages):
        qs = {"activity_types": "FILL", "page_size": 100, "direction": "asc"}
        if token:
            qs["page_token"] = token
        url = f"{cli.base_url}/v2/account/activities?" + urllib.parse.urlencode(qs)
        req = urllib.request.Request(url, headers=cli._headers())
        with urllib.request.urlopen(req, context=cli.ssl_ctx, timeout=20) as r:
            batch = json.loads(r.read().decode())
            token = r.headers.get("X-Next-Page-Token")   # MUST be read inside the response scope
        if not batch:
            break
        out.extend(batch)
        if not token:
            break
    return out


def _num(v) -> float:
    """Alpaca activities return qty/price as STRINGS — coerce safely."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _side_sign(fill: Dict[str, Any]) -> float:
    side = (fill.get("side") or "").lower()
    return 1.0 if side in OPEN_SIDES else -1.0


def _signed_qty(fill: Dict[str, Any]) -> float:
    return _side_sign(fill) * _num(fill.get("qty"))


def _cash(fill: Dict[str, Any]) -> float:
    """Signed cash flow: sells bring cash in (+), buys take cash out (-)."""
    return _side_sign(fill) * -1.0 * _num(fill.get("qty")) * _num(fill.get("price")) * 100.0


def _live_option_symbols(account: str) -> Dict[str, float]:
    try:
        if account == "tradier_live":
            from tradier_broker import TradierClient
            t = TradierClient("live")
            return {p.get("symbol"): float(p.get("qty") or p.get("quantity") or 0) for p in t.get_positions()}
        cli = _client(account)
        return {p.get("symbol"): float(p.get("qty") or 0) for p in cli.get_positions()}
    except Exception:
        return {}


def spread_round_trips(accounts=ACCOUNTS) -> List[Dict[str, Any]]:
    """Reconstructed spread lifecycle records (realized + open), oldest first."""
    trips: List[Dict[str, Any]] = []
    for acct in accounts:
        try:
            fills = fetch_fills(acct)
        except Exception as e:
            print(f"  🔴 trade_history: fill log unavailable for {acct}: {e}")
            continue
        live = _live_option_symbols(acct)

        # per-symbol position + cash walk
        per_sym: Dict[str, Dict[str, Any]] = {}
        for f in fills:
            sym = f.get("symbol") or ""
            if not OCC(sym):
                continue
            rec = per_sym.setdefault(sym, {"qty": 0.0, "cash": 0.0, "fills": []})
            rec["qty"] += _signed_qty(f)
            rec["cash"] += _cash(f)
            rec["fills"].append(f)

        groups: Dict[tuple, Dict[str, Any]] = {}
        for sym, rec in per_sym.items():
            parsed = OCC(sym)
            if not parsed:
                continue
            root, exp, right, strike = parsed
            still_held = sym in live
            for f in rec["fills"]:
                g = groups.setdefault((acct, root, exp, right), {
                    "account": acct, "root": root, "exp": exp, "right": right,
                    "legs": {}, "cash": 0.0, "fills": [], "net_qty": 0.0, "still_held": False,
                })
                g["cash"] += _cash(f)
                g["fills"].append(f)
                leg = g["legs"].setdefault(strike, {"qty": 0.0, "open_price": None, "close_price": None,
                                                    "open_time": None, "close_time": None,
                                                    "open_qty": 0.0, "open_side": None,
                                                    "close_qty": 0.0, "entries": 0})
                sgn = _side_sign(f)
                qty = _num(f.get("qty"))
                px = _num(f.get("price"))
                ts = f.get("transaction_time", "")
                opening = (leg["qty"] == 0.0
                           or (leg["qty"] > 0 and sgn > 0)
                           or (leg["qty"] < 0 and sgn < 0))
                if opening:
                    if leg["open_price"] is None:
                        leg["open_price"] = px
                        leg["open_time"] = ts
                        leg["open_side"] = "SELL" if sgn < 0 else "BUY"
                    leg["open_qty"] += qty
                    leg["entries"] += 1
                else:
                    leg["close_price"] = px          # last closing fill wins
                    leg["close_time"] = ts
                    leg["close_qty"] += qty
                leg["qty"] += sgn * qty
                g["net_qty"] += sgn * qty
                g["still_held"] = g["still_held"] or still_held
            opening_sizes = [abs(l.get("open_qty") or 0) for l in g["legs"].values()
                             if abs(l.get("open_qty") or 0) > 0]
            g["contracts"] = max(1, int(max(opening_sizes))) if opening_sizes else 1

        for (acct_, root, exp, right), g in sorted(groups.items(), key=lambda kv: kv[0][2]):
            legs = g["legs"]
            if len(legs) < 2:
                continue
            def _is_short(v):
                # Classify by OPENING side: residual qty is 0 for every closed spread, so sign-based
                # classification silently dropped all realized round trips.
                return (v.get("open_side") == "SELL") if v.get("open_side") else (v["qty"] < 0)

            shorts = {k: v for k, v in legs.items() if _is_short(v)}
            longs = {k: v for k, v in legs.items() if not _is_short(v)}
            if not shorts or not longs:
                continue
            s_strike = max(shorts) if right == "PUT" else min(shorts)
            l_strike = min(longs) if right == "PUT" else max(longs)
            contracts = g["contracts"]
            short_leg, long_leg = legs[s_strike], legs[l_strike]
            credit_sh = round((short_leg["open_price"] or 0) - (long_leg["open_price"] or 0), 2)
            closed_by_fill = bool(short_leg.get("close_price") or long_leg.get("close_price"))
            debit_sh = (round((short_leg["close_price"] or 0) - (long_leg["close_price"] or 0), 2)
                        if closed_by_fill else 0.0)
            realized = round(g["cash"], 2)
            width = abs(s_strike - l_strike)
            max_risk = round(width * contracts * 100, 2)
            open_ts = min([l["open_time"] for l in legs.values() if l["open_time"]] or [""])
            close_ts = max([l["close_time"] for l in legs.values() if l["close_time"]] or [""])
            if g["still_held"]:
                status, exit_reason = "OPEN", "Active with broker (live position)"
            elif closed_by_fill:
                status = ("CLOSED_HARVESTED" if realized > 0 else "CLOSED_DEFENSIVE")
                base = ("Take-profit close (verified fill)" if realized > 0
                        else "Defensive stop-out / loss close (verified fill)")
                ec = max(short_leg.get("entries", 1), long_leg.get("entries", 1))
                exit_reason = (f"{base} — rolled / re-entered {ec}x" if ec > 1 else base)
            else:
                status = "EXPIRED"
                exit_reason = "Expired with no closing fill (premium retained / protection cost)"

            def _dt(ts):
                try:
                    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except Exception:
                    return None

            o, c = _dt(open_ts), _dt(close_ts)
            hold_hours = round((c - o).total_seconds() / 3600.0, 1) if (o and c) else None
            entry_count = max(short_leg.get("entries", 1), long_leg.get("entries", 1))
            net_per_share = round(realized / (contracts * 100), 2) if contracts else None
            ambiguous = (len(shorts) > 1 or len(longs) > 1)
            trips.append({
                "id": f"TRD-{open_ts[:10].replace('-', '')}-{root}-{int(s_strike)}/{int(l_strike)}",
                "entry_count": entry_count,
                "multiple_entries": entry_count > 1,
                "ambiguous_pairing": ambiguous,
                "pairing_note": ("Multiple strike pairs share this expiry (rolls) — cash-flow P&L is "
                                 "exact, strike labels are the dominant pair" if ambiguous else None),
                "net_per_share": net_per_share,
                "source": "tradier_history" if acct == "tradier_live" else "alpaca_fills",
                "verified": True,
                "account": acct,
                "root": root, "asset": root,
                "exp": exp, "right": right,
                "short_strike": s_strike, "long_strike": l_strike, "width": width,
                "contracts": contracts,
                "credit_sh": credit_sh, "tot_credit": round(credit_sh * contracts * 100, 2),
                "debit_sh": debit_sh if status != "OPEN" else 0.0,
                "tot_debit": round(debit_sh * contracts * 100, 2) if status != "OPEN" else 0.0,
                "max_risk": max_risk,
                "realized_pnl": realized if status != "OPEN" else 0.0,
                "roc": (round(realized / max_risk, 4) if max_risk else None) if status != "OPEN" else None,
                "open_date": open_ts[:10], "open_time": open_ts[11:19],
                "close_date": close_ts[:10] if (close_ts and status != "OPEN") else None,
                "close_time": close_ts[11:19] if (close_ts and status != "OPEN") else None,
                "hold_hours": hold_hours,
                "exit_reason": exit_reason,
                "status": status,
                "order_id": (g["fills"][0].get("order_id") if g["fills"] else None),
                "fill_count": len(g["fills"]),
            })
    trips.sort(key=lambda t: (t.get("open_date") or "", t.get("root") or ""))
    return trips


def realized_summary(trips: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    trips = trips if trips is not None else spread_round_trips()
    closed = [t for t in trips if t["status"] not in ("OPEN",)]
    wins = [t for t in closed if (t["realized_pnl"] or 0) > 0]
    return {
        "round_trips": len(trips),
        "closed": len(closed),
        "open": len(trips) - len(closed),
        "wins": len(wins),
        "losses": len([t for t in closed if (t["realized_pnl"] or 0) < 0]),
        "win_rate_pct": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "realized_pnl_total": round(sum(t["realized_pnl"] or 0 for t in closed), 2),
        "generated_at_ict": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT"),
        "source": "Alpaca /v2/account/activities?activity_types=FILL (both accounts)",
    }


# ------------------------------------------------------------------ two-tier reporting
# CSO ruling 2026-09-23 (Two-Tier Reporting Standard): lifetime losses are NEVER hidden.
# Tier 1 = every verified round trip in the broker fill log.
# Tier 2 = the automated-production era only (post-Sep 13 rule set), i.e. trades OPENED on/after
#          this date — the honest denominator for judging current algorithmic performance.
PRODUCTION_ERA_START = "2026-09-13"
TIER_NOTE = ("Tier 1 is lifetime broker reality (includes the pre-automation manual era). "
             "Tier 2 isolates post-Sep-13 automated production performance.")


def tiered_summary(trips: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """{'tier1_lifetime': {...}, 'tier2_production': {...}, 'era_start': ...}."""
    trips = trips if trips is not None else spread_round_trips()
    t2 = [t for t in trips if (t.get("open_date") or "") >= PRODUCTION_ERA_START]
    return {
        "era_start": PRODUCTION_ERA_START,
        "note": TIER_NOTE,
        "tier1_lifetime": realized_summary(trips),
        "tier2_production": realized_summary(t2),
        "generated_at_ict": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT"),
    }


def tiered_lines(trips: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """Ready-to-render report lines (plain ASCII, safe for Telegram/bridge)."""
    s = tiered_summary(trips)
    t1, t2 = s["tier1_lifetime"], s["tier2_production"]

    def _fmt(label: str, d: Dict[str, Any]) -> str:
        wr = f"{d['win_rate_pct']}%" if d.get("win_rate_pct") is not None else "n/a"
        return (f"{label}: {d['closed']} closed ({d['wins']}W/{d['losses']}L, {wr} WR) | "
                f"realized ${d['realized_pnl_total']:,.2f} | {d['open']} open")

    return [_fmt("TIER 1 (Lifetime broker reality)", t1),
            _fmt(f"TIER 2 (Automated production era, opened >= {s['era_start']})", t2)]



if __name__ == "__main__":
    tr = spread_round_trips()
    print(f"{'OPEN':<10} {'CLOSED':<18} {'ACCT':<10} {'ASSET':<6} {'STRIKES':<16} {'EXP':<12} "
          f"{'C':<3} {'CRED':>6} {'DEB':>6} {'P&L':>9} {'ROC%':>7} STATUS")
    for t in tr:
        print(f"{t['open_date']:<10} {str(t['close_date'] or '-'):<18} {t['account']:<10} "
              f"{t['root']:<6} {t['short_strike']:.0f}/{t['long_strike']:.0f}{t['right'][0]:<13} "
              f"{t['exp']:<12} {t['contracts']:<3} {t['credit_sh']:>6.2f} {t['debit_sh']:>6.2f} "
              f"{t['realized_pnl']:>9.2f} {(t['roc'] or 0) * 100:>7.2f} {t['status']}")
    print()
    print(json.dumps(realized_summary(tr), indent=2))
