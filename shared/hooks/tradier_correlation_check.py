"""
Standalone correlation check hook for Tradier.

Usage (CLI):
    python tradier_correlation_check.py --symbol AAPL --structure debit_call_spread

Function:
    check_correlation(symbol, structure) -> (blocked: bool, reason: str)
"""

from __future__ import annotations

import sys

_SHARED_PATH = "/home/ubuntu/shared"

_BULL = {"debit_call_spread", "credit_put_spread", "long_call"}
_BEAR = {"debit_put_spread", "credit_call_spread", "long_put"}
_NEUTRAL = {"iron_condor", "iron_butterfly", "calendar_spread", "diagonal_spread"}


def _structure_to_direction(structure: str) -> str:
    if structure in _BULL:
        return "bull"
    if structure in _BEAR:
        return "bear"
    if structure in _NEUTRAL:
        return "neutral"
    return "neutral"


def check_correlation(symbol: str, structure: str) -> tuple[bool, str]:
    """Return (blocked, reason).  blocked=True means the trade should be rejected."""
    if _SHARED_PATH not in sys.path:
        sys.path.insert(0, _SHARED_PATH)

    import portfolio_tracker  # noqa: PLC0415

    direction = _structure_to_direction(structure)
    blocked, reason = portfolio_tracker.correlation_blocked(symbol, direction)
    return blocked, reason


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Tradier correlation check")
    parser.add_argument("--symbol", required=True, help="Ticker symbol, e.g. AAPL")
    parser.add_argument(
        "--structure",
        required=True,
        help="Trade structure, e.g. debit_call_spread",
    )
    args = parser.parse_args()

    try:
        blocked, reason = check_correlation(args.symbol, args.structure)
        if blocked:
            print(f"BLOCKED: {reason}")
            sys.exit(1)
        else:
            print(f"OK: {reason}" if reason else "OK")
            sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
