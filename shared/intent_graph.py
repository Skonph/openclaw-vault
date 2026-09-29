"""
intent_graph.py — Shared Action & Intent Graph Ledger for Hermes & Anna.

Tracks commitments, hypotheses, target metrics, and reinforcement resolution
across daily operations, crons, and Sunday session resets.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional, Any

GRAPH_FILE_PATH = Path(__file__).parent / "intent_graph.json"

STATUS_PENDING = "PENDING"
STATUS_EXECUTING = "EXECUTING"
STATUS_VERIFIED_SUCCESS = "VERIFIED_SUCCESS"
STATUS_FAILED_NEEDS_CORRECTION = "FAILED_NEEDS_CORRECTION"
STATUS_EXPIRED = "EXPIRED"

@dataclass
class IntentNode:
    intent_id: str
    creator: str                     # "hermes" | "anna" | "system"
    goal_description: str            # e.g. "Close SPY/QQQ spreads before FOMC blackout"
    hypothesis: str                  # e.g. "Clear positions to 100% cash entering Jul 28-29 FOMC"
    action_type: str                 # "close_positions" | "run_scan" | "evaluate_iv" | "custom"
    action_payload: Dict[str, Any]   # parameters for execution
    expected_metric: Dict[str, Any]  # e.g. {"positions_count": 0, "target_cash_pct": 100}
    created_at: str                  # UTC ISO
    trigger_time: Optional[str] = None # ISO or cron expression
    cron_task_id: Optional[str] = None
    status: str = STATUS_PENDING
    actual_metric: Optional[Dict[str, Any]] = None
    resolved_at: Optional[str] = None
    reward_score: float = 0.0        # +1.0 for success, -1.0 for failure
    resolution_notes: str = ""
    graduation_gap_at_creation: Optional[Dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IntentNode":
        return cls(**data)


class IntentGraphManager:
    def __init__(self, file_path: Path = GRAPH_FILE_PATH):
        self.file_path = file_path
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        if not self.file_path.exists():
            self.file_path.write_text(json.dumps({"nodes": []}, indent=2))

    def _load_data(self) -> Dict[str, Any]:
        try:
            with open(self.file_path, "r") as f:
                return json.load(f)
        except Exception:
            return {"nodes": []}

    def _save_data(self, data: Dict[str, Any]):
        with open(self.file_path, "w") as f:
            json.dump(data, f, indent=2)

    def register_intent(
        self,
        creator: str,
        goal_description: str,
        hypothesis: str,
        action_type: str,
        action_payload: Dict[str, Any],
        expected_metric: Dict[str, Any],
        trigger_time: Optional[str] = None,
        cron_task_id: Optional[str] = None,
        graduation_gap: Optional[Dict[str, Any]] = None
    ) -> IntentNode:
        data = self._load_data()
        intent_id = f"INTENT-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        node = IntentNode(
            intent_id=intent_id,
            creator=creator,
            goal_description=goal_description,
            hypothesis=hypothesis,
            action_type=action_type,
            action_payload=action_payload,
            expected_metric=expected_metric,
            created_at=now_iso,
            trigger_time=trigger_time,
            cron_task_id=cron_task_id,
            graduation_gap_at_creation=graduation_gap or {}
        )

        data["nodes"].append(node.to_dict())
        self._save_data(data)
        return node

    def get_pending_intents(self) -> List[IntentNode]:
        data = self._load_data()
        return [
            IntentNode.from_dict(n)
            for n in data.get("nodes", [])
            if n.get("status") in (STATUS_PENDING, STATUS_EXECUTING)
        ]

    def get_intent_by_id(self, intent_id: str) -> Optional[IntentNode]:
        data = self._load_data()
        for n in data.get("nodes", []):
            if n.get("intent_id") == intent_id:
                return IntentNode.from_dict(n)
        return None

    def get_intent_by_cron_task_id(self, cron_task_id: str) -> Optional[IntentNode]:
        data = self._load_data()
        for n in data.get("nodes", []):
            if n.get("cron_task_id") == cron_task_id and n.get("status") in (STATUS_PENDING, STATUS_EXECUTING):
                return IntentNode.from_dict(n)
        return None

    def mark_executing(self, intent_id: str) -> bool:
        data = self._load_data()
        for n in data.get("nodes", []):
            if n.get("intent_id") == intent_id:
                n["status"] = STATUS_EXECUTING
                self._save_data(data)
                return True
        return False

    def resolve_intent(
        self,
        intent_id: str,
        success: bool,
        actual_metric: Dict[str, Any],
        resolution_notes: str = ""
    ) -> Optional[IntentNode]:
        data = self._load_data()
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        for n in data.get("nodes", []):
            if n.get("intent_id") == intent_id:
                n["status"] = STATUS_VERIFIED_SUCCESS if success else STATUS_FAILED_NEEDS_CORRECTION
                n["actual_metric"] = actual_metric
                n["resolved_at"] = now_iso
                n["reward_score"] = 1.0 if success else -1.0
                n["resolution_notes"] = resolution_notes
                self._save_data(data)
                return IntentNode.from_dict(n)
        return None

    def list_all_nodes(self) -> List[IntentNode]:
        data = self._load_data()
        return [IntentNode.from_dict(n) for n in data.get("nodes", [])]


if __name__ == "__main__":
    manager = IntentGraphManager()
    print(f"IntentGraphManager initialized at {manager.file_path}")
