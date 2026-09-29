#!/usr/bin/env python3
"""
Agent Bridge — Shared SQLite Message Bus for Inter-Agent Communication

Provides a lightweight, zero-dependency message queue that any agent on the
same host can import to send/receive structured messages to other agents.

Usage (from any agent):
    from agent_bridge import AgentBridge

    bridge = AgentBridge("anna")            # identify yourself
    bridge.send("hermes", "Need GEX levels for SPX before you execute")
    msgs = bridge.receive()                 # pull unread messages for you
    bridge.send("hermes", body, channel="intel", priority="high")

Database: ~/shared/bridge.db (auto-created, WAL mode for concurrent access)
"""

import json
import random
import sqlite3
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# ── Database location ────────────────────────────────────────────────────────
DB_PATH = Path(__file__).resolve().parent / "bridge.db"

# ── Schema ───────────────────────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent  TEXT    NOT NULL,
    to_agent    TEXT    NOT NULL,       -- agent name or '*' for broadcast
    channel     TEXT    DEFAULT 'general',
    priority    TEXT    DEFAULT 'normal',
    subject     TEXT,
    body        TEXT    NOT NULL,
    metadata    TEXT,                   -- JSON blob for structured payloads
    created_at  TEXT    DEFAULT (datetime('now')),
    read_at     TEXT,
    acked_at    TEXT
);

CREATE TABLE IF NOT EXISTS agents (
    name            TEXT PRIMARY KEY,
    display_name    TEXT,
    role            TEXT,
    model           TEXT,
    status          TEXT DEFAULT 'online',
    last_heartbeat  TEXT,
    capabilities    TEXT                -- JSON array
);

CREATE INDEX IF NOT EXISTS idx_msg_to       ON messages(to_agent, read_at);
CREATE INDEX IF NOT EXISTS idx_msg_channel  ON messages(channel);
CREATE INDEX IF NOT EXISTS idx_msg_created  ON messages(created_at);
"""

# ── Channel & Priority Constants ─────────────────────────────────────────────
CHANNELS = {
    "general":   "General conversation",
    "intel":     "Market intelligence & flow data (Anna → Hermes)",
    "request":   "Analysis or action requests between agents",
    "alert":     "Urgent alerts requiring immediate attention",
    "execution": "Trade execution updates (Hermes → Anna)",
    "system":    "Health checks, heartbeats, status",
}

PRIORITIES = ("critical", "high", "normal", "low")


class AgentBridge:
    """Message bus client for a single agent."""

    def __init__(self, agent_name: str, db_path: Optional[str] = None):
        self.agent_name = agent_name.lower()
        self.db_path = str(db_path or DB_PATH)
        self._init_db()
        self.heartbeat()

    # ── Connection helpers ───────────────────────────────────────────────
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    # ── Agent Registration ───────────────────────────────────────────────
    def register(self, display_name: str = "", role: str = "",
                 model: str = "", capabilities: list = None):
        """Register or update this agent's profile."""
        caps = json.dumps(capabilities or [])
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO agents (name, display_name, role, model,
                                    capabilities, last_heartbeat, status)
                VALUES (?, ?, ?, ?, ?, datetime('now'), 'online')
                ON CONFLICT(name) DO UPDATE SET
                    display_name = excluded.display_name,
                    role         = excluded.role,
                    model        = excluded.model,
                    capabilities = excluded.capabilities,
                    last_heartbeat = datetime('now'),
                    status       = 'online'
            """, (self.agent_name, display_name, role, model, caps))

    def heartbeat(self):
        """Update last-seen timestamp."""
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO agents (name, last_heartbeat, status)
                VALUES (?, datetime('now'), 'online')
                ON CONFLICT(name) DO UPDATE SET
                    last_heartbeat = datetime('now'),
                    status = 'online'
            """, (self.agent_name,))

    def list_agents(self) -> list[dict]:
        """List all registered agents."""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM agents ORDER BY name").fetchall()
            return [dict(r) for r in rows]

    # ── Send ─────────────────────────────────────────────────────────────
    def send(self, to_agent: str, body: str, *,
             channel: str = "general", priority: str = "normal",
             subject: str = None, metadata: dict = None) -> int:
        """
        Send a message to another agent (or '*' to broadcast).

        Returns the message ID.
        """
        if priority not in PRIORITIES:
            raise ValueError(f"priority must be one of {PRIORITIES}")

        meta_json = json.dumps(metadata) if metadata else None
        with self._conn() as conn:
            cur = conn.execute("""
                INSERT INTO messages
                    (from_agent, to_agent, channel, priority, subject, body, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (self.agent_name, to_agent.lower(), channel, priority,
                  subject, body, meta_json))
            msg_id = cur.lastrowid

        # Fire non-blocking webhook notification to wake up recipient agent in real time
        self._notify_webhook(to_agent.lower(), msg_id, channel, priority, subject)

        # Piggyback cleanup: ~5% chance per send, purge read msgs older than 3 days
        if random.randint(1, 20) == 1:
            try:
                self.cleanup(days=3)
            except Exception:
                pass  # never let cleanup failure break a send

        return msg_id

    def _notify_webhook(self, to_agent: str, msg_id: int, channel: str,
                        priority: str, subject: str = None):
        """
        Trigger fast synchronous HTTP webhook notification to local dispatcher (~2ms on localhost).
        Fails silently to ensure DB operations and messaging are never interrupted.
        """
        import urllib.request

        webhook_port = int(os.getenv("AGENT_WEBHOOK_PORT", "8888"))
        url = f"http://127.0.0.1:{webhook_port}/webhook/{to_agent}"
        payload_data = json.dumps({
            "msg_id": msg_id,
            "from_agent": self.agent_name,
            "to_agent": to_agent,
            "channel": channel,
            "priority": priority,
            "subject": subject
        }).encode("utf-8")

        try:
            req = urllib.request.Request(
                url,
                data=payload_data,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=1.0):
                pass
        except Exception:
            pass  # fail safe, fallback to polling

    def broadcast(self, body: str, **kwargs) -> int:
        """Send a message to all agents."""
        return self.send("*", body, **kwargs)

    # ── Receive ──────────────────────────────────────────────────────────
    def receive(self, *, channel: str = None, priority: str = None,
                mark_read: bool = True, limit: int = 50) -> list[dict]:
        """
        Fetch unread messages addressed to this agent (or broadcast).
        Optionally filter by channel/priority. Marks as read by default.
        """
        conditions = [
            "(to_agent = ? OR to_agent = '*')",
            "read_at IS NULL",
        ]
        params: list = [self.agent_name]

        if channel:
            conditions.append("channel = ?")
            params.append(channel)
        if priority:
            conditions.append("priority = ?")
            params.append(priority)

        where = " AND ".join(conditions)
        query = f"""
            SELECT * FROM messages
            WHERE {where}
            ORDER BY
                CASE priority
                    WHEN 'critical' THEN 0
                    WHEN 'high'     THEN 1
                    WHEN 'normal'   THEN 2
                    WHEN 'low'      THEN 3
                END,
                created_at ASC
            LIMIT ?
        """
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
            messages = [dict(r) for r in rows]

            if mark_read and messages:
                ids = [m["id"] for m in messages]
                placeholders = ",".join("?" * len(ids))
                conn.execute(f"""
                    UPDATE messages SET read_at = datetime('now')
                    WHERE id IN ({placeholders})
                """, ids)

            return messages

    def peek(self, **kwargs) -> list[dict]:
        """Like receive() but does NOT mark messages as read."""
        return self.receive(mark_read=False, **kwargs)

    def count_unread(self, channel: str = None) -> int:
        """Count unread messages for this agent."""
        conditions = [
            "(to_agent = ? OR to_agent = '*')",
            "read_at IS NULL",
        ]
        params: list = [self.agent_name]
        if channel:
            conditions.append("channel = ?")
            params.append(channel)

        where = " AND ".join(conditions)
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM messages WHERE {where}", params
            ).fetchone()
            return row["cnt"]

    def acknowledge(self, message_id: int):
        """Mark a message as acknowledged (processed)."""
        with self._conn() as conn:
            conn.execute("""
                UPDATE messages SET acked_at = datetime('now')
                WHERE id = ?
            """, (message_id,))

    # ── History & Search ─────────────────────────────────────────────────
    def history(self, other_agent: str = None, *, channel: str = None,
                hours: int = 24, limit: int = 100) -> list[dict]:
        """
        Get message history (read + unread) involving this agent.
        Optionally filter by conversation partner, channel, or time window.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        conditions = [
            "(from_agent = ? OR to_agent = ? OR to_agent = '*')",
            "created_at >= ?",
        ]
        params: list = [self.agent_name, self.agent_name, cutoff]

        if other_agent:
            conditions.append("(from_agent = ? OR to_agent = ?)")
            params.extend([other_agent.lower(), other_agent.lower()])
        if channel:
            conditions.append("channel = ?")
            params.append(channel)

        where = " AND ".join(conditions)
        with self._conn() as conn:
            rows = conn.execute(f"""
                SELECT * FROM messages
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT ?
            """, [*params, limit]).fetchall()
            return [dict(r) for r in rows]

    # ── Maintenance ──────────────────────────────────────────────────────
    def cleanup(self, days: int = 7):
        """Delete messages older than N days that have been read."""
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        with self._conn() as conn:
            cur = conn.execute("""
                DELETE FROM messages
                WHERE created_at < ? AND read_at IS NOT NULL
            """, (cutoff,))
            return cur.rowcount

    def stats(self) -> dict:
        """Get queue statistics."""
        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) as c FROM messages"
            ).fetchone()["c"]
            unread = conn.execute(
                "SELECT COUNT(*) as c FROM messages WHERE read_at IS NULL"
            ).fetchone()["c"]
            by_channel = {}
            for row in conn.execute("""
                SELECT channel, COUNT(*) as c FROM messages
                WHERE read_at IS NULL GROUP BY channel
            """).fetchall():
                by_channel[row["channel"]] = row["c"]

            agents = conn.execute(
                "SELECT name, status, last_heartbeat FROM agents"
            ).fetchall()

            return {
                "total_messages": total,
                "unread_messages": unread,
                "unread_by_channel": by_channel,
                "agents": [dict(a) for a in agents],
            }


# ── Convenience: one-liner send from terminal ────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 4:
        print("Usage: python3 agent_bridge.py <from> <to> <message> [channel] [priority]")
        print("  e.g. python3 agent_bridge.py anna hermes 'SPX GEX flip at 5450' intel high")
        sys.exit(1)

    sender   = sys.argv[1]
    receiver = sys.argv[2]
    message  = sys.argv[3]
    channel  = sys.argv[4] if len(sys.argv) > 4 else "general"
    priority = sys.argv[5] if len(sys.argv) > 5 else "normal"

    bridge = AgentBridge(sender)
    msg_id = bridge.send(receiver, message, channel=channel, priority=priority)
    print(f"✓ Message #{msg_id} sent: {sender} → {receiver} [{channel}/{priority}]")
