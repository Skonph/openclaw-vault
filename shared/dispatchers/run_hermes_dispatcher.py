#!/usr/bin/env python3
"""
Hermes Real-Time Dispatcher Runner — CEO/COO Action Driver
Triggered by agent_webhook_server.py when Anna posts a real-time AI verdict to bridge.db.
"""

import sys
import subprocess
from pathlib import Path

SHARED_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SHARED_DIR))

from agent_bridge import AgentBridge
from closed_loop_executor import ClosedLoopExecutor


def process():
    bridge = AgentBridge("hermes")
    messages = bridge.receive(mark_read=True)
    if not messages:
        return

    executor = ClosedLoopExecutor()

    for msg in messages:
        msg_id = msg["id"]
        from_agent = msg["from_agent"]
        body = msg["body"]
        channel = msg.get("channel", "general")
        subject = msg.get("subject", "Intelligence Update")

        # 🛑 LOOP PREVENTION: Do not send reply back to Anna if receiving an intel verdict or execution ack!
        if channel in ("intel", "execution", "system"):
            print(f"[Hermes Dispatcher] 🛑 Received {channel} verdict #{msg_id} from '{from_agent}'. Executing closed-loop intents & stopping conversation loop.")
            pending = executor.graph_mgr.get_pending_intents()
            for node in pending:
                executor.execute_intent(node.intent_id)
            continue

        print(f"[Hermes Dispatcher] 📥 Received message #{msg_id} from '{from_agent}' [{channel}]: {subject}")


if __name__ == "__main__":
    process()
