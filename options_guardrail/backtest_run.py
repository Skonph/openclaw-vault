"""
Run a backtest and write a report.

    python3 backtest_run.py                 # default 2-symbol, 120-day GBM run
    python3 backtest_run.py --days 252 --seed 5 --symbols SPY,QQQ,IWM

Writes:
    backtest_report.md   human-readable metrics + equity curve summary
    backtest_trades.csv  every closed trade with its exit reason and P&L
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta
from pathlib import Path

from backtest import Backtester
from strategy import default_momentum_strategy

HERE = Path(__file__).parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="SPY,QQQ")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--equity", type=float, default=100_000.0)
    ap.add_argument("--iv", type=float, default=0.20)
    ap.add_argument("--real", action="store_true", help="use real historical data from Tradier")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    dates = None
    paths = None
    data_source_label = "synthetic GBM"
    data_note = "> Synthetic data. Run with `--real` to use historical closes from Tradier."

    if args.real:
        from config import Config
        from tradier_feed import TradierClient
        cfg = Config.load()
        if not cfg.tradier_token:
            print("❌ Error: --real requested but no TRADIER_TOKEN configured.")
            return

        client = TradierClient(cfg.tradier_token, cfg.tradier_base_url)
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=int(args.days * 1.5))
        start_str = start_dt.strftime("%Y-%m-%d")
        end_str = end_dt.strftime("%Y-%m-%d")

        print(f"Fetching real historical data from Tradier for {symbols} from {start_str} to {end_str}...")

        raw_paths = {}
        for s in symbols:
            bars = client.history(s, start_str, end_str)
            if not bars:
                print(f"❌ Error: No historical data returned for {s}.")
                return
            raw_paths[s] = bars

        # Align by date
        date_sets = [set(bar["date"] for bar in raw_paths[s]) for s in symbols]
        common_dates = sorted(list(set.intersection(*date_sets)))

        if not common_dates:
            print("❌ Error: No overlapping trading dates found across symbols.")
            return

        common_dates = common_dates[-args.days:]
        dates = [datetime.strptime(d, "%Y-%m-%d") for d in common_dates]
        paths = {}
        for s in symbols:
            bar_map = {bar["date"]: float(bar["close"]) for bar in raw_paths[s]}
            paths[s] = [bar_map[d] for d in common_dates]

        data_source_label = "real Tradier closes"
        data_note = f"> Real historical data sourced from Tradier ({start_str} to {end_str})."
        print(f"Loaded {len(dates)} days of real history.")
        bt = Backtester(symbols=symbols, strategy=default_momentum_strategy,
                        starting_equity=args.equity, iv=args.iv,
                        dates=dates, paths=paths)
    else:
        spot0 = {s: 100.0 + 60 * i for i, s in enumerate(symbols)}
        bt = Backtester(symbols=symbols, strategy=default_momentum_strategy,
                        spot0=spot0, days=args.days, seed=args.seed,
                        starting_equity=args.equity, iv=args.iv)

    res = bt.run()
    print(res.summary())

    # trades csv
    csv_path = HERE / "backtest_trades.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["plan_id", "symbol", "structure", "qty", "regime",
                    "opened_at", "closed_at", "close_reason", "realized_pnl_usd"])
        for t in res.trades:
            w.writerow([t.plan_id, t.symbol, t.structure, t.qty, t.regime,
                        t.opened_at, t.closed_at, t.close_reason,
                        f"{t.realized_pnl_usd:.2f}"])

    # markdown report
    pf = "inf" if res.profit_factor == float("inf") else f"{res.profit_factor:.2f}"
    md = HERE / "backtest_report.md"
    lines = [
        "# Backtest Report",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}  ",
        f"Policy: **{res.policy_name}**  |  Symbols: {', '.join(symbols)}  |  "
        f"Days: {args.days}  |  Seed: {args.seed}  |  Data: {data_source_label}",
        "",
        data_note,
        "",
        "## Headline metrics",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Trades | {res.n_trades} |",
        f"| Win rate | {res.win_rate:.1%} |",
        f"| Avg win / loss | ${res.avg_win:,.0f} / ${res.avg_loss:,.0f} |",
        f"| Expectancy / trade | ${res.expectancy:,.0f} |",
        f"| Profit factor | {pf} |",
        f"| Total return | {res.total_return:+.2%} |",
        f"| Final equity | ${res.final_equity:,.0f} (from ${res.starting_equity:,.0f}) |",
        f"| **Max drawdown** | **{res.max_drawdown:.2%}** |",
        "",
        "## Exit reasons",
        "",
        "| Reason | Count |",
        "|---|---|",
    ]
    for k, v in sorted(res.reason_counts().items()):
        lines.append(f"| {k} | {v} |")
    lines += [
        "",
        "## Equity curve (marked-to-model, every ~10th point)",
        "",
        "| Date | Marked equity |",
        "|---|---|",
    ]
    for i, (d, marked, _) in enumerate(res.equity_curve):
        if i % 10 == 0 or i == len(res.equity_curve) - 1:
            lines.append(f"| {d} | ${marked:,.0f} |")
    lines += ["", f"Full trade log: `backtest_trades.csv` ({res.n_trades} trades)."]
    md.write_text("\n".join(lines))

    print(f"\nWrote {md.name} and {csv_path.name}")


if __name__ == "__main__":
    main()
