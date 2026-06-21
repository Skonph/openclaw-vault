"""
Tests for preflight + shadow_report (offline).

Run:  pytest -q
"""

from datetime import date

from state import AccountState
from guardrail import Guardrail
from risk_policy import MODERATE
from strategist_bridge import parse_strategist_output, evaluate_envelope
from preflight import (check_tradier, check_telegram, check_openrouter,
                       run_checks, summarize, Check)
from shadow_report import build_shadow_report


# ----------------------------- preflight -----------------------------
class FakeTradierOK:
    def clock(self): return {"state": "open", "description": "open"}
    def quote_summary(self, syms): return {"SPY": {"last": 535.0}}


class FakeTradierBad:
    def clock(self): raise RuntimeError("401 unauthorized")


class RecordTg:
    def __init__(self): self.msgs = []
    def notify(self, t): self.msgs.append(t)


class _Cfg:
    strategist_provider = "openrouter"
    openrouter_api_key = "sk-or-x"
    strategist_model = "anthropic/claude-haiku-4.5"


def test_check_tradier_ok():
    c = check_tradier(FakeTradierOK())
    assert c.ok and "last=535" in c.detail


def test_check_tradier_failure():
    c = check_tradier(FakeTradierBad())
    assert not c.ok and "401" in c.detail


def test_check_telegram_sends():
    tg = RecordTg()
    c = check_telegram(tg)
    assert c.ok and tg.msgs


def test_check_openrouter_key_present():
    c = check_openrouter(_Cfg(), ping=False)
    assert c.ok and "key present" in c.detail


def test_check_openrouter_missing_key():
    class C(_Cfg):
        openrouter_api_key = None
    c = check_openrouter(C(), ping=False)
    assert not c.ok


def test_check_openrouter_ping_uses_caller():
    c = check_openrouter(_Cfg(), ping=True, caller=lambda s, u, m: "OK")
    assert c.ok and "live call ok" in c.detail


def test_run_checks_and_summary_all_ok():
    checks = run_checks(_Cfg(), FakeTradierOK(), RecordTg(), ping=False)
    s = summarize(checks)
    assert "All systems go" in s


def test_summary_flags_failure():
    s = summarize([Check("X", False, "boom")])
    assert "Fix the" in s


# ----------------------------- shadow report -----------------------------
STRAT = """{"session_date":"2026-06-01","regime":"trend","reasoning":"ES held VWAP",
"no_trade":false,"plans":[
 {"plan_id":"SPY-1","symbol":"SPY","structure":"debit_call_spread","thesis":"cont",
  "legs":[{"symbol":"SPY","expiry":"2026-06-19","strike":535,"right":"C","side":"BUY"},
          {"symbol":"SPY","expiry":"2026-06-19","strike":540,"right":"C","side":"SELL"}],
  "net_price":2.1,"max_loss_usd":1000,"requested_qty":5,
  "invalidation":{"kind":"underlying_below","value":531}},
 {"plan_id":"BAD","symbol":"T","structure":"naked_put","thesis":"x",
  "legs":[{"symbol":"T","expiry":"2026-06-19","strike":20,"right":"P","side":"SELL"}],
  "max_loss_usd":500,"requested_qty":1,
  "invalidation":{"kind":"underlying_below","value":18}}]}"""


def _state():
    return AccountState(equity=100_000, day_anchor_equity=100_000,
                        week_anchor_equity=100_000, day_key=date.today().isoformat(),
                        week_key="2026-W23")


def test_shadow_report_shows_would_open_and_blocked():
    env = parse_strategist_output(STRAT)
    decisions = evaluate_envelope(env, _state(), Guardrail(MODERATE))
    market = {"SPY": {"last": 536.0, "change_pct": 0.011, "atm_iv": 0.18}}
    txt = build_shadow_report(env, decisions, market, 100_000)
    assert "would open" in txt
    assert "SPY-1" in txt and "naked_put" not in txt.split("blocked")[0]
    assert "⛔" in txt          # the naked put is blocked
    assert "IV 18" in txt or "IV 18.0%" in txt


def test_shadow_report_no_trade():
    env = parse_strategist_output('{"session_date":"2026-06-02","no_trade":true,'
                                  '"plans":[],"reasoning":"no edge"}')
    decisions = evaluate_envelope(env, _state())
    txt = build_shadow_report(env, decisions, {}, 100_000)
    assert "no edge today" in txt
