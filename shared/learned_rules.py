"""
learned_rules.py — Manages empirical strategy rules and graduation scorecard injection
into Hermes & Anna system prompts. Survived across Sunday session resets.
"""

import json
from pathlib import Path
from typing import Dict, List, Any, Optional

RULES_FILE = Path(__file__).parent / "learned_rules.json"
SCORECARD_FILE = Path(__file__).parent / "graduation_scorecard.json"

class LearnedRulesManager:
    def __init__(self, rules_path: Path = RULES_FILE, scorecard_path: Path = SCORECARD_FILE):
        self.rules_path = rules_path
        self.scorecard_path = scorecard_path
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        if not self.rules_path.exists():
            default_rules = {
                "rules": [
                    {
                        "rule_id": "RULE-001",
                        "category": "risk_management",
                        "rule_text": "Always clear open option spreads before FOMC rate decisions to avoid binary gap risk.",
                        "confidence_score": 1.0,
                        "source": "FOMC Protocol",
                        "created_at": "2026-07-25T00:00:00Z"
                    },
                    {
                        "rule_id": "RULE-002",
                        "category": "intent_execution",
                        "rule_text": "When scheduling an action, register an Intent Node and verify position count == 0 after execution.",
                        "confidence_score": 1.0,
                        "source": "CL-RLG Protocol",
                        "created_at": "2026-07-27T00:00:00Z"
                    },
                    {
                        "rule_id": "RULE-004",
                        "category": "pion2_diversification",
                        "rule_text": "Pion2 Diversification Mandate: Pion2 must trade non-index decorrelated assets (TLT, GLD, SLV, sector ETFs, IBKR MultiSort candidates) using 10-year backtested 45 DTE Spina credit spread parameters (20 delta, 30-45 DTE). Avoid 100% correlation to SPY/QQQ beta.",
                        "confidence_score": 1.0,
                        "source": "10-Year Backtest Mandate",
                        "created_at": "2026-07-27T12:55:00Z"
                    },
                    {
                        "rule_id": "RULE-005",
                        "category": "cron_architecture",
                        "rule_text": "Zero-Token Cron Rule: All position verification crons must run as no_agent headless Python scripts (max 0.5s execution, zero LLM timeout risk). Decouple verification scripts from async LLM report generation.",
                        "confidence_score": 1.0,
                        "source": "Cron Timeout Optimization Protocol",
                        "created_at": "2026-07-27T20:52:00Z"
                    },
                    {
                        "rule_id": "RULE-006",
                        "category": "script_standards",
                        "rule_text": "Zero-Token Fallback Script Rule: Fallback close scripts must fetch Alpaca positions directly via HTTP REST without enforcing cosmetic string label length limits. Always auto-load .env keys.",
                        "confidence_score": 1.0,
                        "source": "Alpaca API Label Fix Protocol",
                        "created_at": "2026-07-27T20:58:00Z"
                    }
                ],
                "fidelity_stats": {
                    "total_intents": 0,
                    "verified_successful_intents": 0,
                    "execution_fidelity_rate": 1.0
                }
            }
            self.rules_path.write_text(json.dumps(default_rules, indent=2))

    def load_rules(self) -> List[Dict[str, Any]]:
        try:
            with open(self.rules_path, "r") as f:
                data = json.load(f)
                return data.get("rules", [])
        except Exception:
            return []

    def add_rule(self, category: str, rule_text: str, source: str = "post_mortem", confidence: float = 1.0):
        try:
            with open(self.rules_path, "r") as f:
                data = json.load(f)
        except Exception:
            data = {"rules": [], "fidelity_stats": {}}

        rule_id = f"RULE-{len(data.get('rules', [])) + 1:03d}"
        new_rule = {
            "rule_id": rule_id,
            "category": category,
            "rule_text": rule_text,
            "confidence_score": confidence,
            "source": source,
            "created_at": "2026-07-27T10:00:00Z"
        }
        data.setdefault("rules", []).append(new_rule)
        with open(self.rules_path, "w") as f:
            json.dump(data, f, indent=2)

    def load_scorecard(self) -> Optional[Dict[str, Any]]:
        if not self.scorecard_path.exists():
            return None
        try:
            with open(self.scorecard_path, "r") as f:
                return json.load(f)
        except Exception:
            return None

    def load_master_directives(self) -> List[Dict[str, Any]]:
        try:
            with open(self.rules_path, "r") as f:
                data = json.load(f)
                return data.get("master_directives", [])
        except Exception:
            return []

    def get_system_prompt_context(self) -> str:
        directives = self.load_master_directives()
        scorecard = self.load_scorecard()

        if directives:
            rules_str = "\n".join([f"• **{d.get('directive_id', 'DIR')}: {d.get('title')}**\n  {d.get('summary')}" for d in directives])
            section_title = "### INSTITUTIONAL MASTER DIRECTIVES (12 CORE PRINCIPLES)"
        else:
            rules = [r for r in self.load_rules() if r.get("status") == "ACTIVE"]
            rules_str = "\n".join([f"- [{r.get('category', 'RULE').upper()}] {r.get('title', r.get('rule_text', r.get('description', '')))}" for r in rules]) if rules else "- No custom rules registered."
            section_title = "### EMPIRICAL LEARNED RULES (DO NOT VIOLATE)"

        if scorecard:
            combined = scorecard.get("combined", {}).get("net", {})
            assess = scorecard.get("combined", {}).get("assessment", {})
            n_trades = combined.get("n", 0)
            exp = combined.get("expectancy", 0.0)
            pf = combined.get("profit_factor", 0.0)
            dd = combined.get("max_dd_pct", 0.0)
            ready = assess.get("ready", False)
            status_str = "✅ READY FOR LIVE CAPITAL" if ready else f"⏳ IN PROGRESS ({max(0, 30 - n_trades)} trades remaining)"

            scorecard_block = (
                f"### LIVE GRADUATION SCORECARD\n"
                f"- Trades Closed: **{n_trades} / 30**\n"
                f"- Net Expectancy: **${exp:.2f}**\n"
                f"- Net Profit Factor: **{pf if pf != float('inf') else '∞'}** (Target: ≥ 1.30)\n"
                f"- Max Drawdown: **{dd * 100:.1f}%** (Cap: ≤ 15.0%)\n"
                f"- Status: **{status_str}**\n"
            )
        else:
            scorecard_block = (
                f"### LIVE GRADUATION SCORECARD\n"
                f"- Trades Closed: **0 / 30** (Target: ≥ 30 trades, Net PF ≥ 1.30, Max DD ≤ 15%)\n"
                f"- Status: ⏳ IN PROGRESS (30 trades remaining)\n"
            )

        return (
            f"\n\n--- REINFORCEMENT & GRADUATION CONTEXT ---\n"
            f"{scorecard_block}\n"
            f"{section_title}\n"
            f"{rules_str}\n"
            f"-----------------------------------------\n"
        )


if __name__ == "__main__":
    mgr = LearnedRulesManager()
    print(mgr.get_system_prompt_context())
