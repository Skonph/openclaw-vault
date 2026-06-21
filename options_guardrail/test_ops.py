"""
Tests for the ops layer (offline: mock HTTP + mock model caller).

Run:  pytest -q
"""

from datetime import date

import pytest

from state import AccountState
from positions import PositionStore
from market_data import MockMarketData
from pipeline import SessionOrchestrator
from telegram_notify import TelegramClient, NullTelegram
from strategist_run import run_strategist, build_user_content
from config import Config


# ----------------------------- Telegram -----------------------------
class FakeHttp:
    """Records POSTs; serves a scripted getUpdates response for approval."""
    def __init__(self, callback_data=None):
        self.posts = []
        self._callback_data = callback_data
        self._served = False

    def post(self, url, payload):
        self.posts.append((url, payload))
        return {"ok": True, "result": {"message_id": 1}}

    def get(self, url):
        if self._callback_data and not self._served:
            self._served = True
            return {"ok": True, "result": [
                {"update_id": 10,
                 "callback_query": {"id": "cb1", "data": self._callback_data}}]}
        return {"ok": True, "result": []}


def test_notify_sends_message():
    http = FakeHttp()
    tg = TelegramClient("tok", "123", http_post=http.post, http_get=http.get)
    tg.notify("hello")
    assert http.posts and http.posts[0][1]["text"] == "hello"


def test_notify_falls_back_to_plain_on_markdown_failure():
    # First send (markdown) returns ok:false (parse error); second (plain) succeeds.
    class FlakyHttp:
        def __init__(self):
            self.calls = []
        def post(self, url, payload):
            self.calls.append(payload)
            if payload.get("parse_mode") == "Markdown":
                return {"ok": False, "description": "can't parse entities"}
            return {"ok": True}
        def get(self, url):
            return {"ok": True, "result": []}
    http = FlakyHttp()
    tg = TelegramClient("tok", "123", http_post=http.post, http_get=http.get)
    tg.notify("has _weird_ markdown debit_call_spread")
    assert len(http.calls) == 2                       # retried
    assert "parse_mode" not in http.calls[1]          # plain-text fallback


def test_approval_true_on_approve_callback():
    http = FakeHttp(callback_data="approve:plan-1")
    tg = TelegramClient("tok", "123", http_post=http.post, http_get=http.get)
    assert tg.request_approval("approve?", "plan-1", timeout_sec=5, poll_sec=0.01) is True


def test_approval_false_on_reject_callback():
    http = FakeHttp(callback_data="reject:plan-1")
    tg = TelegramClient("tok", "123", http_post=http.post, http_get=http.get)
    assert tg.request_approval("approve?", "plan-1", timeout_sec=5, poll_sec=0.01) is False


def test_approval_times_out_to_false():
    http = FakeHttp(callback_data=None)  # no callback ever
    tg = TelegramClient("tok", "123", http_post=http.post, http_get=http.get)
    assert tg.request_approval("approve?", "plan-1", timeout_sec=0, poll_sec=0.01) is False


def test_approval_ignores_other_request_ids():
    http = FakeHttp(callback_data="approve:SOMETHING-ELSE")
    tg = TelegramClient("tok", "123", http_post=http.post, http_get=http.get)
    assert tg.request_approval("approve?", "plan-1", timeout_sec=0, poll_sec=0.01) is False


# ----------------------------- pipeline hooks -----------------------------
STRAT = """{"session_date":"2026-06-01","regime":"trend","reasoning":"x","no_trade":false,
"plans":[{"plan_id":"SPY-1","symbol":"SPY","structure":"debit_call_spread","thesis":"t",
"legs":[{"symbol":"SPY","expiry":"2026-06-19","strike":535,"right":"C","side":"BUY"},
        {"symbol":"SPY","expiry":"2026-06-19","strike":540,"right":"C","side":"SELL"}],
"net_price":2.10,"max_loss_usd":1000.0,"target_profit_usd":1500.0,"requested_qty":5,
"invalidation":{"kind":"underlying_below","value":531.0}}]}"""


def _state():
    return AccountState(equity=100_000, day_anchor_equity=100_000,
                        week_anchor_equity=100_000,
                        day_key=date.today().isoformat(), week_key="2026-W22")


def test_approver_can_block_entry():
    market = MockMarketData(prices={"SPY": 536.0}, pnls={"SPY-1": 0.0})
    notes = []
    orch = SessionOrchestrator(market, _state(), PositionStore(None), executor=None,
                               notifier=type("N", (), {"notify": lambda s, t: notes.append(t)})(),
                               approver=lambda plan, res: False)  # reject all
    env, rep = orch.open_from_strategist(STRAT)
    assert rep.opened == [] and "SPY-1" in rep.skipped


def test_approver_allows_and_notifies():
    market = MockMarketData(prices={"SPY": 536.0}, pnls={"SPY-1": 0.0})
    notes = []
    orch = SessionOrchestrator(market, _state(), PositionStore(None), executor=None,
                               notifier=type("N", (), {"notify": lambda s, t: notes.append(t)})(),
                               approver=lambda plan, res: True)
    env, rep = orch.open_from_strategist(STRAT)
    assert rep.opened == ["SPY-1"]
    assert any("OPEN SPY-1" in n for n in notes)


def test_null_telegram_is_safe():
    tg = NullTelegram()
    tg.notify("x")  # no error
    assert tg.request_approval("?", "id") is False  # fail-safe


# ----------------------------- strategist_run -----------------------------
def _cfg(tmp_path, provider="anthropic", openrouter_key=None) -> Config:
    return Config(
        strategist_provider=provider,
        anthropic_api_key=None, openrouter_api_key=openrouter_key,
        strategist_model="claude-opus-4-6",
        strategist_prompt_path=tmp_path / "p.md",
        telegram_token=None, telegram_chat_id=None, approval_timeout_sec=10,
        ibkr_host="127.0.0.1", ibkr_port=7497, ibkr_client_id=17, ibkr_paper_only=True,
        tradier_env="sandbox", tradier_token=None,
        tradier_base_url="https://sandbox.tradier.com/v1", tradier_account=None,
        finnhub_api_key=None, fred_api_key=None,
        mode="semi", equity=100_000.0, data_dir=tmp_path,
    )


def test_strategist_run_writes_json(tmp_path):
    (tmp_path / "p.md").write_text("system prompt")
    cfg = _cfg(tmp_path)
    raw = STRAT

    res = run_strategist({"session_date": "2026-06-01", "notes": "ctx"},
                         cfg=cfg, caller=lambda s, u, m: raw)
    assert res.output_path.exists()
    assert len(res.envelope.plans) == 1
    assert res.wrote_safe_default is False


def test_strategist_run_safe_default_on_bad_model_output(tmp_path):
    (tmp_path / "p.md").write_text("system prompt")
    cfg = _cfg(tmp_path)

    def bad_caller(s, u, m):
        raise RuntimeError("model down")

    res = run_strategist({"session_date": "2026-06-01"}, cfg=cfg, caller=bad_caller)
    assert res.wrote_safe_default is True
    assert res.envelope.no_trade is True
    assert res.envelope.plans == []


def test_openrouter_caller_parses_response(tmp_path, monkeypatch):
    import strategist_run as sr

    captured = {}

    class FakeResp:
        def __init__(self, payload):
            self._p = payload
        def read(self):
            import json as _j
            return _j.dumps(self._p).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        return FakeResp({"choices": [{"message": {"content": "ROUTED-JSON"}}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    caller = sr._openrouter_caller("sk-or-test")
    out = caller("sys", "user", "anthropic/claude-opus-4")
    assert out == "ROUTED-JSON"
    assert "openrouter.ai" in captured["url"]
    assert captured["auth"] == "Bearer sk-or-test"


def test_caller_from_config_selects_openrouter(tmp_path):
    from strategist_run import caller_from_config
    cfg = _cfg(tmp_path, provider="openrouter", openrouter_key="sk-or-x")
    assert callable(caller_from_config(cfg))


def test_caller_from_config_openrouter_missing_key_raises(tmp_path):
    from strategist_run import caller_from_config
    cfg = _cfg(tmp_path, provider="openrouter", openrouter_key=None)
    with pytest.raises(RuntimeError):
        caller_from_config(cfg)


def test_build_user_content_includes_sections():
    txt = build_user_content({"session_date": "2026-06-01",
                              "iv": "VIX 14", "economic_calendar": "none"})
    assert "IV" in txt and "ECONOMIC_CALENDAR" in txt and "2026-06-01" in txt
