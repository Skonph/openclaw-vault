# Shared Market Context — Consumer Integration Guide

**Updated:** 2026-06-18 · Reconciled to the existing publisher.

## The setup (important context)

A shared-context **publisher already exists on the server** and has run nightly since June 6:

- `market_context_writer.py` → writes `~/shared/market_context.json` at **20:55 ICT, Mon–Fri** (before all scanners).
- It publishes: `regime`, `vix`, `spy_change_pct`, `calendar_skip` + `next_event`, per-symbol `signals` (SMA20, momentum 5/10d, ATR14, pct_vs_sma20, trend label), full `quotes` (SPY/QQQ/IWM/VIX), a cross-system `portfolio_snapshot`, and `holiday_expirations`.

**The gap:** until 2026-06-18, *nothing read it* — the rich context was published nightly and ignored. This guide wires the consumers. (An earlier parallel publisher, `macro_publisher.py`/`run_macro.sh`, was found redundant against `market_context_writer.py` and **retired** — delete it, see Cleanup.)

## The addition: a freshness-guarded reader

`read_macro_signal.py` — `load_macro_signal()` returns the `market_context.json` dict, or **`None`** if the file is missing, malformed, or older than `max_age_minutes` (default 180). This is the safety the publisher lacks: a consumer that calls it always falls back to its own live fetch if the context is stale/missing, so the shared file is a pure optimization, never a new point of failure. Deploy a copy of `read_macro_signal.py` into each consuming project (or onto its `sys.path`); it defaults to `~/shared/market_context.json` (override with `MACRO_SIGNAL_PATH`).

## `market_context.json` schema (consumed fields)

```json
{
  "generated_at": "2026-06-17T13:55:06Z",
  "regime": "moderate",
  "vix": 16.34,
  "spy_change_pct": 0.14,
  "calendar_skip": true,
  "next_event": {"name": "FOMC", "date": "2026-06-17", "days_away": 0},
  "signals": {"SPY": {"sma20": ..., "trend": "chop", "pct_vs_sma20": ...}, "QQQ": {...}, "IWM": {...}},
  "quotes":  {"SPY": {"last": 751.35, "change": 1.02, "change_pct": 0.14}, "VIX": {"last": 16.34}, ...}
}
```

## Wiring each consumer (one at a time; each independent & reversible)

### 1. Tradier — `daily_scan.py` `morning_scan()`  ✅ DONE (2026-06-18)
`apply_shared_macro(quote_map, sig)` maps `quotes.<SYM>.last/change/change_pct` onto the regime-driving symbols (SPY/QQQ/IWM/VIX); sectors (SMH/XLE/TLT) stay from the live feed. Falls back if the context is missing/stale. Tests: `test_macro_consumer.py` (9/9). **Note:** for Tradier the benefit is *consistency + resilience* (it still needs one quote call for sectors), not a saved call.

### 2. OpenClaw — `openclaw_scanner.py` (the real dedup) — NEXT
OpenClaw fetches VIX/SPY itself before `determine_regime`. Replace that with:
```python
from read_macro_signal import load_macro_signal
sig = load_macro_signal()
if sig:
    vix_price = sig["quotes"]["VIX"]["last"]
    spy_chg   = sig["spy_change_pct"]
else:
    ...  # existing quote loop
regime = determine_regime(spy_chg, vix_price)   # OpenClaw's own thresholds, untouched
```
This lets OpenClaw **skip its own fetch entirely** when the context is fresh — a real saved call.

### 3. Guardrail — lightest touch, wire last
The guardrail has its own richer trend/econ logic and the strictest invariants. Use `market_context.json` only as a redundancy/cross-check for the VIX/quote block of `context.json`; keep `tradier_feed`/`econ_calendar` authoritative and **never** route order logic through it.

## Cleanup (retire the redundant parallel publisher)

```bash
# server
ssh ubuntu@43.156.9.185 'cd ~/shared && rm -f macro_publisher.py run_macro.sh test_macro_signal.py macro_signal.json'
# Mac vault
rm ~/AI_Prompt/Obsidient/SkonVault/shared/{macro_publisher.py,run_macro.sh,test_macro_signal.py,macro_signal.test.json}
rm -rf ~/AI_Prompt/Obsidient/SkonVault/shared/__pycache__
```
The `run_macro.sh` cron line was already removed. Keep `read_macro_signal.py` + this guide.
```
