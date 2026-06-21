"""
Strategist -> guardrail bridge.

Turns the Opus strategist's JSON output into validated, risk-checked decisions.
It is deliberately TOLERANT of model imperfection: it strips markdown fences,
finds the JSON object, and drops individual malformed plans instead of failing
the whole batch — but it is STRICT about risk (a dropped plan never trades).

    envelope = parse_strategist_output(raw_text)
    results  = evaluate_envelope(envelope, state, guard)
    for r in results:
        if r.result.tradeable: ...  # hand r.plan + r.result to the executor
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from schema import TradePlan, SchemaError
from guardrail import Guardrail, GuardrailResult, Decision
from state import AccountState
from risk_policy import ACTIVE_POLICY


@dataclass
class StrategistEnvelope:
    session_date: Optional[str]
    regime: Optional[str]
    reasoning: str
    no_trade: bool
    plans: List[TradePlan]
    dropped: List[Dict[str, Any]] = field(default_factory=list)  # (raw, error)
    raw: Dict[str, Any] = field(default_factory=dict)


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _extract_json_object(text: str) -> Dict[str, Any]:
    """Find the outermost JSON object in arbitrary model text."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # fall back: grab from first '{' to its matching last '}'
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in strategist output")
    return json.loads(cleaned[start:end + 1])


def parse_strategist_output(text: str) -> StrategistEnvelope:
    """Parse raw strategist text into an envelope. Malformed plans are collected
    in `.dropped` rather than raising."""
    obj = _extract_json_object(text)

    plans: List[TradePlan] = []
    dropped: List[Dict[str, Any]] = []
    for raw_plan in obj.get("plans", []) or []:
        try:
            plans.append(TradePlan.from_dict(raw_plan))
        except (SchemaError, Exception) as e:  # noqa: BLE001 - never trust model JSON
            dropped.append({"plan": raw_plan, "error": str(e)})

    return StrategistEnvelope(
        session_date=obj.get("session_date"),
        regime=obj.get("regime"),
        reasoning=str(obj.get("reasoning", "")),
        no_trade=bool(obj.get("no_trade", False)),
        plans=plans,
        dropped=dropped,
        raw=obj,
    )


@dataclass
class PlanDecision:
    plan: TradePlan
    result: GuardrailResult


def evaluate_envelope(envelope: StrategistEnvelope, state: AccountState,
                      guard: Optional[Guardrail] = None) -> List[PlanDecision]:
    """Run every parsed plan through the guardrail against current account state.
    State is NOT mutated here — the caller decides what to actually open and
    updates state on fills."""
    guard = guard or Guardrail(ACTIVE_POLICY)
    out: List[PlanDecision] = []
    for plan in envelope.plans:
        out.append(PlanDecision(plan=plan, result=guard.evaluate(plan, state)))
    return out


def summarize(envelope: StrategistEnvelope,
              decisions: List[PlanDecision]) -> str:
    lines = [
        f"Session {envelope.session_date or '?'} | regime {envelope.regime or '?'}",
        f"no_trade={envelope.no_trade} | parsed {len(envelope.plans)} plan(s), "
        f"dropped {len(envelope.dropped)}",
    ]
    for d in decisions:
        tag = d.result.decision.value
        lines.append(f"  [{tag:16}] {d.plan.plan_id:24} qty {d.result.approved_qty}")
        for r in d.result.reasons:
            lines.append(f"                     - {r}")
    for drop in envelope.dropped:
        pid = (drop["plan"] or {}).get("plan_id", "?")
        lines.append(f"  [DROPPED         ] {pid}: {drop['error']}")
    return "\n".join(lines)
