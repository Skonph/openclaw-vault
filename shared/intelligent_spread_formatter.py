#!/usr/bin/env python3
"""
intelligent_spread_formatter.py — Unified Quantitative Spread Intelligence Engine (RULE-060)

Transforms raw broker leg data into clear, institutional-grade spread intelligence for Anna & Hermes:
1. Automatically pairs long and short legs by underlying asset into true defined spreads.
2. Computes spot price, strike distance, safety buffer %, upfront credit, and net MTM.
3. Provides actionable health verdicts (SAFE, HARVEST_READY, THETA_BURNING, DEFENSE_ALERT).
4. Formats gorgeous, human-readable Telegram & daily report blocks worth real attention!
"""

from typing import List, Dict, Any, Tuple
import datetime

# Underlyings Spot Price Map / Metadata
ASSET_METADATA = {
    "LMT": {"name": "Lockheed Martin", "theme": "5. Consumer Staples & National Defense", "spot": 0.0},
    "GLD": {"name": "SPDR Gold Trust", "theme": "3. Inflation Defense & Hard Assets", "spot": 0.0},
    "XLU": {"name": "Utilities Select SPDR", "theme": "2. Data Center Power & Cooling", "spot": 0.0},
    "XLF": {"name": "Financial Select SPDR", "theme": "6. Financial Services & Payment Rails", "spot": 0.0},
    "NVDA": {"name": "NVIDIA Corp", "theme": "1. AI Chips & Hardware", "spot": 0.0},
    "AMD": {"name": "Advanced Micro Devices", "theme": "1. AI Chips & Hardware", "spot": 0.0},
    "AVGO": {"name": "Broadcom Inc", "theme": "1. AI Chips & Hardware", "spot": 0.0},
    "TSM": {"name": "Taiwan Semiconductor", "theme": "1. AI Chips & Hardware", "spot": 0.0},
    "CEG": {"name": "Constellation Energy", "theme": "2. Data Center Power & Cooling", "spot": 0.0},
    "VRT": {"name": "Vertiv Holdings", "theme": "2. Data Center Power & Cooling", "spot": 0.0},
    "GE": {"name": "GE Aerospace", "theme": "2. Data Center Power & Cooling", "spot": 0.0},
    "XLE": {"name": "Energy Select Sector SPDR", "theme": "3. Inflation Defense & Hard Assets", "spot": 0.0},
    "XLV": {"name": "Health Care Select Sector SPDR", "theme": "4. Longevity & Healthcare", "spot": 0.0},
    "UNH": {"name": "UnitedHealth Group", "theme": "4. Longevity & Healthcare", "spot": 0.0},
    "JNJ": {"name": "Johnson & Johnson", "theme": "4. Longevity & Healthcare", "spot": 0.0},
    "XLP": {"name": "Consumer Staples Select Sector SPDR", "theme": "5. Consumer Staples & National Defense", "spot": 0.0},
    "JPM": {"name": "JPMorgan Chase & Co", "theme": "6. Financial Services & Payment Rails", "spot": 0.0},
    "V": {"name": "Visa Inc", "theme": "6. Financial Services & Payment Rails", "spot": 0.0},
    "SPY": {"name": "S&P 500 ETF Trust", "theme": "Macro Broad Market Index", "spot": 0.0}
}

def _load_catalog_metadata():
    """Dynamically ingests all 36 assets from verified_universe_catalog.json."""
    import json
    from pathlib import Path
    for p in (Path("/home/ubuntu/shared/verified_universe_catalog.json"), Path(__file__).resolve().parent / "verified_universe_catalog.json"):
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                for theme, cands in data.items():
                    for c in cands:
                        sym = c.get("symbol")
                        if sym:
                            ASSET_METADATA.setdefault(sym, {})
                            ASSET_METADATA[sym]["name"] = c.get("name", sym)
                            ASSET_METADATA[sym]["theme"] = theme
                            if "spot" not in ASSET_METADATA[sym]:
                                ASSET_METADATA[sym]["spot"] = 0.0
                break
            except Exception:
                pass

_load_catalog_metadata()

def lookup_active_trade_credit(underlying: str) -> float | None:
    """Check active_trades.json for confirmed entry fill credit if broker marks are degenerate."""
    import json
    from pathlib import Path
    for p in (Path("/home/ubuntu/shared/active_trades.json"), Path(__file__).resolve().parent / "active_trades.json"):
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                for acct in data.get("accounts", {}).values():
                    for pos in acct.get("positions", []):
                        if pos.get("symbol") == underlying and pos.get("net_credit") is not None:
                            return float(pos["net_credit"])
            except Exception:
                pass
    return None

# ⚠️ REFERENCE-ONLY spot book (last known values). NEVER used for a safety verdict: `resolve_spot()`
# fetches the LIVE price (Tradier -> Finnhub -> Alpaca) and falls back to this book only to DISPLAY a
# clearly-labelled stale value. Any hardcoded literal here must never masquerade as a live quote.
_LIVE_SPOT_CACHE: Dict[str, Any] = {}
_LIVE_SPOT_TTL = 300


def resolve_spot(underlying: str) -> Tuple[float, str]:
    """Return (live_spot, source). source='live:tradier' ... or 'stale_reference' / 'unavailable'."""
    import time
    hit = _LIVE_SPOT_CACHE.get(underlying)
    if hit and (time.time() - hit[0]) <= _LIVE_SPOT_TTL:
        return hit[1], hit[2]
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _sys.path.insert(0, str(_Path(__file__).resolve().parent))
        from live_spot import get_spot
        r = get_spot(underlying)
        if r.get("price"):
            _LIVE_SPOT_CACHE[underlying] = (time.time(), float(r["price"]), f"live:{r.get('source')}")
            return float(r["price"]), f"live:{r.get('source')}"
    except Exception:
        pass
    ref = (ASSET_METADATA.get(underlying) or {}).get("spot") or 0.0
    return float(ref), ("stale_reference" if ref else "unavailable")


def refresh_live_spots(symbols=None) -> Dict[str, str]:
    """Overwrite the reference book with LIVE prices (for report aesthetics + audit)."""
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _sys.path.insert(0, str(_Path(__file__).resolve().parent))
        from live_spot import get_spots
        book = get_spots(symbols or list(ASSET_METADATA), use_cache=False)
    except Exception:
        return {}
    out = {}
    for s, r in book.items():
        if r.get("price"):
            ASSET_METADATA.setdefault(s, {"name": s, "theme": "Quantitative Asset"})
            ASSET_METADATA[s]["spot"] = r["price"]
            ASSET_METADATA[s]["spot_source"] = r.get("source")
            out[s] = r.get("source")
    return out


US_MARKET_HOLIDAYS_2026 = {
    "2026-01-01": "New Year's Day",
    "2026-01-19": "Martin Luther King Jr. Day",
    "2026-02-16": "Washington's Birthday / Presidents' Day",
    "2026-04-03": "Good Friday",
    "2026-05-25": "Memorial Day",
    "2026-06-19": "Juneteenth National Independence Day",
    "2026-07-03": "Independence Day (Observed)",
    "2026-09-07": "Labor Day",
    "2026-11-26": "Thanksgiving Day",
    "2026-12-25": "Christmas Day"
}

def get_t_minus_trading_days(exp_date: datetime.date, trading_days: int = 3) -> datetime.date:
    """
    RULE-076: Holiday-Aware T-3 Rollover Deadline Calculator.
    Calculates the date exactly `trading_days` active trading sessions prior to expiration,
    skipping weekends and US exchange market holidays to guarantee market accessibility.
    """
    current = exp_date
    count = 0
    while count < trading_days:
        current -= datetime.timedelta(days=1)
        if current.weekday() >= 5: # Saturday or Sunday
            continue
        if current.strftime("%Y-%m-%d") in US_MARKET_HOLIDAYS_2026:
            continue
        count += 1
    return current

def parse_option_symbol(symbol: str) -> Tuple[str, str, str, float]:
    """Parses OSI option symbol into (underlying, exp_date, opt_type, strike)."""
    if len(symbol) < 15 or ("P0" not in symbol and "C0" not in symbol):
        return symbol, "", "", 0.0
    try:
        opt_type = "P" if "P0" in symbol else "C"
        parts = symbol.split(opt_type + "0")
        prefix = parts[0]
        strike = float(parts[1]) / 1000.0 if len(parts) > 1 else 0.0
        
        # Extract underlying and expiration
        exp_part = prefix[-6:]
        underlying = prefix[:-6]
        exp_date = f"20{exp_part[:2]}-{exp_part[2:4]}-{exp_part[4:]}"
        return underlying, exp_date, opt_type, strike
    except Exception:
        return symbol[:4], "", "P", 0.0

def calculate_condor_margin(put_width: float, call_width: float, contracts: int) -> float:
    """RULE-094: Calculates single-margin collateral for an Iron Condor (wider wing rule)."""
    return max(put_width, call_width) * contracts * 100.0

def format_iron_condor_key(account: str, symbol: str, put_short: float, put_long: float, call_short: float, call_long: float, exp_date: str) -> str:
    """RULE-094: Generates deterministic composite spread key for Iron Condors."""
    return f"{account}:{symbol}:{put_short:.1f}P_{put_long:.1f}P_{call_short:.1f}C_{call_long:.1f}C_{exp_date}"

def format_intelligent_spread_report(pos_list: List[Dict[str, Any]], account_name: str = "Account") -> str:
    """
    RULE-060: Produces institutional, meaningful spread intelligence for Telegram and Daily Reports.
    """
    if not pos_list:
        return f"• {account_name}: 100% Cash Defense Floor / No Open Market Risk ✅"

    # 1. Group by underlying
    groups = {}
    for p in pos_list:
        s = p.get("symbol", "")
        q = int(float(p.get("qty", p.get("quantity", 0))))
        val = float(p.get("market_value", 0))
        pnl = float(p.get("unrealized_pl", 0))
        # Robust per-share avg_entry extraction
        raw_avg = p.get("avg_entry_price")
        if raw_avg is not None and float(raw_avg) > 0:
            avg_entry = float(raw_avg)
        elif "cost_basis" in p and float(p.get("cost_basis", 0)) != 0:
            cb = abs(float(p.get("cost_basis", 0)))
            # If cost_basis is total dollar value, convert to per-share premium:
            avg_entry = (cb / (abs(q) * 100.0)) if abs(q) > 0 else (cb / 100.0)
        else:
            avg_entry = 0.0

        underlying, exp_date, opt_type, strike = parse_option_symbol(s)
        groups.setdefault(underlying, []).append({
            "symbol": s,
            "qty": q,
            "market_value": val,
            "unrealized_pl": pnl,
            "avg_entry": avg_entry,
            "exp_date": exp_date,
            "opt_type": opt_type,
            "strike": strike
        })

    report_blocks = []
    
    for underlying, legs in groups.items():
        meta = ASSET_METADATA.get(underlying, {"name": underlying, "theme": "Quantitative Asset", "spot": 0.0})
        spot, spot_source = resolve_spot(underlying)
        spot_is_live = spot_source.startswith("live:")

        # Check if paired spread
        if len(legs) >= 2 and any(l["qty"] < 0 for l in legs) and any(l["qty"] > 0 for l in legs):
            short_leg = next(l for l in legs if l["qty"] < 0)
            long_leg = next(l for l in legs if l["qty"] > 0)
            
            contracts = abs(short_leg["qty"])
            s_strike = short_leg["strike"]
            l_strike = long_leg["strike"]
            exp_date = short_leg["exp_date"]
            
            # Net Spread Valuation & Income Generated
            defined_risk = abs(s_strike - l_strike) * 100.0 * contracts
            s_entry = abs(short_leg.get("avg_entry", 0))
            l_entry = abs(long_leg.get("avg_entry", 0))

            strike_width = abs(s_strike - l_strike)
            # Unit scaling guard: If s_entry or l_entry are total dollars instead of per-share premium
            # (e.g. 26.0 for a $1.00 wide spread), scale back by 100.0:
            if strike_width > 0:
                if s_entry > strike_width * 2.0:
                    s_entry = s_entry / 100.0
                if l_entry > strike_width * 2.0:
                    l_entry = l_entry / 100.0

            # Entry credit: REAL broker fills prioritized. If broker paper marks are 0,
            # query confirmed fill in active_trades.json before flagging unknown credit.
            if s_entry > 0 and l_entry > 0 and s_entry > l_entry:
                gross_short_income = s_entry * contracts * 100.0
                long_hedge_cost = l_entry * contracts * 100.0
                net_credit_injected = gross_short_income - long_hedge_cost
                credit_known = True
            elif s_entry > 0 and l_entry > 0:
                gross_short_income = s_entry * contracts * 100.0
                long_hedge_cost = l_entry * contracts * 100.0
                net_credit_injected = gross_short_income - long_hedge_cost
                credit_known = True
            else:
                trade_credit = lookup_active_trade_credit(underlying)
                if trade_credit is not None and trade_credit > 0:
                    net_credit_injected = trade_credit * contracts * 100.0
                    gross_short_income = net_credit_injected
                    long_hedge_cost = 0.0
                    credit_known = True
                else:
                    gross_short_income = long_hedge_cost = 0.0
                    net_credit_injected = 0.0
                    credit_known = False

            # Invariant: Net credit cannot exceed max defined risk
            if defined_risk > 0 and net_credit_injected > defined_risk:
                gross_short_income /= 100.0
                long_hedge_cost /= 100.0
                net_credit_injected /= 100.0

            tp_target = net_credit_injected * 0.50

            # Dates & Worst-Case Rollover Deadline (RULE-076 vs RULE-077)
            today_date = datetime.date.today()
            try:
                exp_dt = datetime.datetime.strptime(exp_date, "%Y-%m-%d").date()
                roll_dt = get_t_minus_trading_days(exp_dt, trading_days=3)
                rollover_deadline = roll_dt.strftime("%b %d, %Y")
                exp_display = exp_dt.strftime("%b %d, %Y")
                dte_days = (exp_dt - today_date).days
            except Exception:
                rollover_deadline = "T-3 Trading Days"
                exp_display = exp_date
                dte_days = 30

            # Spot vs Strike Safety Buffer — derived from the LIVE spot only.
            dist_pct = 0.0
            is_put = short_leg.get("type", "P") == "P"
            if spot > 0 and s_strike > 0:
                dist = (spot - s_strike) if is_put else (s_strike - spot)
                dist_pct = (dist / spot) * 100.0
                live_tag = "" if spot_is_live else " (STALE REFERENCE — verify!)"
                if dist >= 0:
                    safety_str = f"+${dist:.2f} (+{dist_pct:.1f}%) OTM buffer{live_tag}"
                    health = ("100% SAFE (theta burning)" if dist_pct >= 3.0
                              else "THIN BUFFER — WATCH CLOSELY ⚠️")
                else:
                    opt_lbl = "PUT" if is_put else "CALL"
                    safety_str = f"-${abs(dist):.2f} ({abs(dist_pct):.1f}%) IN-THE-MONEY{live_tag}"
                    health = f"🔴 DEFENSE REQUIRED — SHORT {opt_lbl} STRIKE IS ITM"
            else:
                safety_str = "UNKNOWN — no live spot available ⚠️"
                health = "STATUS UNKNOWN (data gap — do not assume safe)"
            if dte_days <= 21 and "🔴" not in health:
                health += f" | 21-DTE GAMMA ZONE ({dte_days} DTE)"

            # Intelligent Rollover vs Terminal Theta Resolution (RULE-077)
            if dte_days <= 3 and dist_pct >= 2.5:
                close_line = (
                    f"• Strategy Mode   : RULE-077 Terminal Theta Run (Holding to 0 DTE Expiry {exp_display} | 100% Cash Capture Imminent 🎯)\n"
                    f"  • Worst-Case Close: Expiration: {exp_display} ⏳ (Rollover Bypassed: Safe Buffer +{dist_pct:.1f}% >= 2.5% 🛡️)"
                )
            elif dte_days <= 3 and dist_pct < 2.5:
                close_line = (
                    f"• Strategy Mode   : GAMMA DEFENSE ROLLOVER REQUIRED ⚠️\n"
                    f"  • Worst-Case Close: Emergency Roll or Close immediately (Exp: {exp_display} | Buffer: {dist_pct:.1f}% < 2.5%)"
                )
            else:
                close_line = f"• Worst-Case Close: Rollover Deadline: {rollover_deadline} (T-3 DTE) | Expiration: {exp_display} ⏳"

            express_tp = net_credit_injected * 0.30
            income_line = (f"+${gross_short_income:,.2f} Short Sale Credit (-${long_hedge_cost:,.2f} Hedge) "
                           f"= +${net_credit_injected:,.2f} Net Bank Cash Injected 💵" if credit_known
                           else "UNAVAILABLE — broker entry prices missing (not estimated)")
            block = f"""🛡️ {underlying} ${s_strike:.0f}P / ${l_strike:.0f}P ({contracts}C Bull Put Spread | {meta['theme']})
  • Spot vs Strike  : ${spot:,.2f} vs ${s_strike:.0f}P ({safety_str}) [src: {spot_source}]
  • Real Cash Income: {income_line}
  • Defined Risk Cap: ${defined_risk:,.2f} Max Risk (Collateral Locked)
  • Profit Target   : RULE-077 Tiered Harvest (30% Express: +${express_tp:,.2f} | 50% Standard: +${tp_target:,.2f}) 🌾
  {close_line}
  • Strategy Status : {health}"""
            report_blocks.append(block)

        else:
            # Standalone / Tail Hedge Leg
            for l in legs:
                q = l["qty"]
                strike = l["strike"]
                pnl = l["unrealized_pl"]
                side_str = "Long Put Floor" if q > 0 else "Short Put"
                block = f"""📦 {underlying} ${strike:.0f}P ({abs(q)}C {side_str} | Exp: {l['exp_date']})
  • Market Value    : ${l['market_value']:+,.1f} | Unrealized P/L: ${pnl:+,.1f}
  • Role            : Tail-Hedge Disaster Floor / Margin Buffer"""
                report_blocks.append(block)

    return "\n\n".join(report_blocks)

def format_account_spreads_summary(client, pos_list: List[Dict[str, Any]]) -> str:
    """Formats one-line summary of account positions for briefs."""
    if not pos_list:
        return "100% Cash Defense Floor / No Open Positions ✅"
    acct_label = client.account_type.replace("_", " ").title() if hasattr(client, "account_type") else "Account"
    return format_intelligent_spread_report(pos_list, acct_label)

if __name__ == "__main__":
    # Test Formatting
    test_pos = [
        {"symbol": "LMT260918P00540000", "qty": 8, "market_value": 2880.0, "unrealized_pl": -2000.0, "avg_entry_price": 6.10},
        {"symbol": "LMT260918P00545000", "qty": -8, "market_value": -5840.0, "unrealized_pl": -1200.0, "avg_entry_price": 5.69},
        {"symbol": "GLD260910P00418000", "qty": 2, "market_value": 1110.0, "unrealized_pl": -260.0, "avg_entry_price": 6.85},
        {"symbol": "GLD260910P00423000", "qty": -2, "market_value": -1770.0, "unrealized_pl": 30.0, "avg_entry_price": 9.00},
    ]
    print(format_intelligent_spread_report(test_pos, "Pion Main"))
