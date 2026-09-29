#!/usr/bin/env python3
"""
position_book.py — Single source of truth for the LIVE position book (zero-token).

Builds a verified, broker-sourced picture of every open leg and pairs it into defined spreads:
  accounts -> equity/cash/buying power (Alpaca live)
  spreads  -> real spot, OTM buffer, mark, credit, uPL, DTE, 21-DTE deadline, gamma-zone flag
  hedges   -> long-only protection legs (tail hedges) with cost + current mark

Why: scripts used to publish hardcoded "spot $45.80 / 100% SAFE" narratives straight from literals.
This module derives safety from live prices so a short strike that is IN THE MONEY can never be
reported as safe again.

Usage:
    from position_book import build_book
    book = build_book()                     # both accounts
    for s in book["spreads"]: print(s["status_line"])
CLI:
    python3 position_book.py [--greeks]
"""
from __future__ import annotations
import json, re, ssl, datetime, urllib.request
from pathlib import Path
from typing import Any, Dict, List

from live_spot import get_spots

HERE = Path(__file__).resolve().parent
DEFAULT_ACCOUNTS = ("alpaca_live", "pion_main", "pion2_sub")
OCC = re.compile(r"^([A-Z\.]+?)(\d{6})([CP])(\d{8})$")
SAFE_OTM_PCT = 3.0        # >= 3% OTM on the short leg = comfortable
WATCH_OTM_PCT = 0.0       # between 0 and 3% = thin buffer
GAMMA_DTE = 21            # Julia Spina 21-DTE gamma-risk exit
LEG_RISK_PCT = 50.0       # flag when uPL loss >= 50% of the max defined risk


def _parse_occ(sym: str):
    m = OCC.match(sym or "")
    if not m:
        return None
    root, ymd, right, strike = m.groups()
    return {"root": root, "exp": f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:6]}",
            "right": "PUT" if right == "P" else "CALL", "strike": int(strike) / 1000.0}


def _dte(exp: str):
    try:
        return (datetime.datetime.strptime(exp, "%Y-%m-%d").date() - datetime.date.today()).days
    except Exception:
        return None


def _deadline_21dte(exp: str):
    """Calendar 21-DTE deadline (Julia Spina gamma-risk exit point)."""
    try:
        return str(datetime.datetime.strptime(exp, "%Y-%m-%d").date() - datetime.timedelta(days=GAMMA_DTE))
    except Exception:
        return None


def build_book(accounts=DEFAULT_ACCOUNTS, with_greeks: bool = False) -> Dict[str, Any]:
    import sys
    sys.path.insert(0, str(HERE))
    from alpaca_broker import AlpacaClient

    acct_info: Dict[str, Any] = {}
    legs: List[Dict[str, Any]] = []
    for acct in accounts:
        try:
            cli = AlpacaClient(acct)
            a = cli.get_account()
            acct_info[acct] = {"equity": float(a.get("equity", 0) or 0),
                               "cash": float(a.get("cash", 0) or 0),
                               "buying_power": float(a.get("buying_power", 0) or 0),
                               "long_market_value": float(a.get("long_market_value", 0) or 0),
                               "short_market_value": float(a.get("short_market_value", 0) or 0),
                               "account_id": getattr(cli, "expected_account_id", None),
                               "status": a.get("status")}
            for p in cli.get_positions():
                info = _parse_occ(p.get("symbol", ""))
                qty = float(p.get("qty", 0) or 0)
                legs.append({
                    "account": acct, "symbol": p.get("symbol"),
                    "root": info["root"] if info else None,
                    "exp": info["exp"] if info else None,
                    "right": info["right"] if info else None,
                    "strike": info["strike"] if info else None,
                    "qty": qty,
                    "avg_entry": float(p.get("avg_entry_price", 0) or 0),
                    "current_price": float(p.get("current_price", 0) or 0),
                    "market_value": float(p.get("market_value", 0) or 0),
                    "unrealized_pl": float(p.get("unrealized_pl", 0) or 0),
                    "unrealized_plpc": p.get("unrealized_plpc"),
                })
        except Exception as e:
            acct_info[acct] = {"error": str(e)}

    # Tradier Live query
    try:
        from tradier_broker import TradierClient
        t_cli = TradierClient("live")
        t_acct = t_cli.get_account()
        acct_info["tradier_live"] = {
            "equity": float(t_acct.get("total_equity", 0) or 0),
            "cash": float(t_acct.get("cash", 0) or 0),
            "buying_power": float(t_acct.get("option_buying_power", 0) or 0),
            "account_id": t_acct.get("account_number"),
            "status": "ACTIVE"
        }
        for p in t_cli.get_positions():
            info = _parse_occ(p.get("symbol", ""))
            qty = float(p.get("quantity", 0) or 0)
            legs.append({
                "account": "tradier_live", "symbol": p.get("symbol"),
                "root": info["root"] if info else None,
                "exp": info["exp"] if info else None,
                "right": info["right"] if info else None,
                "strike": info["strike"] if info else None,
                "qty": qty,
                "avg_entry": float(p.get("avg_entry_price", 0) or 0),
                "current_price": float(p.get("current_price", 0) or 0),
                "market_value": float(p.get("cost_basis", 0) or 0),
                "unrealized_pl": 0.0,
                "unrealized_plpc": 0.0,
            })
    except Exception:
        pass

    roots = sorted({l["root"] for l in legs if l["root"]})
    spots = get_spots(roots, use_cache=True) if roots else {}
    for l in legs:
        s = spots.get(l["root"] or "", {})
        l["spot"] = s.get("price")
        l["spot_source"] = s.get("source")

    # ---- pair legs into spreads (same account/root/exp/right)
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for l in legs:
        if not (l["root"] and l["exp"] and l["right"]):
            continue
        groups.setdefault((l["account"], l["root"], l["exp"], l["right"]), []).append(l)

    spreads: List[Dict[str, Any]] = []
    hedges: List[Dict[str, Any]] = []
    for (acct, root, exp, right), grp in sorted(groups.items()):
        shorts = [l for l in grp if l["qty"] < 0]
        longs = [l for l in grp if l["qty"] > 0]
        spot = grp[0]["spot"]
        dte = _dte(exp)
        if not shorts:                                   # long-only protection
            for l in longs:
                hedges.append({
                    "account": acct, "root": root, "exp": exp, "right": right,
                    "contracts": int(abs(l["qty"])), "strike": l["strike"],
                    "spot": spot, "cost_usd": round(abs(l["qty"]) * l["avg_entry"] * 100, 2),
                    "mark_usd": round(abs(l["qty"]) * (l["current_price"] or 0) * 100, 2),
                    "dte": dte,
                    "otm_pct": round((spot - l["strike"]) / spot * 100, 2) if (spot and right == "PUT") else None,
                })
            continue

        short = max(shorts, key=lambda x: abs(x["qty"]))
        long_leg = min(longs, key=lambda x: abs(x["strike"] - short["strike"])) if longs else None
        contracts = int(abs(short["qty"]))
        width = round(abs(short["strike"] - long_leg["strike"]), 2) if long_leg else None
        credit = round((short["avg_entry"] - (long_leg["avg_entry"] if long_leg else 0)) * contracts * 100, 2)
        mark = round((short["current_price"] - (long_leg["current_price"] if long_leg else 0)) * contracts * 100, 2)
        max_risk = round(width * contracts * 100, 2) if width else None
        upl = round(sum(l["unrealized_pl"] for l in grp), 2)
        if spot:
            if right == "PUT":
                otm_pct = round((spot - short["strike"]) / spot * 100, 2)
            else:
                otm_pct = round((short["strike"] - spot) / spot * 100, 2)
        else:
            otm_pct = None

        buffer_label = (f"{otm_pct}% OTM" if (otm_pct is not None and otm_pct >= 0)
                        else (f"{abs(otm_pct)}% IN THE MONEY" if otm_pct is not None else "n/a"))
        flags: List[str] = []
        if otm_pct is None:
            status = "UNKNOWN (no live spot)"
        elif otm_pct >= SAFE_OTM_PCT:
            status = f"SAFE ({otm_pct}% OTM)"
        elif otm_pct > WATCH_OTM_PCT:
            status = f"WATCH — THIN BUFFER ({otm_pct}% OTM)"
        else:
            opt_tag = "P" if right == "PUT" else "C"
            status = f"BREACH — SHORT ${short['strike']:.0f}{opt_tag} IS ITM ({abs(otm_pct)}% IN THE MONEY)"
            flags.append("ITM_SHORT_LEG")

        if dte is not None and dte <= GAMMA_DTE:
            flags.append(f"GAMMA_ZONE_{dte}DTE")
        if max_risk and abs(min(upl, 0)) >= max_risk * LEG_RISK_PCT / 100:
            flags.append("LOSS_>=50%_OF_MAX_RISK")
        if credit <= 0.05:
            flags.append("THIN_CREDIT")

        opt_tag = "P" if right == "PUT" else "C"
        l_str = f"{long_leg['strike']:.0f}{opt_tag}" if long_leg else "NAKED"
        spreads.append({
            "account": acct, "root": root, "expiry": exp, "right": right,
            "contracts": contracts,
            "short_strike": short["strike"], "long_strike": long_leg["strike"] if long_leg else None,
            "width": width, "spot": spot, "spot_source": short.get("spot_source"),
            "otm_pct": otm_pct, "dte": dte,
            "credit_usd": credit, "mark_usd": mark, "max_risk_usd": max_risk,
            "unrealized_pl": upl,
            "profit_target_50pct_usd": round(credit * 0.5, 2),
            "deadline_21dte": _deadline_21dte(exp),
            "status": status, "flags": flags,
            "status_line": (f"{acct}/{root} ${short['strike']:.0f}{opt_tag}/{l_str} {exp} "
                            f"({contracts}C) spot ${spot} | {status} | uPL ${upl} | {dte}DTE | "
                            f"+{'; '.join(flags) if flags else 'OK'}"),
        })

    # Synthesize Iron Condors where matching Put and Call spreads exist on the same account/root/expiry
    final_spreads: List[Dict[str, Any]] = []
    used_indices = set()
    put_indices = [i for i, s in enumerate(spreads) if s["right"] == "PUT"]
    call_indices = [i for i, s in enumerate(spreads) if s["right"] == "CALL"]

    for pi in put_indices:
        ps = spreads[pi]
        matched_ci = None
        for ci in call_indices:
            if ci not in used_indices:
                cs = spreads[ci]
                if cs["account"] == ps["account"] and cs["root"] == ps["root"] and cs["expiry"] == ps["expiry"]:
                    matched_ci = ci
                    break
        if matched_ci is not None:
            used_indices.add(pi)
            used_indices.add(matched_ci)
            cs = spreads[matched_ci]
            c_contracts = min(ps["contracts"], cs["contracts"])
            c_width = max(ps["width"] or 0, cs["width"] or 0)
            c_credit = round(ps["credit_usd"] + cs["credit_usd"], 2)
            c_mark = round(ps["mark_usd"] + cs["mark_usd"], 2)
            c_risk = round(c_width * c_contracts * 100, 2)
            c_upl = round(ps["unrealized_pl"] + cs["unrealized_pl"], 2)
            c_flags = list(set(ps["flags"] + cs["flags"]))

            final_spreads.append({
                "account": ps["account"], "root": ps["root"], "expiry": ps["expiry"],
                "strategy": "IRON_CONDOR",
                "contracts": c_contracts,
                "put_short": ps["short_strike"], "put_long": ps["long_strike"],
                "call_short": cs["short_strike"], "call_long": cs["long_strike"],
                "width": c_width, "spot": ps["spot"],
                "put_otm_pct": ps["otm_pct"], "call_otm_pct": cs["otm_pct"],
                "dte": ps["dte"],
                "credit_usd": c_credit, "mark_usd": c_mark, "max_risk_usd": c_risk,
                "unrealized_pl": c_upl,
                "profit_target_50pct_usd": round(c_credit * 0.5, 2),
                "status": f"IRON CONDOR (P: {ps['otm_pct']}% OTM | C: {cs['otm_pct']}% OTM)",
                "flags": c_flags,
                "status_line": (f"{ps['account']}/{ps['root']} IC Put {ps['short_strike']:.0f}P/{ps['long_strike'] or 0:.0f}P | "
                                f"Call {cs['short_strike']:.0f}C/{cs['long_strike'] or 0:.0f}C {ps['expiry']} ({c_contracts}C) "
                                f"spot ${ps['spot']} | uPL ${c_upl} | 50% TP ${c_credit*0.5:.2f} | +{'; '.join(c_flags) if c_flags else 'OK'}")
            })
        else:
            final_spreads.append(ps)

    for ci in call_indices:
        if ci not in used_indices:
            final_spreads.append(spreads[ci])

    spreads = final_spreads

    return {
        "asof": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT"),
        "accounts": acct_info,
        "legs": legs,
        "spreads": spreads,
        "hedges": hedges,
        "totals": {
            "equity": round(sum(a.get("equity", 0) for a in acct_info.values()), 2),
            "cash": round(sum(a.get("cash", 0) for a in acct_info.values()), 2),
            "buying_power": round(sum(a.get("buying_power", 0) for a in acct_info.values()), 2),
            "deployed_max_risk": round(sum(s.get("max_risk_usd") or 0 for s in spreads), 2),
        },
    }


if __name__ == "__main__":
    import sys
    book = build_book()
    print(json.dumps({"asof": book["asof"], "totals": book["totals"], "accounts": book["accounts"]},
                     indent=2))
    print("\nSPREADS")
    for s in book["spreads"]:
        print("  " + s["status_line"])
    print("\nHEDGES")
    for h in book["hedges"]:
        print(f"  {h['account']}/{h['root']} {h['strike']}{h['right'][0]} {h['exp']} "
              f"({h['contracts']}C) cost ${h['cost_usd']} mark ${h['mark_usd']} spot ${h['spot']}")
