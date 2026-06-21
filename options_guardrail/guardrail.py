"""
The guardrail engine.

This is the single chokepoint between the strategist's ideas and real (paper)
orders. It is deliberately conservative: when in doubt, REJECT. It never widens
risk, and the most it will ever do to a plan is *shrink* the size.

Decision flow (first failure stops entry):
    1. Kill-switch   — day/week drawdown breached -> reject everything.
    2. Structure     — must be allowed, must not be undefined-risk.
    3. Defined risk  — positive, finite max_loss_usd required.
    4. Invalidation  — required and well-formed.
    5. Reward:risk   — optional minimum.
    6. Per-trade cap — max_loss_usd per unit <= 2% of equity; size down if needed.
    7. Portfolio     — concurrency + total deployed-capital caps.
"""

from __future__ import annotations

import math
import json
import re
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
from typing import List

from risk_policy import ACTIVE_POLICY, RiskPolicy
from schema import TradePlan
from state import AccountState


class Decision(str, Enum):
    APPROVED = "APPROVED"            # send as-is
    APPROVED_RESIZED = "APPROVED_RESIZED"  # send, but at reduced qty
    REJECTED = "REJECTED"           # do not send
    HALTED = "HALTED"               # kill-switch active; nothing trades


@dataclass
class GuardrailResult:
    decision: Decision
    approved_qty: int
    reasons: List[str] = field(default_factory=list)
    per_unit_max_loss: float = 0.0

    @property
    def tradeable(self) -> bool:
        return self.decision in (Decision.APPROVED, Decision.APPROVED_RESIZED)


class Guardrail:
    def __init__(self, policy: RiskPolicy = ACTIVE_POLICY):
        self.policy = policy

    def evaluate(self, plan: TradePlan, state: AccountState) -> GuardrailResult:
        p = self.policy
        reasons: List[str] = []

        # ---------- 1. kill-switch (checked first; overrides everything) ----------
        day_dd = state.get_day_drawdown_pct(p.use_marked_drawdown)
        if day_dd <= -p.daily_halt_pct:
            return GuardrailResult(
                Decision.HALTED, 0,
                [f"DAILY KILL-SWITCH: day P&L {day_dd:+.2%} "
                 f"<= -{p.daily_halt_pct:.0%}. No new entries today."],
            )
        week_dd = state.get_week_drawdown_pct(p.use_marked_drawdown)
        if week_dd <= -p.weekly_halt_pct:
            return GuardrailResult(
                Decision.HALTED, 0,
                [f"WEEKLY KILL-SWITCH: week P&L {week_dd:+.2%} "
                 f"<= -{p.weekly_halt_pct:.0%}. No new entries this week."],
            )

        # ---------- 2. structure ----------
        if plan.structure in p.forbidden_structures:
            return GuardrailResult(
                Decision.REJECTED, 0,
                [f"Structure '{plan.structure}' is undefined-risk and forbidden "
                 f"in autonomous mode."],
            )
        if plan.structure not in p.allowed_structures:
            return GuardrailResult(
                Decision.REJECTED, 0,
                [f"Structure '{plan.structure}' is not in the allowed set."],
            )

        # ---------- 3. defined max loss ----------
        if p.require_defined_max_loss:
            if not math.isfinite(plan.max_loss_usd) or plan.max_loss_usd <= 0:
                return GuardrailResult(
                    Decision.REJECTED, 0,
                    [f"max_loss_usd must be a positive, finite number; "
                     f"got {plan.max_loss_usd!r}."],
                )
        if plan.requested_qty <= 0:
            return GuardrailResult(
                Decision.REJECTED, 0,
                [f"requested_qty must be >= 1; got {plan.requested_qty}."],
            )

        # ---------- 4. invalidation ----------
        if p.require_invalidation and plan.invalidation is None:
            return GuardrailResult(
                Decision.REJECTED, 0,
                ["No invalidation level. Every autonomous trade must define where "
                 "the thesis is wrong."],
            )

        # ---------- 5. reward:risk (optional) ----------
        if p.min_reward_risk_ratio > 0:
            rr = plan.reward_risk_ratio
            if rr is None:
                return GuardrailResult(
                    Decision.REJECTED, 0,
                    [f"Policy requires reward:risk >= {p.min_reward_risk_ratio:.2f} "
                     f"but target_profit_usd is missing."],
                )
            if rr < p.min_reward_risk_ratio:
                return GuardrailResult(
                    Decision.REJECTED, 0,
                    [f"Reward:risk {rr:.2f} < required {p.min_reward_risk_ratio:.2f}."],
                )

        # ---------- 5b. minimum premium threshold ----------
        if p.min_premium_threshold > 0:
            premium = abs(plan.net_price) if plan.net_price is not None else None
            if premium is not None and premium < p.min_premium_threshold:
                return GuardrailResult(
                    Decision.REJECTED, 0,
                    [f"Net premium ${premium:.2f} < minimum required ${p.min_premium_threshold:.2f} (prevent fee drag)."],
                )

        # ---------- 6. per-trade loss cap + sizing ----------
        per_unit_max_loss = plan.max_loss_usd / plan.requested_qty
        cap_usd = state.equity * p.max_loss_per_trade_pct
        if per_unit_max_loss <= 0:
            return GuardrailResult(
                Decision.REJECTED, 0, ["per-unit max loss computed as <= 0."],
            )

        # Largest qty whose total defined loss stays within the per-trade cap.
        max_qty_by_loss = int(math.floor(cap_usd / per_unit_max_loss))
        if max_qty_by_loss < 1:
            return GuardrailResult(
                Decision.REJECTED, 0,
                [f"Even 1 unit risks ${per_unit_max_loss:,.0f}, above the "
                 f"per-trade cap of ${cap_usd:,.0f} (2% of ${state.equity:,.0f})."],
                per_unit_max_loss=per_unit_max_loss,
            )

        approved_qty = min(plan.requested_qty, max_qty_by_loss)

        # Apply VIX-based contract scaling
        vix = None
        try:
            from config import Config
            cfg = Config.load()
            context_path = cfg.data_dir / "context.json"
            if context_path.exists():
                context_data = json.loads(context_path.read_text())
                iv_data = context_data.get("iv", {})
                if isinstance(iv_data, dict):
                    vix = iv_data.get("vix")
                elif isinstance(iv_data, str):
                    match = re.search(r"VIX\s+([\d\.]+)", iv_data, re.IGNORECASE)
                    if match:
                        vix = float(match.group(1))
        except Exception:
            pass

        max_allowed_qty = p.max_contracts_default
        if vix is not None and vix > p.min_vix_for_two_contracts:
            max_allowed_qty = p.max_contracts_vix_high
            reasons.append(f"VIX is {vix:.1f} (> {p.min_vix_for_two_contracts}); scaling max contracts to {max_allowed_qty}.")
        elif vix is not None:
            reasons.append(f"VIX is {vix:.1f}; limiting max contracts to {max_allowed_qty}.")
        elif max_allowed_qty < 999:
            reasons.append(f"VIX is unknown; limiting max contracts to default {max_allowed_qty}.")

        if approved_qty > max_allowed_qty:
            reasons.append(f"Resized approved qty {approved_qty} -> {max_allowed_qty} to adhere to contract scaling rules.")
            approved_qty = max_allowed_qty

        if approved_qty < plan.requested_qty and not any("adhere to contract scaling" in r or "limiting max contracts" in r for r in reasons):
            reasons.append(
                f"Resized {plan.requested_qty} -> {approved_qty} units to keep loss "
                f"<= ${cap_usd:,.0f} (per-unit risk ${per_unit_max_loss:,.0f})."
            )

        # ---------- 7. portfolio caps ----------
        if state.open_positions >= p.max_concurrent_positions:
            return GuardrailResult(
                Decision.REJECTED, 0,
                [f"At max concurrent positions ({p.max_concurrent_positions})."],
            )

        deploy_cap = state.equity * p.max_total_deployed_pct
        room = deploy_cap - state.deployed_usd
        if room <= 0:
            return GuardrailResult(
                Decision.REJECTED, 0,
                [f"Deployed-capital cap reached: ${state.deployed_usd:,.0f} of "
                 f"${deploy_cap:,.0f} ({p.max_total_deployed_pct:.0%}) in use."],
            )
        max_qty_by_deploy = int(math.floor(room / per_unit_max_loss))
        if max_qty_by_deploy < approved_qty:
            if max_qty_by_deploy < 1:
                return GuardrailResult(
                    Decision.REJECTED, 0,
                    [f"No deployed-capital room for even 1 unit "
                     f"(${room:,.0f} free)."],
                )
            reasons.append(
                f"Resized -> {max_qty_by_deploy} units to stay under the "
                f"{p.max_total_deployed_pct:.0%} deployed-capital cap."
            )
            approved_qty = max_qty_by_deploy

        decision = (Decision.APPROVED if approved_qty == plan.requested_qty
                    else Decision.APPROVED_RESIZED)
        if not reasons:
            reasons.append(
                f"OK: {approved_qty} unit(s), max loss "
                f"${per_unit_max_loss * approved_qty:,.0f} "
                f"(<= 2% cap ${cap_usd:,.0f})."
            )
        return GuardrailResult(decision, approved_qty, reasons,
                               per_unit_max_loss=per_unit_max_loss)
