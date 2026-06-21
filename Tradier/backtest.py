#!/usr/bin/env python3
import os
"""
Backtest module for daily_scan.py / position_monitor.py (Tradier bot).

Replays the bot's entry filters and exit rules against ~2 years of real
SPY / QQQ / IWM daily closes, using a Black-Scholes options model with a
realized-volatility (RV) proxy in place of live VIX/IV (VIX history is not
available from the data feed).

KEY METHODOLOGY NOTES / ASSUMPTIONS (documented for the report):
  - IV proxy: 20-day annualized realized volatility of log returns,
    multiplied by IV_MULTIPLIER (1.20) to approximate the typical
    implied-vol risk premium over realized vol. This proxy is used
    both as the "VIX" for regime routing (vix_proxy = RV20% * 1.20)
    and as the BS sigma for option pricing.
  - Each of SPY/QQQ/IWM is backtested with ITS OWN regime/momentum/SMA20
    signals (in production, SPY's VIX + SPY's daily change drive routing
    for all three; here each symbol routes off its own numbers). This is
    a simplification appropriate for a 3-symbol sweep and is called out
    in the report.
  - "low_vix_secondary" regime (12 <= vix_proxy < 15) is treated as a
    normal eligible regime for that symbol (defaults to the momentum-based
    bull-put/bear-call choice) rather than "try the next symbol", since
    each symbol is already being evaluated independently.
  - Bear Call Spread trend filter mirrors the documented Bull Put filter:
    BPS requires price >= SMA20 (per daily_scan.py); BCS is assumed to
    require price <= SMA20 (symmetric mirror). Iron Condor has no SMA
    filter (sideways/elevated-IV regime only).
  - Expirations: weekly Fridays 2/3/4 weeks out -> DTE candidates of
    14 / 21 / 28 calendar days (35-day candidate excluded by MAX_DTE=28,
    matching daily_scan.py's filter).
  - Iron Condor = best-scoring put credit spread + best-scoring call
    credit spread (each independently passing its own credit floor).
    max_loss = max(put_max_loss, call_max_loss)  [standard defined-risk
    IC margin treatment -- only one side can finish ITM].
    "Threatened wing" exit: if EITHER leg's cost-to-close reaches 2x
    that leg's entry credit, the whole IC is closed (a conservative
    simplification of position_monitor.py's partial-exit logic).
  - Exit-rule priority exactly matches position_monitor.py:
      Time Stop (DTE<=2) > Stop Loss (cost-to-close >= 2x entry credit)
      > Profit Target (cost-to-close <= 50% of entry credit)
      > Profit Lock (DTE<=21 and captured >= 25%)
  - Sizing: qty = 2 if vix_proxy > 20 and score > 0.30 and 2*max_loss <=
    MAX_RISK, else 1 (matches construct_bull_put_spread). MAX_POSITIONS=2
    concurrent across the combined SPY/QQQ/IWM portfolio. MAX_RISK=$100
    per single contract's max loss (matches daily_scan.py).
  - Risk-free rate r = 4.0% (flat, reasonable for the 2024-2026 window).
"""

import csv
import math
import datetime
import json
from collections import defaultdict

# ─── Constants mirrored from daily_scan.py / position_monitor.py ───────────
# 2026-06-19: scaled to the $15k primary account (2% per-trade). Env-overridable
# so the old $2k/$100 baseline can still be reproduced for comparison.
MAX_RISK         = float(os.environ.get('BT_MAX_RISK', '320'))     # was 300
MAX_POSITIONS    = int(os.environ.get('BT_MAX_POSITIONS', '5'))    # was 2
STARTING_CAPITAL = float(os.environ.get('BT_CAPITAL', '16000'))    # was 15000
MIN_DTE          = 10
MAX_DTE          = 28
TARGET_DELTA_MAX = 0.35
TARGET_DELTA_MIN = 0.10
VIX_SPY_FLOOR       = 15
VIX_SECONDARY_FLOOR = 12
WIDTHS = [1, 2, 3, 4, 5, 7, 10]
DTE_CANDIDATES = [14, 21, 28]   # weekly Fridays 2/3/4 weeks out

# Position-monitor exit constants
PROFIT_TARGET_PCT   = 0.50   # close at 50% of entry credit captured
STOP_LOSS_MULT      = 2.0    # close if cost-to-close >= 2x entry credit
PROFIT_LOCK_DTE     = 21
PROFIT_LOCK_MIN_CAPTURE = 0.25
TIME_STOP_DTE       = 2
IC_WING_STOP_MULT   = 2.0

R_FREE = 0.04
IV_MULTIPLIER = 1.20
RV_WINDOW = 20

DATA_DIR = os.environ.get('BT_DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_data"))
SYMBOLS = os.environ.get('BT_SYMBOLS', "SPY,QQQ,IWM").split(",")


# ─── Black-Scholes ───────────────────────────────────────────────────────
def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S, K, T, r, sigma, is_call):
    if T <= 0 or sigma <= 0:
        return max(0.0, (S - K) if is_call else (K - S))
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    if is_call:
        return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    else:
        return K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)


def bs_delta(S, K, T, r, sigma, is_call):
    if T <= 0 or sigma <= 0:
        if is_call:
            return 1.0 if S > K else 0.0
        else:
            return -1.0 if S < K else 0.0
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    if is_call:
        return norm_cdf(d1)
    else:
        return norm_cdf(d1) - 1.0


# ─── Data loading ────────────────────────────────────────────────────────
def load_series(symbol):
    path = f"{DATA_DIR}/{symbol}_daily.csv"
    rows = []
    with open(path) as f:
        for row in csv.DictReader(f):
            rows.append({
                "t": int(row["t"]),
                "o": float(row["o"]),
                "h": float(row["h"]),
                "l": float(row["l"]),
                "c": float(row["c"]),
                "v": float(row["v"]),
            })
    rows.sort(key=lambda x: x["t"])
    for r in rows:
        r["date"] = datetime.datetime.utcfromtimestamp(r["t"] / 1000.0).date()
    return rows


def enrich_series(rows):
    """Add sma20, daily_change_pct, rv20 (annualized), vix_proxy, dow."""
    closes = [r["c"] for r in rows]
    for i, r in enumerate(rows):
        # SMA20
        if i >= 19:
            r["sma20"] = sum(closes[i - 19:i + 1]) / 20.0
        else:
            r["sma20"] = None
        # daily change %
        if i >= 1:
            r["chg_pct"] = (closes[i] - closes[i - 1]) / closes[i - 1] * 100.0
        else:
            r["chg_pct"] = 0.0
        # RV20 (annualized, %) using log returns over trailing 20 days
        if i >= RV_WINDOW:
            log_rets = [math.log(closes[j] / closes[j - 1]) for j in range(i - RV_WINDOW + 1, i + 1)]
            mean = sum(log_rets) / len(log_rets)
            var = sum((x - mean) ** 2 for x in log_rets) / (len(log_rets) - 1)
            rv = math.sqrt(var) * math.sqrt(252) * 100.0
            r["rv20"] = rv
            r["vix_proxy"] = rv * IV_MULTIPLIER
        else:
            r["rv20"] = None
            r["vix_proxy"] = None
        r["dow"] = r["date"].weekday()  # Monday=0 ... Sunday=6
    return rows


# ─── Option chain construction at a point in time ───────────────────────
def build_chain(S, sigma, T):
    """Return dict strike -> {put_price, put_delta, call_price, call_delta}."""
    chain = {}
    lo = max(1, int(S * 0.70))
    hi = int(S * 1.30) + 1
    for K in range(lo, hi + 1):
        Kf = float(K)
        chain[K] = {
            "put_price":  bs_price(S, Kf, T, R_FREE, sigma, False),
            "put_delta":  bs_delta(S, Kf, T, R_FREE, sigma, False),
            "call_price": bs_price(S, Kf, T, R_FREE, sigma, True),
            "call_delta": bs_delta(S, Kf, T, R_FREE, sigma, True),
        }
    return chain


def best_credit_spread(chain, option_type, credit_floor_pct):
    """
    Search all (short strike x width) combos for the given option_type
    ('put' or 'call'). Returns best dict or None.
    """
    best = None
    for K, leg in chain.items():
        if option_type == "put":
            delta = leg["put_delta"]
            if not (-TARGET_DELTA_MAX <= delta <= -TARGET_DELTA_MIN):
                continue
        else:
            delta = leg["call_delta"]
            if not (TARGET_DELTA_MIN <= delta <= TARGET_DELTA_MAX):
                continue

        for width in WIDTHS:
            long_K = K - width if option_type == "put" else K + width
            if long_K not in chain:
                continue
            if option_type == "put":
                short_price = chain[K]["put_price"]
                long_price  = chain[long_K]["put_price"]
            else:
                short_price = chain[K]["call_price"]
                long_price  = chain[long_K]["call_price"]

            net_credit = round(short_price - long_price, 4)
            max_loss = round((width - net_credit) * 100, 2)
            min_credit = max(0.30, round(width * credit_floor_pct, 4))

            if net_credit < min_credit:
                continue
            if max_loss <= 0 or max_loss > MAX_RISK:
                continue

            score = net_credit / max_loss
            cand = {
                "short_strike": K,
                "long_strike":  long_K,
                "width":        width,
                "net_credit":   net_credit,
                "max_loss":     max_loss,
                "score":        score,
                "short_delta":  delta,
                "option_type":  option_type,
            }
            if best is None or score > best["score"]:
                best = cand
    return best


def construct_trade(symbol, S, sigma, strategy, credit_floor_pct):
    """
    Search across DTE_CANDIDATES for the globally best-scoring spread(s)
    for the given strategy. Returns trade dict or None.
    """
    best_overall = None

    for dte in DTE_CANDIDATES:
        T = dte / 365.0
        chain = build_chain(S, sigma, T)

        if strategy in ("bull_put_spread", "low_vix_secondary"):
            leg = best_credit_spread(chain, "put", credit_floor_pct)
            if leg is None:
                continue
            cand = {
                "strategy": "bull_put_spread", "dte": dte,
                "net_credit": leg["net_credit"], "max_loss": leg["max_loss"],
                "score": leg["score"], "legs": {"put": leg},
            }
        elif strategy == "bear_call_spread":
            leg = best_credit_spread(chain, "call", credit_floor_pct)
            if leg is None:
                continue
            cand = {
                "strategy": "bear_call_spread", "dte": dte,
                "net_credit": leg["net_credit"], "max_loss": leg["max_loss"],
                "score": leg["score"], "legs": {"call": leg},
            }
        elif strategy == "iron_condor":
            put_leg  = best_credit_spread(chain, "put", credit_floor_pct)
            call_leg = best_credit_spread(chain, "call", credit_floor_pct)
            if put_leg is None or call_leg is None:
                continue
            net_credit = round(put_leg["net_credit"] + call_leg["net_credit"], 4)
            max_loss = max(put_leg["max_loss"], call_leg["max_loss"])
            score = net_credit * 100 / max_loss if max_loss > 0 else 0
            cand = {
                "strategy": "iron_condor", "dte": dte,
                "net_credit": net_credit, "max_loss": max_loss,
                "score": score, "legs": {"put": put_leg, "call": call_leg},
            }
        else:
            continue

        if best_overall is None or cand["score"] > best_overall["score"]:
            best_overall = cand

    if best_overall is None:
        return None

    # Quantity sizing (mirrors construct_bull_put_spread)
    qty = 1
    single_loss = best_overall["max_loss"]
    if sigma_to_vix(sigma) > 20 and best_overall["score"] > 0.30 and (2 * single_loss <= MAX_RISK):
        qty = 2

    best_overall["symbol"] = symbol
    best_overall["qty"] = qty
    best_overall["entry_S"] = S
    best_overall["entry_sigma"] = sigma
    return best_overall


def sigma_to_vix(sigma):
    return sigma * 100.0


# ─── Position simulation (mark-to-market + exit rules) ─────────────────
def reprice_trade(trade, S, sigma, dte_remaining):
    """Return current cost-to-close (net credit equivalent) for the trade,
    plus per-leg costs for IC wing-stop checks."""
    T = max(dte_remaining, 0) / 365.0
    leg_costs = {}
    for side, leg in trade["legs"].items():
        if side == "put":
            short_p = bs_price(S, leg["short_strike"], T, R_FREE, sigma, False)
            long_p  = bs_price(S, leg["long_strike"],  T, R_FREE, sigma, False)
        else:
            short_p = bs_price(S, leg["short_strike"], T, R_FREE, sigma, True)
            long_p  = bs_price(S, leg["long_strike"],  T, R_FREE, sigma, True)
        leg_costs[side] = round(short_p - long_p, 4)  # current "net credit" of this leg
    total_cost = round(sum(leg_costs.values()), 4)
    return total_cost, leg_costs


def simulate_exit(trade, series, entry_idx, expiry_date):
    """
    Walk forward day by day from entry_idx+1, applying exit rules in
    priority order. Returns (exit_idx, exit_cost_to_close, exit_reason).
    """
    entry_credit = trade["net_credit"]
    entry_leg_credits = {side: leg["net_credit"] for side, leg in trade["legs"].items()}

    for i in range(entry_idx + 1, len(series)):
        day = series[i]
        dte_remaining = (expiry_date - day["date"]).days
        sigma = (day["vix_proxy"] / 100.0) if day["vix_proxy"] else trade["entry_sigma"]
        S = day["c"]

        cost_now, leg_costs = reprice_trade(trade, S, sigma, dte_remaining)
        captured = (entry_credit - cost_now) / entry_credit if entry_credit else 0

        # Priority 1: Time stop
        if dte_remaining <= TIME_STOP_DTE:
            cost_now, _ = reprice_trade(trade, S, sigma, max(dte_remaining, 0))
            return i, cost_now, "time_stop"

        # Recycle gate (14-day calendar hold limit)
        elapsed_days = (day["date"] - series[entry_idx]["date"]).days
        if elapsed_days >= 14:
            return i, cost_now, "recycle_gate"

        # Priority 2: Stop loss (whole position)
        if cost_now >= STOP_LOSS_MULT * entry_credit:
            return i, cost_now, "stop_loss"

        # Priority 2b: IC threatened-wing stop (per-leg 2x entry credit)
        if trade["strategy"] == "iron_condor":
            for side, lc in leg_costs.items():
                ec = entry_leg_credits[side]
                if ec > 0 and lc >= IC_WING_STOP_MULT * ec:
                    return i, cost_now, "ic_wing_stop"

        # Priority 3: Profit target
        if cost_now <= (1 - PROFIT_TARGET_PCT) * entry_credit:
            return i, cost_now, "profit_target"

        # Priority 4: Profit lock at 21 DTE
        if dte_remaining <= PROFIT_LOCK_DTE and captured >= PROFIT_LOCK_MIN_CAPTURE:
            return i, cost_now, "profit_lock"

        if dte_remaining <= 0:
            return i, cost_now, "expired"

    # Ran off the end of data — mark at last available bar
    last = series[-1]
    dte_remaining = (expiry_date - last["date"]).days
    sigma = (last["vix_proxy"] / 100.0) if last["vix_proxy"] else trade["entry_sigma"]
    cost_now, _ = reprice_trade(trade, last["c"], sigma, max(dte_remaining, 0))
    return len(series) - 1, cost_now, "data_end"


# ─── Entry-filter routing (mirrors morning_scan + trend filters) ────────
def determine_strategy(day):
    if day["dow"] in (0, 4):  # Monday / Friday
        return "pass", "dow_filter"
    if day["vix_proxy"] is None:
        return "pass", "no_vix_proxy"

    vix = day["vix_proxy"]
    chg = day["chg_pct"]

    if vix > 30:
        return "cash", "extreme_vol"
    if vix < VIX_SECONDARY_FLOOR:
        return "pass", "vix_too_low"
    if vix < VIX_SPY_FLOOR:
        strategy = "low_vix_secondary"
    elif abs(chg) <= 0.5 and vix >= 18:
        # Iron Condor cut permanently (2026-06-20)
        return "pass", "ic_disabled"
    elif chg > 0.5:
        strategy = "bull_put_spread"
    elif chg < -0.5:
        # Bear Call Spread cut permanently (2026-06-20)
        return "pass", "bear_disabled"
    else:
        strategy = "bull_put_spread"

    # Trend filters
    sma = day["sma20"]
    if sma is None:
        return "pass", "no_sma"
    price = day["c"]
    if strategy in ("bull_put_spread", "low_vix_secondary") and price < sma:
        return "pass", "trend_filter_bullput"
    if strategy == "bear_call_spread" and price > sma:
        return "pass", "trend_filter_bearcall"

    return strategy, "ok"


# ─── Main portfolio backtest for one credit-floor threshold ────────────
def run_backtest(series_by_symbol, credit_floor_pct):
    n = len(series_by_symbol["SPY"])
    open_positions = []  # list of dicts: symbol, trade, entry_idx, expiry_date
    closed_trades = []

    equity = STARTING_CAPITAL
    equity_curve = []

    start_idx = max(RV_WINDOW, 19) + 1  # need sma20 + rv20 ready

    for i in range(start_idx, n):
        # 1) Process exits for any open positions whose exit index == i
        still_open = []
        for pos in open_positions:
            if pos["exit_idx"] == i:
                trade = pos["trade"]
                pnl = round((trade["net_credit"] - pos["exit_cost"]) * 100 * trade["qty"], 2)
                equity += pnl
                closed_trades.append({
                    "symbol": pos["symbol"],
                    "strategy": trade["strategy"],
                    "entry_date": str(pos["entry_date"]),
                    "exit_date": str(series_by_symbol[pos["symbol"]][i]["date"]),
                    "dte_at_entry": trade["dte"],
                    "net_credit": trade["net_credit"],
                    "exit_cost": pos["exit_cost"],
                    "max_loss": trade["max_loss"],
                    "qty": trade["qty"],
                    "pnl": pnl,
                    "exit_reason": pos["exit_reason"],
                    "win": pnl > 0,
                })
                equity_curve.append((str(series_by_symbol[pos["symbol"]][i]["date"]), equity))
            else:
                still_open.append(pos)
        open_positions = still_open

        # 2) Try to open new positions if slots available
        for symbol in SYMBOLS:
            if len(open_positions) >= MAX_POSITIONS:
                break
            # Don't double-enter same symbol same day if already open
            if any(p["symbol"] == symbol for p in open_positions):
                continue

            series = series_by_symbol[symbol]
            day = series[i]
            strategy, reason = determine_strategy(day)
            if strategy in ("pass", "cash"):
                continue

            sigma = day["vix_proxy"] / 100.0
            trade = construct_trade(symbol, day["c"], sigma, strategy, credit_floor_pct)
            if trade is None:
                continue

            entry_date = day["date"]
            expiry_date = entry_date + datetime.timedelta(days=trade["dte"])
            exit_idx, exit_cost, exit_reason = simulate_exit(trade, series, i, expiry_date)

            open_positions.append({
                "symbol": symbol, "trade": trade, "entry_idx": i,
                "entry_date": entry_date, "expiry_date": expiry_date,
                "exit_idx": exit_idx, "exit_cost": exit_cost, "exit_reason": exit_reason,
            })

    # Close any remaining open positions at data end
    for pos in open_positions:
        trade = pos["trade"]
        pnl = round((trade["net_credit"] - pos["exit_cost"]) * 100 * trade["qty"], 2)
        equity += pnl
        closed_trades.append({
            "symbol": pos["symbol"],
            "strategy": trade["strategy"],
            "entry_date": str(pos["entry_date"]),
            "exit_date": str(series_by_symbol[pos["symbol"]][pos["exit_idx"]]["date"]),
            "dte_at_entry": trade["dte"],
            "net_credit": trade["net_credit"],
            "exit_cost": pos["exit_cost"],
            "max_loss": trade["max_loss"],
            "qty": trade["qty"],
            "pnl": pnl,
            "exit_reason": pos["exit_reason"],
            "win": pnl > 0,
        })
        equity_curve.append((str(series_by_symbol[pos["symbol"]][pos["exit_idx"]]["date"]), equity))

    return closed_trades, equity, equity_curve


def aggregate(closed_trades, final_equity, equity_curve, starting=STARTING_CAPITAL):
    n = len(closed_trades)
    wins = sum(1 for t in closed_trades if t["win"])
    total_pnl = sum(t["pnl"] for t in closed_trades)
    avg_credit = sum(t["net_credit"] for t in closed_trades) / n if n else 0
    avg_pnl = total_pnl / n if n else 0

    # Max drawdown on equity curve
    peak = starting
    max_dd = 0.0
    for _, eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd

    by_symbol = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0})
    by_strategy = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0})
    by_exit = defaultdict(int)
    for t in closed_trades:
        s = by_symbol[t["symbol"]]
        s["n"] += 1
        s["wins"] += 1 if t["win"] else 0
        s["pnl"] += t["pnl"]

        st = by_strategy[t["strategy"]]
        st["n"] += 1
        st["wins"] += 1 if t["win"] else 0
        st["pnl"] += t["pnl"]

        by_exit[t["exit_reason"]] += 1

    return {
        "n_trades": n,
        "wins": wins,
        "win_rate": round(wins / n * 100, 2) if n else 0,
        "total_pnl": round(total_pnl, 2),
        "avg_pnl_per_trade": round(avg_pnl, 2),
        "avg_credit": round(avg_credit, 2),
        "final_equity": round(final_equity, 2),
        "return_pct": round((final_equity - starting) / starting * 100, 2),
        "max_drawdown": round(max_dd, 2),
        "by_symbol": {k: {"n": v["n"], "win_rate": round(v["wins"]/v["n"]*100,2) if v["n"] else 0, "pnl": round(v["pnl"],2)} for k,v in by_symbol.items()},
        "by_strategy": {k: {"n": v["n"], "win_rate": round(v["wins"]/v["n"]*100,2) if v["n"] else 0, "pnl": round(v["pnl"],2)} for k,v in by_strategy.items()},
        "by_exit_reason": dict(by_exit),
    }


def main():
    series_by_symbol = {}
    for sym in SYMBOLS:
        series_by_symbol[sym] = enrich_series(load_series(sym))

    print(f"Loaded {len(series_by_symbol['SPY'])} trading days for SPY/QQQ/IWM "
          f"({series_by_symbol['SPY'][0]['date']} -> {series_by_symbol['SPY'][-1]['date']})")

    results = {}
    for floor_pct in [0.10, 0.15, 0.20, 0.25]:
        closed, final_eq, eq_curve = run_backtest(series_by_symbol, floor_pct)
        agg = aggregate(closed, final_eq, eq_curve)
        results[f"{int(floor_pct*100)}%"] = agg
        print(f"\n=== Credit floor = {floor_pct*100:.0f}% of width ===")
        print(json.dumps(agg, indent=2))

    out_path = os.environ.get('BT_OUT', os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                     "backtest_results.json"))
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)

    print("\nSaved -> backtest_results.json")


if __name__ == "__main__":
    main()
