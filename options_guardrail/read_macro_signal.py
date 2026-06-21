#!/usr/bin/env python3
"""
read_macro_signal.py
--------------------
Freshness-guarded reader for the shared market context.
"""

import os
import json
import datetime
from pathlib import Path
from typing import Optional, Dict, Any

MACRO_SIGNAL_PATH = Path(os.environ.get("MACRO_SIGNAL_PATH", "/home/ubuntu/shared/market_context.json"))

def load_macro_signal(max_age_minutes: int = 180) -> Optional[Dict[str, Any]]:
    """
    Read the shared market_context.json.
    Returns the parsed dict, or None if the file is missing, malformed,
    or older than max_age_minutes.
    """
    if not MACRO_SIGNAL_PATH.exists():
        return None

    try:
        # Check file age (mtime)
        mtime = datetime.datetime.fromtimestamp(MACRO_SIGNAL_PATH.stat().st_mtime, datetime.timezone.utc)
        now = datetime.datetime.now(datetime.timezone.utc)
        age = now - mtime
        if age > datetime.timedelta(minutes=max_age_minutes):
            print(f"[read_macro_signal] Context file is stale: {age.total_seconds()/60:.1f} min old (max {max_age_minutes} min)")
            return None

        # Parse JSON
        data = json.loads(MACRO_SIGNAL_PATH.read_text())
        
        # Verify schema timestamp
        gen_str = data.get("generated_at")
        if gen_str:
            gen_time = datetime.datetime.strptime(gen_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
            age_gen = now - gen_time
            if age_gen > datetime.timedelta(minutes=max_age_minutes):
                print(f"[read_macro_signal] Context generated_at is stale: {age_gen.total_seconds()/60:.1f} min old")
                return None

        return data
    except Exception as e:
        print(f"[read_macro_signal] Error reading shared context: {e}")
        return None
