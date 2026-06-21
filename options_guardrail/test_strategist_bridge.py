"""
Tests for the strategist -> guardrail bridge.

Run:  pytest -q
"""

from datetime import date

import pytest

from state import AccountState
from guardrail import Decision
from strategist_bridge import (
    parse_strategist_output, evaluate_envelope, summarize,
)


def _state(equity=100_000.0):
    return AccountState(
        equity=equity, day_anchor_equity=equity, week_anchor_equity=equity,
        day_key=date.today().isoformat(), week_key="2026-W22",
    )


GOOD = """```json
{
  "session_date": "2026-06-01",
  "regime": "trend",
  "reasoning": "ES held VWAP.",
  "no_trade": false,
  "plans": [
    {
      "plan_id": "2026-06-01-SPY-1",
      "symbol": "SPY",
      "structure": "debit_call_spread",
      "thesis": "continuation",
      "legs": [
        {"symbol":"SPY","expiry":"2026-06-19","strike":535,"right":"C","side":"BUY"},
        {"symbol":"SPY","expiry":"2026-06-19","strike":540,"right":"C","side":"SELL"}
      ],
      "net_price": 2.10,
      "max_loss_usd": 150.0,
      "target_profit_usd": 300.0,
      "requested_qty": 1,
      "invalidation": {"kind": "underlying_below", "value": 531.0}
    }
  ]
}
```"""


def test_parses_fenced_json():
    env = parse_strategist_output(GOOD)
    assert env.session_date == "2026-06-01"
    assert len(env.plans) == 1
    assert env.plans[0].symbol == "SPY"
    assert env.dropped == []


def test_parses_json_with_surrounding_prose():
    text = "Here is my plan:\n\n" + GOOD.replace("```json", "").replace("```", "") + \
           "\n\nLet me know if you want changes."
    env = parse_strategist_output(text)
    assert len(env.plans) == 1


def test_malformed_plan_is_dropped_not_fatal():
    text = """{
      "session_date":"2026-06-01","regime":"chop","reasoning":"x","no_trade":false,
      "plans":[
        {"plan_id":"good","symbol":"SPY","structure":"debit_call_spread",
         "thesis":"t",
         "legs":[{"symbol":"SPY","expiry":"2026-06-19","strike":535,"right":"C","side":"BUY"},
                 {"symbol":"SPY","expiry":"2026-06-19","strike":540,"right":"C","side":"SELL"}],
         "max_loss_usd":150.0,"requested_qty":1,
         "invalidation":{"kind":"underlying_below","value":531.0}},
        {"plan_id":"bad","symbol":"QQQ"}
      ]
    }"""
    env = parse_strategist_output(text)
    assert len(env.plans) == 1 and env.plans[0].plan_id == "good"
    assert len(env.dropped) == 1 and env.dropped[0]["plan"]["plan_id"] == "bad"


def test_no_trade_envelope():
    env = parse_strategist_output(
        '{"session_date":"2026-06-01","regime":"chop","reasoning":"no edge",'
        '"no_trade":true,"plans":[]}'
    )
    assert env.no_trade is True and env.plans == []


def test_evaluate_runs_guardrail():
    env = parse_strategist_output(GOOD)
    decisions = evaluate_envelope(env, _state())
    assert len(decisions) == 1
    assert decisions[0].result.decision in (Decision.APPROVED, Decision.APPROVED_RESIZED)
    assert decisions[0].result.tradeable


def test_evaluate_halts_on_drawdown():
    env = parse_strategist_output(GOOD)
    st = _state(equity=94_000)  # -6% day
    st.day_anchor_equity = 100_000
    decisions = evaluate_envelope(env, st)
    assert decisions[0].result.decision == Decision.HALTED


def test_forbidden_structure_rejected_via_bridge():
    text = """{"session_date":"2026-06-01","reasoning":"x","no_trade":false,"plans":[
      {"plan_id":"n","symbol":"TSLA","structure":"naked_put",
       "thesis":"wheel",
       "legs":[{"symbol":"TSLA","expiry":"2026-06-19","strike":300,"right":"P","side":"SELL"}],
       "max_loss_usd":500.0,"requested_qty":1,
       "invalidation":{"kind":"underlying_below","value":290.0}}]}"""
    env = parse_strategist_output(text)
    decisions = evaluate_envelope(env, _state())
    assert decisions[0].result.decision == Decision.REJECTED


def test_bad_text_raises():
    with pytest.raises(ValueError):
        parse_strategist_output("no json here at all")


def test_summarize_renders():
    env = parse_strategist_output(GOOD)
    s = summarize(env, evaluate_envelope(env, _state()))
    assert "SPY-1" in s
