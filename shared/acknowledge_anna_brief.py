#!/usr/bin/env python3
"""Acknowledge Anna's latest brief and update gate files."""
import json, os, sqlite3
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
SHARED = os.path.join(HOME, "shared")
db_path = os.path.join(SHARED, "bridge.db")
conn = sqlite3.connect(db_path)
cur = conn.cursor()
now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

# Mark message 34 as read
cur.execute('UPDATE messages SET read_at = ? WHERE id = 34 AND read_at IS NULL', (now,))
if cur.rowcount > 0:
    print(f"ACKNOWLEDGED Anna message #34 at {now} UTC")
else:
    print(f"Message #34 already acknowledged")

# Send ACK message back to Anna
ack_body = json.dumps({
    "type": "ack",
    "responding_to": 34,
    "subject": "Dynamic Pre-Market Brief",
    "timestamp": now,
    "status": "ok",
    "summary": "Acknowledged. Labor Day (Sep 07) confirmed — markets closed today. GLD rollover deadline (Sep 07) shifted to Tue Sep 08. XLU rollover deadline (Sep 08) noted. All positions OTM and healthy. Gate files being refreshed to match your 5/5 verdict.",
    "read_positions": ["LMT", "NVDA", "XLU", "GLD", "AVGO", "JPM"],
    "next_verification": "2026-09-08 20:30 ICT (Tue market open)"
})
cur.execute("""INSERT INTO messages (from_agent, to_agent, channel, priority, subject, body, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?)""", (
    "hermes", "anna", "intel", "normal",
    "ACK — Dynamic Pre-Market Brief (Sep 07)",
    ack_body,
    now
))
print(f"Sent ACK to Anna for message #34")

conn.commit()
conn.close()

# ===== UPDATE GATE FILES =====
# Based on Anna's 5/5 aligned verdict from message #34

gate = {
    "verdict": "go",
    "reason": "Gate refreshed by Hermes Morning Digest — Anna's Sep 07 brief shows 5/5 tumblers aligned, BULLISH_STABLE regime",
    "regime_tier": "AGGRESSIVE",
    "verdict_source": {
        "bridge_message_id": 34,
        "timestamp": "2026-09-07T12:40:00+07:00",
        "tumblers_aligned": 5,
        "verdict_text": "GO — 5/5 tumblers aligned, XLV lead setup active (markets closed Labor Day)"
    },
    "updated_at": now,
    "refreshed_by": "Hermes Morning Digest",
    "stale_after_hours": 4
}

# Write anna_gate.json
gate_path = os.path.join(SHARED, "anna_gate.json")
with open(gate_path, "w") as f:
    json.dump(gate, f, indent=2)
print(f"Updated anna_gate.json")

# Write screener_gate.json (same gate, different reason)
gate_screener = dict(gate)
gate_screener["reason"] = "Gate refreshed by Hermes Morning Digest — Anna's Sep 07 brief, 5/5 aligned"
screener_path = os.path.join(SHARED, "screener_gate.json")
with open(screener_path, "w") as f:
    json.dump(gate_screener, f, indent=2)
print(f"Updated screener_gate.json")

print("DONE")