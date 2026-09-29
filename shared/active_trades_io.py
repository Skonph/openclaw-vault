#!/usr/bin/env python3
"""
active_trades_io.py — Single Unified Ground-Truth Write Path for active_trades.json (RULE-092 & RULE-093)

Enforces atomic, synchronized timestamps (ICT and UTC) and author attribution on every save.
Fails loudly on write errors to eliminate corrupted or partial writes.
"""

import os
import json
import datetime
from pathlib import Path
from typing import Dict, Any, Optional

def get_active_trades_path() -> Path:
    base_dir = Path("/home/ubuntu/shared")
    if not base_dir.exists():
        base_dir = Path(__file__).parent
    return base_dir / "active_trades.json"

def load_active_trades(path: Optional[Path] = None) -> Dict[str, Any]:
    f = path or get_active_trades_path()
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception as ex:
        print(f"  🔴 Error reading active_trades.json: {ex}")
        return {}

def save_active_trades(
    data: Dict[str, Any],
    updated_by: str = "system",
    path: Optional[Path] = None,
    writer: Optional[str] = None,
    **kwargs
) -> bool:
    f = path or get_active_trades_path()
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    now_utc = now_dt.isoformat()
    now_ict = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT")

    author = writer or updated_by or kwargs.get("author", "system")
    data["timestamp"] = now_ict
    data["updated_at_ict"] = now_ict
    data["updated_at_utc"] = now_utc
    data["updated_by"] = author

    try:
        tmp_f = f.with_suffix(".tmp")
        tmp_f.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp_f.replace(f)
        return True
    except Exception as ex:
        print(f"  🔴 RULE-092 INTEGRITY ALERT: Failed to save active_trades.json ({ex})")
        return False

# Backward-compatibility aliases for legacy scripts
save = save_active_trades
load = load_active_trades
