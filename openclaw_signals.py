#!/usr/bin/env python3
"""
openclaw_signals.py
===================
Unifier for OpenClaw forecast features. Combines:
  - items 1-3 : openclaw_features.FeatureExtractor   (IBKR vol/term/skew/flow)
  - item 4    : openclaw_forward.ForwardExtractor    (earnings/ratings/news)

into ONE merged signal block per ticker, written to the vault as JSON for the
DeepSeek V4 Pro strategist to reason over in a single pass.

This is the integration layer: the strategist should never have to stitch two
files together. It gets one object per name with both axes — current vol/
positioning state AND forward-looking event/revision/sentiment — plus a
consolidated `notes` list and a small set of cross-signal flags that only make
sense when both axes are present.

Design contract
---------------
- Does NOT predict, score conviction, or trade. It assembles features.
- Emits {ticker: {merged block}} plus a run-level meta header.
- Resilient: if one extractor fails for a ticker, the other's block still lands.
- Writes atomically (temp file + rename) so vault_updater.py never reads a
  half-written file.

Wiring (cron, after openclaw_scanner.py):
    cd ~/openclaw && set -a && . ./.env.openclaw && set +a && \
        python3 openclaw_signals.py F FCX PLTR SOFI RIVN \
        --out ~/openclaw-vault/signals/forecast_signals.json
"""

from __future__ import annotations

import os
import sys
import json
import time
import tempfile
import datetime as dt
from typing import Optional

# item 4 is self-contained (stdlib HTTP). items 1-3 need injected IBKR callables.
import openclaw_forward as fwd

try:
    import openclaw_features as feat
    _HAVE_FEATURES = True
except Exception:  # noqa: BLE001
    _HAVE_FEATURES = False


# --------------------------------------------------------------------------- #
# Cross-signal interpretation — only meaningful when BOTH axes are present
# --------------------------------------------------------------------------- #

def _cross_flags(vol: dict, forward: dict) -> list[str]:
    """Observations that require both blocks. These are the payoff of merging —
    a vol fact + a forward fact that together mean more than either alone.
    Still observations, NOT trade signals; the strategist decides."""
    notes = []

    # Cheap IV + improving consensus = asymmetric setup worth a look
    if vol.get("iv_hv_ratio") and vol["iv_hv_ratio"] < 0.85 \
            and forward.get("rec_trend_state") == "improving":
        notes.append("cheap IV + improving analyst consensus — favourable asymmetry")

    # Steep put skew + deteriorating consensus = corroborated downside
    if vol.get("skew_state") == "steep_put" \
            and forward.get("rec_trend_state") == "deteriorating":
        notes.append("put skew corroborated by deteriorating consensus — downside conviction")

    # Earnings ban overrides everything — surface it loudly when vol looks tradeable
    if forward.get("earnings_ban_active") and vol.get("iv_rank_ok"):
        notes.append("vol/IV-rank look tradeable BUT earnings ban active — skip per v4.0")

    # Unusual option activity + news/earnings catalyst = explained flow
    if vol.get("opt_vol_vs_avg") and vol["opt_vol_vs_avg"] > 2.0:
        if forward.get("eps_surprise_direction") in ("beat", "miss") \
                or forward.get("news_sentiment_state") in ("bullish", "bearish"):
            notes.append("unusual option flow aligns with a fundamental catalyst — flow likely informed")
        else:
            notes.append("unusual option flow with no obvious catalyst — caution")

    # Term backwardation often = pending earnings; cross-check
    if vol.get("term_state") == "backwardation" and forward.get("days_to_earnings") is not None:
        if 0 < forward["days_to_earnings"] <= (vol.get("term_front_dte") or 999):
            notes.append("term backwardation explained by earnings inside front expiry")

    return notes


def merge_ticker(symbol: str, vol_block: Optional[dict], fwd_block: Optional[dict]) -> dict:
    """Combine the two per-ticker blocks into one. Either may be None."""
    merged: dict = {"symbol": symbol}
    notes: list[str] = []

    if vol_block and "error" not in vol_block:
        for k, v in vol_block.items():
            if k in ("symbol", "asof"):
                continue
            if k == "notes":
                notes.extend(v or [])
            else:
                merged[k] = v
    elif vol_block and "error" in vol_block:
        merged["vol_error"] = vol_block["error"]

    if fwd_block and "error" not in fwd_block:
        for k, v in fwd_block.items():
            if k in ("symbol", "asof"):
                continue
            if k == "notes":
                notes.extend(v or [])
            else:
                merged[k] = v
    elif fwd_block and "error" in fwd_block:
        merged["forward_error"] = fwd_block["error"]

    # cross-signal flags (only if both axes present)
    if vol_block and fwd_block and "error" not in vol_block and "error" not in fwd_block:
        notes.extend(_cross_flags(vol_block, fwd_block))

    if notes:
        merged["notes"] = notes
    return merged


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def build_signals(symbols, vol_extractor=None, fwd_extractor=None) -> dict:
    """Run both extractors over the watchlist and merge per ticker.

    vol_extractor may be either the IBKR FeatureExtractor or the Tradier
    TradierVolExtractor — both expose a compatible run_batch via their module.
    We dispatch on which module provides run_batch to stay backend-agnostic.
    """
    fwd_extractor = fwd_extractor or fwd.ForwardExtractor()

    vol_blocks = {}
    if vol_extractor is not None:
        # pick the run_batch matching the extractor's module
        mod = type(vol_extractor).__module__
        if mod == "openclaw_tradier_vol":
            import openclaw_tradier_vol as _tv
            vol_blocks = _tv.run_batch(symbols, vol_extractor)
        elif _HAVE_FEATURES:
            vol_blocks = feat.run_batch(symbols, vol_extractor)
    fwd_blocks = fwd.run_batch(symbols, fwd_extractor)

    out = {
        "_meta": {
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "asof_date": dt.date.today().isoformat(),
            "symbols": list(symbols),
            "has_vol_axis": vol_extractor is not None,
            "has_forward_axis": True,
        },
        "signals": {},
    }
    for sym in symbols:
        out["signals"][sym] = merge_ticker(
            sym, vol_blocks.get(sym), fwd_blocks.get(sym))
    return out


def write_atomic(path: str, data: dict) -> None:
    """Write JSON atomically so vault_updater.py never reads a partial file."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _build_vol_extractor():
    """Wire the vol/positioning backend for items 1-3.

    Uses the Tradier backend (openclaw_tradier_vol) since that's the API the
    server authenticates to. Returns an object exposing .extract(symbol) and a
    matching run_batch, or None to run forward-only.
    """
    try:
        import openclaw_tradier_vol as tv  # type: ignore
        ex = tv.build_tradier_extractor()
        if ex is None:
            return None
        return ex
    except Exception as e:  # noqa: BLE001
        print(f"[signals] Tradier vol backend unavailable ({e}) — forward-only.")
        return None


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="OpenClaw unified forecast signals")
    p.add_argument("symbols", nargs="*", default=["F"])
    p.add_argument("--out", default=None,
                   help="write merged JSON here (atomic). If omitted, prints to stdout.")
    args = p.parse_args()
    syms = args.symbols or ["F"]

    vol_ex = _build_vol_extractor()
    result = build_signals(syms, vol_extractor=vol_ex)

    if args.out:
        write_atomic(args.out, result)
        n = len(result["signals"])
        axes = "vol+forward" if result["_meta"]["has_vol_axis"] else "forward-only"
        print(f"[signals] wrote {n} tickers ({axes}) -> {args.out}")
    else:
        print(json.dumps(result, indent=2))
