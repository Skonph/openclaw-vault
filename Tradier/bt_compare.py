#!/usr/bin/env python3
"""Print a MAX_POSITIONS comparison from /tmp/u{P}.json backtest outputs."""
import json
print("MAX_POS | trades  WR     gross    maxDD    | gross/maxDD")
for P in (2, 3, 4, 5, 6):
    try:
        r = json.load(open("/tmp/u%d.json" % P))["15%"]
    except (FileNotFoundError, KeyError):
        continue
    ratio = r["total_pnl"] / r["max_drawdown"] if r["max_drawdown"] else float("inf")
    print("   %d    | %4d   %.1f%%  $%4.0f   $%4.0f   | %.2f"
          % (P, r["n_trades"], r["win_rate"], r["total_pnl"], r["max_drawdown"], ratio))
