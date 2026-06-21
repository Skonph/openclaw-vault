#!/usr/bin/env python3
"""
fetch_backtest_data.py — pull ~2y of daily OHLCV from Tradier into the CSV format
backtest.py's load_series() expects (columns: t,o,h,l,c,v ; t = ms epoch).

Runs on the server where the Tradier prod token is available. Lets you backtest a
WIDER underlying universe without piping data through a chat context.

    TRADIER_PROD_TOKEN=... python3 fetch_backtest_data.py SPY QQQ IWM XLF XLK XLE XLV XLI DIA GLD TLT
    # then:
    BT_SYMBOLS="SPY,QQQ,IWM,XLF,XLK,XLE,XLV,XLI,DIA,GLD,TLT" python3 backtest.py
"""
import os, sys, csv, datetime, requests

TOKEN = os.environ.get('TRADIER_PROD_TOKEN', '')
BASE  = "https://api.tradier.com/v1"
OUT   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_data")
HDRS  = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}

# default: 3 broad index + sector SPDRs + a few uncorrelated asset classes
SYMBOLS = sys.argv[1:] or [
    "SPY", "QQQ", "IWM",                       # broad US equity (current)
    "XLF", "XLK", "XLE", "XLV", "XLI", "XLY",  # sector SPDRs (correlated to SPY)
    "DIA",                                     # Dow
    "GLD", "TLT", "USO",                       # gold / bonds / oil — UNCORRELATED diversifiers
]


def fetch(sym, days=760):
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days)
    r = requests.get(f"{BASE}/markets/history", headers=HDRS,
                     params={"symbol": sym, "interval": "daily",
                             "start": start.isoformat(), "end": end.isoformat()},
                     timeout=20)
    r.raise_for_status()
    days_ = (r.json().get("history") or {}).get("day", [])
    if isinstance(days_, dict):
        days_ = [days_]
    return days_


def write_csv(sym, rows):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"{sym}_daily.csv")
    n = 0
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["t", "o", "h", "l", "c", "v"])
        for d in rows:
            try:
                t = int(datetime.datetime.strptime(d["date"], "%Y-%m-%d")
                        .replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
                w.writerow([t, d["open"], d["high"], d["low"], d["close"], d.get("volume", 0)])
                n += 1
            except (KeyError, ValueError):
                continue
    return path, n


def main():
    if not TOKEN:
        sys.exit("Set TRADIER_PROD_TOKEN")
    for sym in SYMBOLS:
        try:
            rows = fetch(sym)
            path, n = write_csv(sym, rows)
            print(f"  ✓ {sym:5} {n} bars -> {path}")
        except Exception as e:
            print(f"  ✗ {sym:5} {e}")
    print("\nNext: BT_SYMBOLS=\"" + ",".join(SYMBOLS) + "\" python3 backtest.py")


if __name__ == "__main__":
    main()
