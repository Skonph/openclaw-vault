"""
Shadow report — the interim daily Telegram report for the NO-EXECUTION phase
(while IBKR options permission / Gateway aren't live yet).

It proves the whole brain end-to-end without trading:
    Tradier real-time snapshot  +  tonight's strategist plans  ->  guardrail eval
    -> a Telegram digest of what the system WOULD have done (sized to risk).

No orders, no positions touched. When IBKR goes live you switch to the real
session manager + daily_report and retire this.

    ./run.sh shadow_report.py --watchlist SPY,QQQ,IWM
"""

from __future__ import annotations

from typing import Dict, List, Optional

from state import AccountState
from guardrail import Guardrail, Decision
from risk_policy import ACTIVE_POLICY
from strategist_bridge import (
    parse_strategist_output, evaluate_envelope, StrategistEnvelope, PlanDecision,
)


def build_shadow_report(envelope: StrategistEnvelope,
                        decisions: List[PlanDecision],
                        market: Optional[Dict[str, dict]],
                        equity: float) -> str:
    lines = ["👤 *Shadow Report* (no execution — IBKR pending)",
             f"_session {envelope.session_date or '?'} • regime "
             f"{envelope.regime or '?'} • equity ${equity:,.0f}_"]

    if market:
        lines.append("")
        lines.append("*Market (Tradier):*")
        for sym, row in market.items():
            chg = row.get("change_pct")
            chg_s = f"{chg:+.2%}" if isinstance(chg, (int, float)) else "?"
            iv = row.get("atm_iv")
            iv_s = f"{iv:.1%}" if isinstance(iv, (int, float)) else "?"
            lines.append(f"  {sym}: {row.get('last')} ({chg_s})  IV {iv_s}")

    would_open = [d for d in decisions if d.result.tradeable]
    lines += ["",
              f"*Plans:* {len(envelope.plans)} parsed, "
              f"{len(would_open)} would open, "
              f"{len(envelope.plans) - len(would_open)} blocked, "
              f"{len(envelope.dropped)} dropped"]

    if envelope.no_trade and not envelope.plans:
        lines.append("_Strategist: no edge today (valid outcome)._")

    for d in decisions:
        tag = d.result.decision.value
        if d.result.tradeable:
            ml = d.result.per_unit_max_loss * d.result.approved_qty
            lines.append(f"  ✅ `{d.plan.plan_id}` {d.plan.structure} "
                         f"x{d.result.approved_qty} (max loss ${ml:,.0f}) — {tag}")
        else:
            why = d.result.reasons[0] if d.result.reasons else tag
            lines.append(f"  ⛔ `{d.plan.plan_id}` {tag}: {why}")

    for drop in envelope.dropped:
        pid = (drop.get("plan") or {}).get("plan_id", "?")
        lines.append(f"  🗑️ dropped `{pid}`: {drop['error']}")

    if envelope.reasoning:
        lines += ["", f"_Thesis: {envelope.reasoning[:400]}_"]
    return "\n".join(lines)


def main() -> None:
    import argparse
    from config import Config
    from telegram_notify import from_config as telegram_from_config
    from tradier_feed import TradierClient

    ap = argparse.ArgumentParser()
    ap.add_argument("--watchlist", default="SPY,QQQ,IWM")
    args = ap.parse_args()

    cfg = Config.load()
    symbols = [s.strip().upper() for s in args.watchlist.split(",") if s.strip()]
    state = AccountState.load(cfg.state_path, default_equity=cfg.equity)

    # strategist output from tonight (strategist_run.py writes it)
    if not cfg.strategist_output_path.exists():
        env = parse_strategist_output('{"no_trade":true,"plans":[],'
                                      '"reasoning":"no strategist output found"}')
    else:
        env = parse_strategist_output(cfg.strategist_output_path.read_text())

    decisions = evaluate_envelope(env, state, Guardrail(ACTIVE_POLICY))

    # market snapshot (best-effort)
    market: Dict[str, dict] = {}
    if cfg.tradier_token:
        try:
            client = TradierClient(cfg.tradier_token, cfg.tradier_base_url)
            q = client.quote_summary(symbols)
            for s in symbols:
                row = dict(q.get(s, {}))
                try:
                    row["atm_iv"] = client.atm_iv(s)
                except Exception:
                    row["atm_iv"] = None
                market[s] = row
        except Exception as e:
            market = {"_error": {"last": f"tradier error: {e}"}}

    report = build_shadow_report(env, decisions, market, state.equity)
    telegram_from_config(cfg).notify(report)
    print(report)


if __name__ == "__main__":
    main()
