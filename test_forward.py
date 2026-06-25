"""
Validate openclaw_forward.py parsing logic against realistic mocked API
responses matching the documented Alpha Vantage / Finnhub schemas.
This proves the parsing/feature logic without needing live network.
"""
import datetime as dt
import openclaw_forward as fwd

TODAY = dt.date(2026, 6, 25)

# --- monkeypatch the HTTP layer with documented-shape fixtures ---

def fake_json(base, params):
    fn = params.get("function")
    if "finnhub.io" in base and base.endswith("/calendar/earnings"):
        return {"earningsCalendar": [
            {"date": "2026-07-28", "epsEstimate": 0.31, "symbol": "F"},
            {"date": "2026-10-27", "epsEstimate": 0.34, "symbol": "F"},
        ]}
    if "finnhub.io" in base and base.endswith("/stock/recommendation"):
        # newest first
        return [
            {"period": "2026-06-01", "strongBuy": 4, "buy": 6, "hold": 8, "sell": 2, "strongSell": 1, "symbol": "F"},
            {"period": "2026-05-01", "strongBuy": 2, "buy": 5, "hold": 10, "sell": 3, "strongSell": 2, "symbol": "F"},
        ]
    if fn == "EARNINGS":
        return {"quarterlyEarnings": [
            {"fiscalDateEnding": "2026-03-31", "surprisePercentage": "12.5"},
            {"fiscalDateEnding": "2025-12-31", "surprisePercentage": "-3.1"},
        ]}
    if fn == "NEWS_SENTIMENT":
        return {"feed": [
            {"title": "Ford EV push", "ticker_sentiment": [
                {"ticker": "F", "ticker_sentiment_score": "0.28"}]},
            {"title": "Analyst note", "ticker_sentiment": [
                {"ticker": "F", "ticker_sentiment_score": "0.10"},
                {"ticker": "GM", "ticker_sentiment_score": "-0.05"}]},
            {"title": "Recall risk", "ticker_sentiment": [
                {"ticker": "F", "ticker_sentiment_score": "0.02"}]},
        ]}
    return None

def fake_csv(base, params):
    return [{"symbol": "F", "reportDate": "2026-07-28", "estimate": "0.31"}]

fwd._http_get_json = fake_json
fwd._http_get_csv = fake_csv

ex = fwd.ForwardExtractor(av_key="TEST", fh_key="TEST", today=TODAY)

import json
print(json.dumps(fwd.run_batch(["F"], ex), indent=2))
