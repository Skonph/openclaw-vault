#!/usr/bin/env python3
"""
hermes_preflight.py
-------------------
Autonomous pre-processor script that runs before Hermes is scheduled to execute.
Gathers the system service status, scans the latest pending orders, tails critical logs,
and outputs a compact context markdown file to ~/.hermes/preflight_summary.md.

This prevents Hermes from having to run multiple exploratory bash commands,
saving thousands of Sonnet input tokens on every run.
"""

import os
import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime

# Paths
HOME_DIR = Path("/home/ubuntu")
if not HOME_DIR.exists():
    HOME_DIR = Path.home()

HERMES_DIR = HOME_DIR / ".hermes"
OPENCLAW_DIR = HOME_DIR / "openclaw"
if not OPENCLAW_DIR.exists():
    OPENCLAW_DIR = Path(__file__).resolve().parent.parent / "OpenClaw"
    
TRADING_BOT_DIR = HOME_DIR / "trading-bot"
if not TRADING_BOT_DIR.exists():
    TRADING_BOT_DIR = Path(__file__).resolve().parent.parent / "trading-bot"

PENDING_ORDERS_PATH = OPENCLAW_DIR / "pending_orders.json"
MOCK_PENDING_ORDERS_PATH = Path(__file__).resolve().parent.parent / "OpenClaw/pending_orders.json"
OUTPUT_PATH = HERMES_DIR / "preflight_summary.md"


SERVICES = [
    "ibc-gateway",
    "key-server",
    "hermes-gateway",
]

def check_service_status(service_name: str) -> str:
    """Check the status of a systemd service using systemctl."""
    try:
        # Run systemctl show to get both ActiveState and UnitFileState (enabled/disabled)
        cmd = ["systemctl", "show", service_name, "--property=ActiveState,UnitFileState"]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        
        if res.returncode != 0:
            return f"Unknown (systemctl error)"
        
        properties = {}
        for line in res.stdout.strip().split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                properties[k] = v
                
        active_state = properties.get("ActiveState", "unknown")
        unit_state = properties.get("UnitFileState", "unknown")
        
        emoji = "🟢" if active_state == "active" else "🔴"
        if service_name == "ibc-gateway" and active_state != "active":
            # ibc-gateway is intentionally disabled to save RAM
            emoji = "⚪"
            
        return f"{emoji} **{active_state}** ({unit_state})"
    except Exception as e:
        return f"⚠️ Error: {e}"

def tail_file(path: Path, lines: int = 15) -> str:
    """Tail the last N lines of a file."""
    if not path.exists():
        return "*File not found.*"
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.readlines()
            tail_lines = content[-lines:]
            return "".join(tail_lines).strip()
    except Exception as e:
        return f"*Error reading file: {e}*"

def main():
    print(f"[{datetime.now().isoformat()}] Starting Hermes preflight gather...")
    
    # 1. Gather Service Statuses
    services_summary = []
    for svc in SERVICES:
        status = check_service_status(svc)
        services_summary.append(f"- **{svc}**: {status}")
    
    # 2. Gather Pending Orders
    pending_path = PENDING_ORDERS_PATH if PENDING_ORDERS_PATH.exists() else MOCK_PENDING_ORDERS_PATH
    uncertain_orders = []
    total_pending = 0
    
    if pending_path.exists():
        try:
            with open(pending_path, "r") as f:
                data = json.load(f)
                
            # data can be a list of orders or a dictionary depending on format
            orders = []
            if isinstance(data, list):
                orders = data
            elif isinstance(data, dict):
                orders = data.get("orders", []) or data.get("pending", [])
                if not orders and isinstance(data, dict):
                    # check if the dictionary itself holds orders
                    orders = [v for v in data.values() if isinstance(v, dict) and "symbol" in v]
            
            total_pending = len(orders)
            for order in orders:
                symbol = order.get("symbol", "unknown")
                order_id = order.get("order_id") or order.get("id") or "N/A"
                # Some files have events_status or status
                events_status = order.get("events_status") or order.get("status") or "unknown"
                
                if str(events_status).lower() in ["uncertain", "pending_approval", "flagged"]:
                    reason = order.get("reason") or order.get("uncertain_reason") or "No reason provided"
                    uncertain_orders.append(
                        f"  - **ID `{order_id}`**: {symbol} is **{events_status}** (Reason: *{reason}*)"
                    )
        except Exception as e:
            uncertain_orders.append(f"  - ⚠️ *Failed to parse pending orders: {e}*")
    else:
        uncertain_orders.append("  - *No pending_orders.json file found.*")

    # 3. Read log snippets
    cron_log = tail_file(TRADING_BOT_DIR / "logs/cron.log", 15)
    scanner_log = tail_file(OPENCLAW_DIR / "logs/scanner.log", 10)
    morning_report = tail_file(OPENCLAW_DIR / "logs/morning_report.log", 10)

    # 4. Generate Markdown
    md_content = f"""# Hermes Preflight Context Summary
Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} (BKK Time / UTC+7)

## 🖥️ Systemd Service Statuses
{chr(10).join(services_summary)}

> [!NOTE]
> `ibc-gateway` is intentionally stopped and disabled to prevent out-of-memory (OOM) crashes on this 2GB RAM VPS.

## 📋 OpenClaw Pending Orders & Approvals
- **Total Pending Orders:** {total_pending}
- **Uncertain/Action Items:**
{chr(10).join(uncertain_orders) if uncertain_orders else "  - *None. All current orders are clear or approved.*"}

---

## 📄 Logs Tail

### Tradier Cron Log (`~/trading-bot/logs/cron.log`)
```text
{cron_log}
```

### OpenClaw Scanner Log (`~/openclaw/logs/scanner.log`)
```text
{scanner_log}
```

### OpenClaw Morning Report (`~/openclaw/logs/morning_report.log`)
```text
{morning_report}
```
"""
    
    # Write to target file
    try:
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            f.write(md_content)
        print(f"✅ Context summary successfully written to {OUTPUT_PATH}")
    except Exception as e:
        print(f"❌ Failed to write context summary: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
