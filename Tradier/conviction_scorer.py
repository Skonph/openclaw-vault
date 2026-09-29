#!/usr/bin/env python3
"""
Stub for Tradier conviction scorer.
Routes to the unified shared/conviction_scorer.py
"""
import sys
from pathlib import Path

# Add workspace root to sys.path
sys.path.append(str(Path(__file__).parent.parent))

from shared.conviction_scorer import (
    score_conviction,
    _score_offline,
    CONVICTION_MIN
)

# Export for backwards compatibility
__all__ = ['score_conviction', '_score_offline', 'CONVICTION_MIN']
