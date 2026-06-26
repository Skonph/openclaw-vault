#!/usr/bin/env python3
"""
openclaw_forward.py
===================
Forward-looking signal extractor for OpenClaw (priority-stack item 4).

Adds the ORTHOGONAL signal that price/IV features (openclaw_features.py) can't
see: estimate revisions, analyst rating drift, earnings event risk, and news
sentiment. These lead or explain price; vol/positioning features describe the
current state. Together they give the strategist both axes.

Cost: ZERO recurring. Uses two free-tier APIs:
  - Alpha Vantage : NEWS_SENTIMENT, EARNINGS_CALENDAR, EARNINGS  (free key, 25 req/day)
  - Finnhub       : recommendation-trends, earnings calendar     (free key, 60 req/min)

Keys are read from environment variables (never hard-code, never log):
    ALPHAVANTAGE_API_KEY
    FINNHUB_API_KEY

Design (same contract as openclaw_features.py)
----------------------------------------------
Emits ONE compact signal block per ticker for the DeepSeek V4 Pro strategist.
It does NOT predict or trade. The block plugs into vault JSON alongside the
vol/positioning block, so the strategist reasons over both in one pass.

Rate-limit note
---------------
Alpha Vantage free = 25 calls/DAY. Each ticker uses up to 2 AV calls
(news + earnings cal is shared/cached). Keep watchlists <=10 names, or rotate.
Finnhub is roomier (60/min) so rating-trends run per-ticker freely.
The module caches the shared EARNINGS_CALENDAR response for the whole batch.
"""

from __future__ import annotations

import os
import json
import time
import datetime as dt
from dataclasses import dataclass, asdict, field
from typing import Optional, Any
from urllib import request, parse, error

# --------------------------------------------------------------------------- #
# Config — aligned to OpenClaw ruleset v4.0
# --------------------------------------------------------------------------- #

EARNINGS_BAN_DAYS = 14          # ruleset: no trades within +/-14 days of earnings
REVISION_LOOKBACK_DAYS = 30     # window for counting rating actions
NEWS_LOOKBACK_DAYS = 7          # sentiment aggregation window
NEWS_MAX_ARTICLES = 50
NEWS_MIN_RELEVANCE = 0.10       # drop AV mentions below this relevance score
SURPRISE_CAP_PCT = 100.0        # |EPS surprise %| above this is flagged as outlier
AV_BASE = "https://www.alphavantage.co/query"
FH_BASE = "https://finnhub.io/api/v1"
MAX_RETRIES = 3
RETRY_BACKOFF_S = 2.0
HTTP_TIMEOUT = 25


# --------------------------------------------------------------------------- #
# HTTP helper — stdlib only, no external deps for cron portability
# --------------------------------------------------------------------------- #

def _http_get_json(base: str, params: dict) -> Optional[Any]:
    url = base + "?" + parse.urlencode(params)
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            req = request.Request(url, headers={"User-Agent": "openclaw/1.0"})
            with request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8"))
            # Alpha Vantage signals rate-limit / errors inside a 200 body
            if isinstance(data, dict):
                if "Note" in data or "Information" in data:
                    raise RuntimeError(data.get("Note") or data.get("Information"))
                if data.get("Error Message"):
                    raise RuntimeError(data["Error Message"])
            return data
        except error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (429, 503) and attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_S * (2 ** attempt))
                continue
            break
        except Exception as e:  # noqa: BLE001
            last = str(e)
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_S * (2 ** attempt))
                continue
    print(f"[forward] request failed ({base}): {last}")
    return None


def _http_get_csv(base: str, params: dict) -> Optional[list[dict]]:
    """Alpha Vantage EARNINGS_CALENDAR returns CSV, not JSON."""
    import csv
    import io
    url = base + "?" + parse.urlencode(params)
    for attempt in range(MAX_RETRIES):
        try:
            req = request.Request(url, headers={"User-Agent": "openclaw/1.0"})
            with request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                text = r.read().decode("utf-8")
            if text.strip().startswith("{"):       # error envelope, not CSV
                raise RuntimeError(text[:200])
            rows = list(csv.DictReader(io.StringIO(text)))
            return rows
        except Exception as e:  # noqa: BLE001
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_S * (2 ** attempt))
                continue
            print(f"[forward] earnings-calendar CSV failed: {e}")
    return None


def _f(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Signal container
# --------------------------------------------------------------------------- #

@dataclass
class ForwardSignals:
    symbol: str
    asof: str

    # --- earnings event risk (ruleset earnings-ban enforcement) ---
    next_earnings_date: Optional[str] = None
    days_to_earnings: Optional[int] = None
    earnings_ban_active: Optional[bool] = None      # within +/-14d -> True
    last_eps_surprise_pct: Optional[float] = None    # PEAD context
    eps_beat_streak: Optional[int] = None            # consecutive beats (>0) / misses (<0)
    eps_surprise_direction: Optional[str] = None     # beat / miss / inline

    # --- analyst rating revisions (the documented drift anomaly) ---
    rec_strong_buy: Optional[int] = None
    rec_buy: Optional[int] = None
    rec_hold: Optional[int] = None
    rec_sell: Optional[int] = None
    rec_strong_sell: Optional[int] = None
    rec_net_score: Optional[float] = None            # (buy-side - sell-side)/total, -1..+1
    rec_trend_delta: Optional[float] = None          # net_score change vs prior month
    rec_trend_state: Optional[str] = None            # improving / deteriorating / stable

    # --- news sentiment (clean, structured — not forum scraping) ---
    news_count_7d: Optional[int] = None
    news_sentiment_mean: Optional[float] = None      # AV score, -1..+1 ish
    news_sentiment_state: Optional[str] = None       # bullish / neutral / bearish

    notes: list = field(default_factory=list)

    def to_signal_block(self) -> dict:
        raw = asdict(self)
        out = {}
        for k, v in raw.items():
            if v is None:
                continue
            out[k] = round(v, 4) if isinstance(v, float) else v
        return out


# --------------------------------------------------------------------------- #
# Extractor
# --------------------------------------------------------------------------- #

class ForwardExtractor:
    def __init__(self, av_key: Optional[str] = None, fh_key: Optional[str] = None,
                 today: Optional[dt.date] = None, enable_av: Optional[bool] = None):
        self.av_key = av_key or os.environ.get("ALPHAVANTAGE_API_KEY")
        self.fh_key = fh_key or os.environ.get("FINNHUB_API_KEY")
        self.today = today or dt.date.today()
        self._earnings_cal_cache: Optional[list[dict]] = None
        # AV is OFF by default — its free tier (25 req/day) is too tight for a
        # nightly multi-ticker run, and its unique contribution (news sentiment)
        # is the lowest-value forward signal. Finnhub carries earnings + ratings
        # for all tickers with no quota concern. Enable AV explicitly only when
        # under quota: ForwardExtractor(enable_av=True) or OPENCLAW_ENABLE_AV=1.
        if enable_av is None:
            enable_av = os.environ.get("OPENCLAW_ENABLE_AV", "0") == "1"
        self.enable_av = enable_av and bool(self.av_key)
        if not self.fh_key:
            print("[forward] WARNING: FINNHUB_API_KEY not set — rating features skipped")
        if self.enable_av:
            print("[forward] Alpha Vantage news sentiment ENABLED (watch 25/day quota)")

    # ---- earnings (Finnhub primary, AV fallback) ------------------------ #

    def _earnings_finnhub(self, f: ForwardSignals) -> bool:
        if not self.fh_key:
            return False
        frm = self.today.isoformat()
        to = (self.today + dt.timedelta(days=90)).isoformat()
        data = _http_get_json(f"{FH_BASE}/calendar/earnings",
                              {"symbol": f.symbol, "from": frm, "to": to,
                               "token": self.fh_key})
        if not data or not data.get("earningsCalendar"):
            return False
        upcoming = sorted(data["earningsCalendar"], key=lambda e: e.get("date", ""))
        if upcoming:
            f.next_earnings_date = upcoming[0]["date"]
            f.days_to_earnings = (
                dt.datetime.strptime(f.next_earnings_date, "%Y-%m-%d").date() - self.today
            ).days
            return True
        return False

    def _earnings_av(self, f: ForwardSignals) -> None:
        """Fallback / surprise history via Alpha Vantage. Only when AV enabled."""
        if not self.enable_av:
            return
        # surprise history (JSON)
        data = _http_get_json(AV_BASE, {"function": "EARNINGS",
                                        "symbol": f.symbol, "apikey": self.av_key})
        if data and data.get("quarterlyEarnings"):
            q = data["quarterlyEarnings"][0]
            sp = _f(q.get("surprisePercentage"))
            f.last_eps_surprise_pct = sp
            if sp is not None and abs(sp) > SURPRISE_CAP_PCT:
                f.notes.append(
                    f"last EPS surprise {sp:.0f}% is an outlier (likely one-off/charge) "
                    f"— discount as PEAD signal")
            # direction of most recent quarter
            if sp is not None:
                if sp > 2.0:
                    f.eps_surprise_direction = "beat"
                elif sp < -2.0:
                    f.eps_surprise_direction = "miss"
                else:
                    f.eps_surprise_direction = "inline"
            # consecutive beat/miss streak across recent quarters (PEAD persistence)
            streak = 0
            for qe in data["quarterlyEarnings"][:4]:
                s = _f(qe.get("surprisePercentage"))
                if s is None:
                    break
                if s > 2.0:
                    if streak >= 0:
                        streak += 1
                    else:
                        break
                elif s < -2.0:
                    if streak <= 0:
                        streak -= 1
                    else:
                        break
                else:
                    break
            f.eps_beat_streak = streak if streak != 0 else None
            if streak >= 3:
                f.notes.append(f"{streak} consecutive EPS beats — positive PEAD persistence")
            elif streak <= -2:
                f.notes.append(f"{abs(streak)} consecutive EPS misses — negative drift risk")

        # next date via shared CSV calendar (cached for the batch)
        if f.next_earnings_date is None:
            if self._earnings_cal_cache is None:
                self._earnings_cal_cache = _http_get_csv(
                    AV_BASE, {"function": "EARNINGS_CALENDAR",
                              "horizon": "3month", "apikey": self.av_key}) or []
            for row in self._earnings_cal_cache:
                if row.get("symbol") == f.symbol and row.get("reportDate"):
                    f.next_earnings_date = row["reportDate"]
                    f.days_to_earnings = (
                        dt.datetime.strptime(row["reportDate"], "%Y-%m-%d").date() - self.today
                    ).days
                    break

    def _finalize_earnings(self, f: ForwardSignals) -> None:
        if f.days_to_earnings is not None:
            f.earnings_ban_active = abs(f.days_to_earnings) <= EARNINGS_BAN_DAYS
            if f.earnings_ban_active:
                f.notes.append(
                    f"earnings in {f.days_to_earnings}d — within +/-{EARNINGS_BAN_DAYS}d ban (avoid per v4.0)")

    # ---- analyst rating revisions (Finnhub recommendation-trends) ------- #

    def rating_features(self, f: ForwardSignals) -> None:
        if not self.fh_key:
            return
        data = _http_get_json(f"{FH_BASE}/stock/recommendation",
                              {"symbol": f.symbol, "token": self.fh_key})
        if not data or not isinstance(data, list) or not data:
            return
        # API returns newest first; element 0 = current month, 1 = prior month
        cur = data[0]
        f.rec_strong_buy = cur.get("strongBuy")
        f.rec_buy = cur.get("buy")
        f.rec_hold = cur.get("hold")
        f.rec_sell = cur.get("sell")
        f.rec_strong_sell = cur.get("strongSell")

        def net(rec):
            sb, b = rec.get("strongBuy", 0), rec.get("buy", 0)
            h = rec.get("hold", 0)
            s, ss = rec.get("sell", 0), rec.get("strongSell", 0)
            total = sb + b + h + s + ss
            if total == 0:
                return None
            # weight strong calls double, hold neutral
            score = (2 * sb + b - s - 2 * ss) / (2 * total)
            return score

        f.rec_net_score = net(cur)
        # Analyst rating COUNTS are sticky month-to-month (a rating sits until
        # changed), so adjacent-month deltas are usually ~0 and miss real drift.
        # Compare current vs the OLDEST available snapshot (typically 3mo back)
        # to capture genuine consensus migration. Fall back to prior month if
        # only two snapshots exist.
        baseline_idx = len(data) - 1 if len(data) > 1 else 0
        if baseline_idx > 0 and f.rec_net_score is not None:
            base = net(data[baseline_idx])
            if base is not None:
                f.rec_trend_delta = f.rec_net_score - base
                months_span = baseline_idx
                if f.rec_trend_delta > 0.05:
                    f.rec_trend_state = "improving"
                    f.notes.append(
                        f"analyst consensus improving over {months_span}mo — revision tailwind")
                elif f.rec_trend_delta < -0.05:
                    f.rec_trend_state = "deteriorating"
                    f.notes.append(
                        f"analyst consensus deteriorating over {months_span}mo — revision headwind")
                else:
                    f.rec_trend_state = "stable"

    # ---- news sentiment (Alpha Vantage NEWS_SENTIMENT) ------------------ #

    def news_features(self, f: ForwardSignals) -> None:
        if not self.enable_av:
            return
        time_from = (self.today - dt.timedelta(days=NEWS_LOOKBACK_DAYS)).strftime("%Y%m%dT0000")
        data = _http_get_json(AV_BASE, {
            "function": "NEWS_SENTIMENT", "tickers": f.symbol,
            "time_from": time_from, "limit": NEWS_MAX_ARTICLES,
            "sort": "LATEST", "apikey": self.av_key})
        if not data or not data.get("feed"):
            return
        # AV returns multi-ticker articles; each carries a per-ticker relevance
        # and sentiment. Filter to our ticker, DROP low-relevance mentions, and
        # weight the average by relevance so a story barely about the name
        # doesn't count as much as a name-centric one.
        weighted_sum = 0.0
        weight_total = 0.0
        kept = 0
        for art in data["feed"]:
            for ts in art.get("ticker_sentiment", []):
                if ts.get("ticker") != f.symbol:
                    continue
                rel = _f(ts.get("relevance_score"))
                sc = _f(ts.get("ticker_sentiment_score"))
                if rel is None or sc is None:
                    continue
                if rel < NEWS_MIN_RELEVANCE:        # skip marginal mentions
                    continue
                weighted_sum += sc * rel
                weight_total += rel
                kept += 1
        if kept and weight_total > 0:
            f.news_count_7d = kept                  # count of RELEVANT articles
            f.news_sentiment_mean = weighted_sum / weight_total
            if f.news_sentiment_mean > 0.15:
                f.news_sentiment_state = "bullish"
            elif f.news_sentiment_mean < -0.15:
                f.news_sentiment_state = "bearish"
            else:
                f.news_sentiment_state = "neutral"
            if kept < 3:
                f.notes.append(f"thin news coverage ({kept} relevant articles) — low-confidence sentiment")

    # ---- orchestration --------------------------------------------------- #

    def extract(self, symbol: str) -> ForwardSignals:
        f = ForwardSignals(symbol=symbol, asof=self.today.isoformat())
        got_fh_earnings = self._earnings_finnhub(f)
        self._earnings_av(f)            # surprise history + fallback date
        self._finalize_earnings(f)
        self.rating_features(f)
        self.news_features(f)
        return f


def run_batch(symbols, extractor: ForwardExtractor) -> dict:
    out = {}
    for sym in symbols:
        try:
            out[sym] = extractor.extract(sym).to_signal_block()
        except Exception as e:  # noqa: BLE001
            out[sym] = {"symbol": sym, "error": str(e)}
        time.sleep(0.3)   # gentle pacing for AV's 5/min ceiling
    return out


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="OpenClaw forward-looking signals (item 4)")
    p.add_argument("symbols", nargs="*", default=["F"], help="tickers to scan")
    args = p.parse_args()
    ex = ForwardExtractor()
    print(json.dumps(run_batch(args.symbols or ["F"], ex), indent=2))
