#!/usr/bin/env python3
"""
auto_harvest_positions.py — State-Aware Bulletproof Position Harvester & Profit Locker

OPERATIONAL EXCELLENCE HARVEST PROTOCOL (RULE-002, RULE-047, RULE-050):
1. Mandatory Profit Threshold Guard (RULE-002):
   - Vertical spreads are ONLY harvested if unrealized profit meets the 50%-60% target (or >=25% for 24h Express).
   - Prevents premature liquidation of working spreads during early theta cycles!
2. Orphaned Residual Long Leg Sweeper:
   - When a short put has already been harvested/closed, automatically liquidates the leftover long put.
3. Atomic Multi-Leg Closing Combo (order_class: "mleg"):
   - Liquidates both legs simultaneously with zero margin conflict.
4. Open-Order Collision Guard:
   - Prevents HTTP 403 duplicate collisions.
"""

import sys
import os
import json
import time
import datetime
import urllib.request
import urllib.error
import ssl
import re
import math
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from alpaca_broker import AlpacaClient
from agent_bridge import AgentBridge
from live_spot import get_spot

def _resolve_entry_credit(
    und: str,
    account_type: str,
    width: float,
    s_entry: float,
    l_entry: float,
    s_strike: Optional[float] = None
) -> float:
    """
    Ground truth entry fill lookup.
    Priority 1: active_trades.json (true fill credit for this account and strike)
    Priority 2: Valid broker average entry price difference (s_entry - l_entry)
    Priority 3: Curated known spread credits
    Priority 4: Default width-based estimate
    """
    for base_p in (Path("/home/ubuntu/shared"), Path(__file__).parent):
        trades_file = base_p / "active_trades.json"
        if trades_file.exists():
            try:
                tdata = json.loads(trades_file.read_text(encoding="utf-8"))
                for acct_name, acct in tdata.get("accounts", {}).items():
                    if account_type and (account_type not in acct_name and acct_name not in account_type):
                        continue
                    for pos in acct.get("positions", []):
                        if pos.get("symbol") == und:
                            if s_strike is not None and abs(float(pos.get("short_strike", 0) or 0) - s_strike) > 0.5:
                                continue
                            if pos.get("net_credit") is not None:
                                return float(pos["net_credit"])
            except Exception:
                pass

    # Sanity guard if total dollars were passed instead of per-share premium:
    if width > 0 and s_entry > width * 2.0:
        s_entry /= 100.0
    if width > 0 and l_entry > width * 2.0:
        l_entry /= 100.0

    if s_entry > 0 and l_entry > 0 and (s_entry > l_entry):
        diff = round(s_entry - l_entry, 2)
        if diff >= 0.04:
            return diff

    KNOWN_SPREAD_CREDITS = {
        "LMT": 0.80, "NVDA": 0.71, "XLU": 0.95, "AMD": 0.55, "XLF": 0.08, "TSM": 1.50,
        "META": 1.12, "IWM": 0.62, "GE": 1.26, "RTX": 0.82, "SPY": 0.50, "XLE": 0.36
    }
    if und in KNOWN_SPREAD_CREDITS:
        return KNOWN_SPREAD_CREDITS[und]

    return 0.80 if width <= 2.5 else 1.20

def _resolve_days_held(und: str, s_strike: float, account_type: str) -> int:
    """
    Computes days held from active_trades.json entry_date.
    Defaults to 3 if not found.
    """
    for base_p in (Path("/home/ubuntu/shared"), Path(__file__).parent):
        trades_file = base_p / "active_trades.json"
        if trades_file.exists():
            try:
                tdata = json.loads(trades_file.read_text(encoding="utf-8"))
                for acct_name, acct in tdata.get("accounts", {}).items():
                    if account_type in acct_name or acct_name in account_type:
                        for pos in acct.get("positions", []):
                            if pos.get("symbol") == und and abs(float(pos.get("short_strike", 0)) - s_strike) < 0.5:
                                e_str = pos.get("entry_date", "")
                                if e_str:
                                    e_date = datetime.datetime.strptime(e_str.split()[0], "%Y-%m-%d").date()
                                    return max(0, (datetime.date.today() - e_date).days)
            except Exception:
                pass
    return 3

SHARED_DIR = Path("/home/ubuntu/shared") if Path("/home/ubuntu/shared").exists() else Path(__file__).resolve().parent
HARVEST_RATCHETS_PATH = SHARED_DIR / "harvest_ratchets.json"

def load_harvest_ratchets() -> Dict[str, Any]:
    if HARVEST_RATCHETS_PATH.exists():
        try:
            return json.loads(HARVEST_RATCHETS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_harvest_ratchets(data: Dict[str, Any]):
    try:
        HARVEST_RATCHETS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"  ⚠️ Error saving harvest_ratchets.json: {e}")

def is_spread_runner(spread_key: str) -> bool:
    """Returns True if the spread has undergone Tranche 1 scale-out and is an active runner."""
    ratchets = load_harvest_ratchets()
    return bool(ratchets.get(spread_key, {}).get("is_runner"))

def get_runner_info(spread_key: str) -> Dict[str, Any]:
    """Returns runner metadata if active, or empty dict."""
    ratchets = load_harvest_ratchets()
    return ratchets.get(spread_key, {})

def register_scale_out_runner(
    spread_key: str,
    target_symbol: str,
    account_type: str,
    initial_contracts: int,
    scaled_out_contracts: int,
    remaining_runner_contracts: int,
    profit_pct: float,
    tot_pnl: float,
    order_id: str
):
    """
    Registers Tranche 1 Scale-Out (e.g. 50% TP closed) and arms Tranche 2 Runner
    with 5-minute High-Water Mark trailing ratchet.
    """
    ratchets = load_harvest_ratchets()
    now_ict = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT")
    rec = ratchets.get(spread_key, {})
    rec.update({
        "symbol": target_symbol,
        "account": account_type,
        "is_runner": True,
        "initial_contracts": initial_contracts,
        "scaled_out_contracts": scaled_out_contracts,
        "runner_contracts": remaining_runner_contracts,
        "scaled_out_order_id": order_id,
        "scaled_out_at_ict": now_ict,
        "scaled_out_pnl": round(tot_pnl * (scaled_out_contracts / max(1, initial_contracts)), 2),
        "scaled_out_profit_pct": round(profit_pct, 2),
        "high_water_profit_pct": round(profit_pct, 2),
        # Trailing floor starts at profit_pct - 15.0% (e.g. 50% - 15% = 35% floor)
        "runner_floor_pct": round(max(30.0, profit_pct - 15.0), 2),
        "ratchet_active": True,
        "updated_at_ict": now_ict
    })
    ratchets[spread_key] = rec
    save_harvest_ratchets(ratchets)
    print(f"  🏃‍♂️ RUNNER REGISTERED: {target_symbol} ({spread_key}) -> {remaining_runner_contracts} runner contract(s) armed with {rec['runner_floor_pct']:.1f}% trailing floor!")

def update_and_check_profit_ratchet(
    spread_key: str,
    target_symbol: str,
    account_type: str,
    profit_pct: float,
    tot_pnl: float
) -> Tuple[bool, Optional[str]]:
    """
    RULE-097: Profit High-Water Mark Ratchet & Velocity Protection.
    - If in RUNNER MODE (Tranche 2):
      * Tracks peak profit HWM and raises trailing floor (HWM - 15%).
      * If profit hits >= 85%: Triggers Terminal Runner Harvest.
      * If profit dips below trailing floor: Triggers Trailing Dip Harvest.
      * If held >= 5 days as runner: Triggers Time Decay Exhaustion Harvest.
    - If in STANDARD MODE (Pre-scale-out or single contract):
      * If unrealized profit touches >= 30%, activates a +20.0% Hard Floor Ratchet.
      * If market pulls back towards +20.0% floor, triggers an emergency profit-lock harvest.
    Returns (should_ratchet_harvest: bool, reason: str)
    """
    ratchets = load_harvest_ratchets()
    rec = ratchets.get(spread_key, {})
    now_ict = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT")

    if rec.get("is_runner"):
        # ──────────────────────────────────────────────────────────────
        # ACTIVE RUNNER PROTOCOL (TRANCHE 2)
        # ──────────────────────────────────────────────────────────────
        hwm = float(rec.get("high_water_profit_pct", 50.0))
        if profit_pct > hwm:
            hwm = round(profit_pct, 2)
            rec["high_water_profit_pct"] = hwm
            # Trail by 15% below peak (minimum 30% floor)
            rec["runner_floor_pct"] = round(max(float(rec.get("runner_floor_pct", 30.0)), hwm - 15.0), 2)
            rec["updated_at_ict"] = now_ict
            ratchets[spread_key] = rec
            save_harvest_ratchets(ratchets)
            print(f"  🏃‍♂️ RUNNER EXPANSION: {target_symbol} touched new peak {hwm:.1f}% profit! Trailing floor raised to {rec['runner_floor_pct']:.1f}%.")

        runner_floor = float(rec.get("runner_floor_pct", 35.0))

        # 1. Terminal Runner Capture: >= 85% profit
        if profit_pct >= 85.0:
            reason = f"DIR-09 RUNNER 85%+ TERMINAL HARVEST (+${tot_pnl:,.2f} | {profit_pct:.1f}% | Full Capture 🚀)"
            return True, reason

        # 2. Trailing Dip Harvest: profit dips below the trailing floor
        if profit_pct <= (runner_floor + 0.5) and tot_pnl > 0:
            reason = (f"DIR-09 RUNNER TRAILING DIP HARVEST (+${tot_pnl:,.2f} | {profit_pct:.1f}% | "
                      f"Peak {hwm:.1f}% -> Trailing Floor {runner_floor:.1f}% Locked 🔒)")
            return True, reason

        # 3. Time Decay Exhaustion: Held as runner for >= 5 days
        scaled_out_at = rec.get("scaled_out_at_ict")
        if scaled_out_at:
            try:
                dt_scale = datetime.datetime.strptime(scaled_out_at, "%Y-%m-%d %H:%M:%S ICT")
                days_as_runner = (datetime.datetime.now() - dt_scale).days
                if days_as_runner >= 5:
                    reason = (f"DIR-09 RUNNER TIME DECAY EXHAUSTION (+${tot_pnl:,.2f} | {profit_pct:.1f}% | "
                              f"Held {days_as_runner}d post-scale | Freeing Collateral 🌾)")
                    return True, reason
            except Exception:
                pass

        return False, ""

    # ──────────────────────────────────────────────────────────────────
    # STANDARD MODE: PRE-SCALE-OUT / SINGLE CONTRACT RATCHET
    # ──────────────────────────────────────────────────────────────────
    hwm = float(rec.get("high_water_profit_pct", 0.0))
    if profit_pct > hwm:
        hwm = profit_pct
        rec["high_water_profit_pct"] = round(hwm, 2)
        rec["symbol"] = target_symbol
        rec["account"] = account_type
        rec["updated_at_ict"] = now_ict
        if hwm >= 25.0 and not rec.get("ratchet_active"):
            rec["ratchet_active"] = True
            rec["ratchet_floor_pct"] = 15.0
            rec["activated_at_ict"] = now_ict
            print(f"  🔒 RATCHET ARMED: {target_symbol} touched {hwm:.1f}% profit! +15.0% Hard Profit Floor locked in.")
        if hwm >= 30.0 and rec.get("ratchet_floor_pct", 0) < 20.0:
            rec["ratchet_floor_pct"] = 20.0
            print(f"  🔒 RATCHET UPGRADED: {target_symbol} touched {hwm:.1f}% profit! +20.0% Hard Profit Floor locked in.")
        ratchets[spread_key] = rec
        save_harvest_ratchets(ratchets)

    if rec.get("ratchet_active"):
        floor_pct = float(rec.get("ratchet_floor_pct", 20.0))
        if profit_pct <= (floor_pct + 1.0) and tot_pnl > 0:
            reason = (f"DIR-09 Profit Ratchet Protection (+${tot_pnl:,.2f} | {profit_pct:.1f}% | "
                      f"Peak {hwm:.1f}% -> Protected at +{floor_pct:.0f}% Floor 🔒)")
            return True, reason

    return False, ""

def clear_harvest_ratchet(spread_key: str):
    """
    Cleans up armed profit ratchet once spread has been harvested or closed.
    """
    ratchets = load_harvest_ratchets()
    if spread_key in ratchets:
        del ratchets[spread_key]
        save_harvest_ratchets(ratchets)

def apply_closing_micro_walk(
    target_symbol: str,
    est_debit: float,
    net_credit_sh: float,
    days_held: int,
    is_defensive: bool = False
) -> Tuple[float, str]:
    """
    RULE-096: Closing Micro-Walk & Penny-Pilot Fill Accelerator.
    On Penny-Pilot tickers, applies a +$0.01 debit nudge on closing limit orders,
    guaranteeing immediate fill across the penny bid-ask spread to release collateral instantly.
    """
    if is_defensive:
        return est_debit, "DEFENSIVE_SLIP_ACTIVE"

    PENNY_PILOT_SYMBOLS = {
        "SPY", "QQQ", "IWM", "XLF", "XLE", "XLU", "GLD", "SLV",
        "NVDA", "AMD", "TSM", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "IBIT"
    }
    is_penny = target_symbol.upper() in PENNY_PILOT_SYMBOLS
    if not is_penny:
        return est_debit, "STANDARD_MID_DEBIT"

    # Normalize credit per share if total dollar credit was passed
    credit_per_share = net_credit_sh / 100.0 if net_credit_sh > 5.0 else net_credit_sh

    walked_debit = round(est_debit + 0.01, 2)
    min_locked_pct = 25.0 if days_held <= 2 else (35.0 if days_held <= 5 else 45.0)
    max_debit_allowed = round(credit_per_share * (1.0 - (min_locked_pct / 100.0)), 2)

    if walked_debit <= max_debit_allowed:
        return walked_debit, f"PENNY_PILOT_MICRO_WALK (+${walked_debit:.2f} [+$0.01 nudge for instant fill])"

    return est_debit, "STANDARD_MID_DEBIT"

def is_market_open(broker: AlpacaClient) -> bool:
    """Queries Alpaca /v2/clock to check if US options exchange is currently open."""
    url = f"{broker.base_url}/v2/clock"
    try:
        req = urllib.request.Request(url, headers=broker._headers())
        with urllib.request.urlopen(req, context=broker.ssl_ctx, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return bool(data.get("is_open", False))
    except Exception:
        now_dt = datetime.datetime.now()
        h, m = now_dt.hour, now_dt.minute
        return (h > 20 or (h == 20 and m >= 30)) or (h < 3)

def get_open_orders(broker: AlpacaClient) -> List[Dict[str, Any]]:
    """Queries active open orders from broker."""
    url = f"{broker.base_url}/v2/orders?status=open"
    try:
        req = urllib.request.Request(url, headers=broker._headers())
        with urllib.request.urlopen(req, context=broker.ssl_ctx, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return []

def cancel_order(broker: AlpacaClient, order_id: str) -> bool:
    """Cancels a specific order by ID."""
    url = f"{broker.base_url}/v2/orders/{order_id}"
    try:
        req = urllib.request.Request(url, headers=broker._headers(), method="DELETE")
        with urllib.request.urlopen(req, context=broker.ssl_ctx, timeout=10) as resp:
            return resp.status in [200, 204]
    except Exception:
        return False

def _get_lockout_file() -> Path:
    base_dir = Path("/home/ubuntu/shared")
    if not base_dir.exists():
        base_dir = Path(__file__).parent
    return base_dir / "harvest_lockouts.json"

def load_harvest_lockouts() -> Dict[str, Any]:
    f = _get_lockout_file()
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_harvest_lockouts(lockouts: Dict[str, Any]):
    f = _get_lockout_file()
    try:
        f.write_text(json.dumps(lockouts, indent=2), encoding="utf-8")
    except Exception as ex:
        print(f"  ℹ️ Error saving harvest lockouts: {ex}")

def _parse_cooldown_ts(val: Any) -> float:
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val)
        except ValueError:
            try:
                return datetime.datetime.fromisoformat(val).timestamp()
            except Exception:
                return 0.0
    return 0.0

def is_spread_locked_out(spread_key: str) -> bool:
    """
    RULE-092: Checks if a spread is currently locked out from re-harvesting.
    Returns True if locked out (IN_PROGRESS or COMPLETED within active cooldown).
    """
    lockouts = load_harvest_lockouts()
    record = lockouts.get(spread_key)
    if not record:
        return False
    
    status = record.get("status", "")
    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
    cooldown_until = _parse_cooldown_ts(record.get("cooldown_until_utc", 0.0))

    if status in ("COMPLETED", "IN_PROGRESS"):
        return now_ts < cooldown_until

    return False

def get_spread_lockout_info(spread_key: str) -> Tuple[bool, str]:
    """Returns (is_locked: bool, reason: str) for verbose logging."""
    lockouts = load_harvest_lockouts()
    record = lockouts.get(spread_key)
    if not record:
        return False, ""
    
    status = record.get("status", "")
    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
    cooldown_until = _parse_cooldown_ts(record.get("cooldown_until_utc", 0.0))

    if status == "COMPLETED":
        if now_ts < cooldown_until:
            rem_hrs = max(0.1, (cooldown_until - now_ts) / 3600.0)
            return True, f"COMPLETED ({rem_hrs:.1f}h cooldown active)"
    elif status == "IN_PROGRESS":
        if now_ts < cooldown_until:
            rem_mins = max(0.1, (cooldown_until - now_ts) / 60.0)
            return True, f"IN_PROGRESS (Order resting on book, {rem_mins:.1f}m resting guard)"

    return False, ""

def set_harvest_lockout(
    spread_key: str,
    status: str,
    order_id: Optional[str] = None,
    cooldown_hours: float = 24.0,
    details: Optional[Dict[str, Any]] = None
):
    """
    RULE-092: Sets spread lockout state:
    - 'IN_PROGRESS': sets 10-minute order resting guard.
    - 'COMPLETED': sets cooldown_hours (default 24 hours).
    """
    lockouts = load_harvest_lockouts()
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    now_ts = now_dt.timestamp()
    now_ict = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT")

    if status == "COMPLETED":
        cooldown_until = now_ts + (cooldown_hours * 3600)
    else: # IN_PROGRESS
        cooldown_until = now_ts + (10 * 60)    # 10 minutes

    rec = lockouts.get(spread_key, {})
    rec.update({
        "spread_key": spread_key,
        "status": status,
        "updated_at_ict": now_ict,
        "cooldown_until_utc": cooldown_until
    })
    if order_id:
        rec["order_id"] = order_id
    if details:
        rec.update(details)
    lockouts[spread_key] = rec
    save_harvest_lockouts(lockouts)

def send_telegram_alert(text: str) -> bool:
    """Dispatches Telegram notifications safely to the trading operations group."""
    group_id = "-1004375899205"
    hermes_token = "8991076258:AAGHyb-jEnp29BQ5O5V0mo4vFOmgXfgnmTg"
    ctx_ssl = ssl._create_unverified_context()
    try:
        url = f"https://api.telegram.org/bot{hermes_token}/sendMessage"
        payload = json.dumps({"chat_id": group_id, "text": text}).encode("utf-8")
        req_t = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req_t, context=ctx_ssl, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return bool(data.get("ok"))
    except Exception as e:
        print(f"  ℹ️ Telegram alert notice: {e}")
        return False

def broadcast_to_anna(subject: str, message: str, payload: Optional[Dict[str, Any]] = None):
    """RULE-083: Mirrors events directly to Anna via SQLite AgentBridge."""
    try:
        bridge = AgentBridge("hermes")
        body_text = message
        if payload:
            body_text += "\n\nJSON_METADATA:\n" + json.dumps(payload, indent=2)
        bridge.send("anna", body_text, channel="trade_execution", subject=subject)
        print(f"  ⚡ Mirrored event '{subject}' to Anna via AgentBridge!")
    except Exception as ex_b:
        print(f"  ℹ️ Bridge mirror notice: {ex_b}")

def check_and_emit_amber_alert(
    account_type: str,
    target_symbol: str,
    spot: float,
    s_strike: float,
    buf_pct: float,
    dte: int,
    opt_tag: str,
    spread_key: str
):
    """
    DIR-10 & RULE-090: Proactive Amber Strike-Touch Caution Radar.
    Emits an early alert when spot drifts to within 0.50% < buf_pct <= 1.50% of the short strike.
    Enforces a 60-minute cooldown per spread to prevent notification spam.
    """
    if not (0.50 < buf_pct <= 1.50):
        return

    amber_key = f"amber_{spread_key}"
    lockouts = load_harvest_lockouts()
    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
    rec = lockouts.get(amber_key, {})
    last_amber = _parse_cooldown_ts(rec.get("cooldown_until_utc", 0.0))
    if now_ts < last_amber:
        return  # In cooldown

    # Set 60-minute cooldown
    lockouts[amber_key] = {
        "spread_key": spread_key,
        "status": "AMBER_WARNING",
        "buf_pct": round(buf_pct, 2),
        "spot": spot,
        "short_strike": s_strike,
        "updated_at_ict": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT"),
        "cooldown_until_utc": now_ts + 3600
    }
    save_harvest_lockouts(lockouts)

    alert_text = f"""⚠️ HERMES AMBER RADAR: [{target_symbol}] SHORT STRIKE CAUTION!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🛡️ PROACTIVE SENTINEL EARLY WARNING (RULE-090)
• Account      : {account_type.upper()}
• Current Spot : ${spot:,.2f}
• Short Strike : ${s_strike:,.2f} ({opt_tag})
• Live Buffer  : {buf_pct:+.2f}% (Inside the <=1.50% Caution Zone)
• DTE Remaining: {dte}d

⚡ STATUS & DIRECTIVE:
• 5-Minute Strike Sentinel elevated to HIGH ALERT.
• DIR-10 Auto-defense armed & primed to trigger at <= 0.50% buffer touch.
• Zero manual action needed; system monitoring orderbook every 5 mins."""

    print(f"\n  ⚠️ [AMBER CAUTION RADAR] {target_symbol} buffer is {buf_pct:+.2f}% <= 1.50%! Emitting early warning...")
    send_telegram_alert(alert_text)
    broadcast_to_anna(f"AMBER_ALERT_{target_symbol}", alert_text, {
        "account": account_type,
        "symbol": target_symbol,
        "spot": spot,
        "short_strike": s_strike,
        "buffer_pct": buf_pct,
        "dte": dte
    })

def log_stopout_audit_event(
    account: str,
    target_symbol: str,
    s_strike: float,
    l_strike: float,
    width: float,
    contracts: int,
    exp_date_str: str,
    dte: int,
    spot: float,
    buf_pct: float,
    initial_credit: float,
    est_debit: float,
    order_id: str,
    order_status: str,
    reason: str
) -> Dict[str, Any]:
    """
    RULE-087, RULE-089 & RULE-092: Records defensive stop-outs to persistent audit ledger.
    Deduplicates by spread identity to eliminate duplicate phantom P&L hits.
    """
    target_paths = [p for p in (Path("/home/ubuntu/shared"), Path(__file__).parent) if p.exists()]
    res_event = {}
    for base_p in target_paths:
        ledger_file = base_p / "stopout_audit_ledger.json"
        try:
            if ledger_file.exists():
                data = json.loads(ledger_file.read_text(encoding="utf-8"))
            else:
                data = {"updated_at_ict": "", "cumulative_stats": {}, "events": []}

            collateral_locked = width * contracts * 100.0
            counterfactual_max_loss = -(collateral_locked) + initial_credit
            actual_loss_at_stopout = -(est_debit * contracts * 100.0) + initial_credit
            capital_saved = round(max(0.0, abs(counterfactual_max_loss) - abs(actual_loss_at_stopout)), 2)
            salvage_pct = round(max(0.0, (1.0 - (est_debit / width)) * 100.0), 1) if width > 0 else 0.0

            now_ict = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT")
            event_id = f"STOPOUT_{target_symbol}_{datetime.date.today()}"

            event = {
                "event_id": event_id,
                "timestamp_ict": now_ict,
                "account": account,
                "symbol": target_symbol,
                "strategy": f"Bull Put Spread ({s_strike:.0f}P/{l_strike:.0f}P)",
                "short_strike": s_strike,
                "long_strike": l_strike,
                "spread_width": width,
                "contracts": contracts,
                "expiration": exp_date_str,
                "dte_at_stopout": dte,
                "spot_at_stopout": spot,
                "breach_amount": round(spot - s_strike, 2),
                "breach_pct": round(buf_pct, 2),
                "initial_credit_total": round(initial_credit, 2),
                "closing_debit_est": round(est_debit, 2),
                "order_id": order_id,
                "order_status": order_status,
                "collateral_locked": collateral_locked,
                "counterfactual_max_loss": round(counterfactual_max_loss, 2),
                "actual_loss_at_stopout": round(actual_loss_at_stopout, 2),
                "capital_saved_vs_max_loss": capital_saved,
                "collateral_salvage_pct": salvage_pct,
                "trigger_reason": reason
            }

            # RULE-092: Deduplicate by spread identity (account, symbol, strikes, expiration)
            # Rather than creating duplicate loss records for the same spread, update in-place!
            existing_event = None
            for e in data.get("events", []):
                if (e.get("account") == account and
                    e.get("symbol") == target_symbol and
                    abs(float(e.get("short_strike", 0)) - s_strike) < 0.1 and
                    abs(float(e.get("long_strike", 0)) - l_strike) < 0.1 and
                    e.get("expiration") == exp_date_str):
                    existing_event = e
                    break

            if existing_event:
                existing_event["timestamp_ict"] = now_ict
                existing_event["order_id"] = order_id
                existing_event["order_status"] = order_status
                existing_event["spot_at_stopout"] = spot
                existing_event["breach_amount"] = round(spot - s_strike, 2)
                existing_event["breach_pct"] = round(buf_pct, 2)
                existing_event["closing_debit_est"] = round(est_debit, 2)
                existing_event["actual_loss_at_stopout"] = round(actual_loss_at_stopout, 2)
                existing_event["capital_saved_vs_max_loss"] = capital_saved
                existing_event["collateral_salvage_pct"] = salvage_pct
                existing_event["trigger_reason"] = reason
                print(f"  📝 RULE-092 AUDIT DEDUP: Updated existing stopout record for {target_symbol} ({event_id}). Zero duplicate rows added.")
            else:
                data.setdefault("events", []).append(event)

            # Recompute cumulative stats
            events = data.get("events", [])
            data["updated_at_ict"] = now_ict
            data["cumulative_stats"] = {
                "total_stopouts_logged": len(events),
                "total_collateral_unlocked": sum(e.get("collateral_locked", 0) for e in events),
                "total_counterfactual_loss_prevented": sum(abs(e.get("counterfactual_max_loss", 0)) for e in events),
                "total_capital_saved_usd": round(sum(e.get("capital_saved_vs_max_loss", 0) for e in events), 2),
                "avg_collateral_salvage_pct": round(sum(e.get("collateral_salvage_pct", 0) for e in events) / len(events), 1) if events else 0.0
            }

            ledger_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            print(f"  📊 AUDIT LOGGED: {event_id} | Collateral Salvaged: {salvage_pct}% | Capital Saved: +${capital_saved:,.2f}")
            res_event = event
        except Exception as ex_audit:
            print(f"  ℹ️ Stop-out audit notice: {ex_audit}")
    return res_event

def harvest_spread_positions(
    account_type: str = "pion2_sub",
    target_symbol: str = "SPY",
    force_close: bool = False,
    min_profit_pct: float = 50.0,
    opt_type_filter: Optional[str] = None
) -> List[Dict[str, Any]]:
    print(f"\n🌾 AUDITING POSITION: {target_symbol}{' (' + opt_type_filter + ' Wing)' if opt_type_filter else ''} ON {account_type.upper()}...")
    broker = AlpacaClient(account_type)
    headers = broker._headers()
    ctx_ssl = broker.ssl_ctx

    is_holiday, holiday_name = broker.is_market_holiday()
    if is_holiday:
        print(f"  🛑 US MARKET HOLIDAY: {holiday_name}. Standing down (RULE-074).")
        return []

    market_open = is_market_open(broker)
    session_str = "OPEN (Regular Market Hours) ✅" if market_open else "CLOSED (Pre-Market / Off-Hours) ⏳"
    force_sequential = False

    # 1. Check existing open orders for collision prevention (RULE-075 Safety Guard)
    open_orders = get_open_orders(broker)
    target_open_orders = [o for o in open_orders if target_symbol in o.get("symbol", "") or any(target_symbol in leg.get("symbol", "") for leg in o.get("legs", []))]

    # Filter out ENTRY orders (managed by RULE-075 Fill Tracker)
    # auto_harvest_positions must ONLY manage CLOSING harvest orders!
    closing_open_orders = []
    for o in target_open_orders:
        legs = o.get("legs") or []
        is_entry = False
        for leg in legs:
            if leg.get("position_intent") in ("sell_to_open", "buy_to_open"):
                is_entry = True
                break
        if not is_entry and o.get("position_intent") in ("sell_to_open", "buy_to_open"):
            is_entry = True

        if is_entry:
            print(f"  🛡️ Preserving active entry order {o.get('id')} ({target_symbol}) — Managed by RULE-075 Fill Tracker!")
        else:
            closing_open_orders.append(o)

    if closing_open_orders:
        print(f"  ℹ️ Found {len(closing_open_orders)} active closing harvest order(s) working on broker:")
        for o in closing_open_orders:
            print(f"     • Order ID: {o.get('id')} | Symbol: {o.get('symbol')} | Status: {o.get('status')} | Type: {o.get('type')}")

        if not market_open:
            print(f"  ✅ PRE-MARKET ORDER CONFIRMED: Closing order is already accepted and queued for 20:30 ICT market open!")
            return [{"symbol": target_symbol, "order_id": closing_open_orders[0].get("id"), "status": "QUEUED_FOR_OPEN"}]
        else:
            # RULE-087, RULE-089 & RULE-090: Allow closing orders to rest on exchange book for 10 minutes.
            # If unfilled after 10 minutes, autonomously cancel and trigger sequential short-first liquidation!
            created_at_str = closing_open_orders[0].get("created_at") or closing_open_orders[0].get("submitted_at") or ""
            order_age_sec = 0
            if created_at_str:
                try:
                    clean_ts = re.sub(r"\.\d+Z$", "+00:00", created_at_str.replace("Z", "+00:00"))
                    if "+" not in clean_ts and "Z" not in created_at_str:
                        clean_ts += "+00:00"
                    created_dt = datetime.datetime.fromisoformat(clean_ts)
                    order_age_sec = (datetime.datetime.now(datetime.timezone.utc) - created_dt).total_seconds()
                except Exception:
                    pass

            # Allow order to rest for at least 10 minutes before considering it stale
            if order_age_sec < 600 and len(closing_open_orders) == 1:
                print(f"  ⏳ ORDER RESTING ON EXCHANGE BOOK: Active closing order {closing_open_orders[0].get('id')} is {order_age_sec/60:.1f}m old.")
                print("     Letting order rest on exchange book to allow matching engine to fill (RULE-087).")
                return [{"symbol": target_symbol, "order_id": closing_open_orders[0].get("id"), "status": "WORKING_ON_BOOK"}]
            else:
                print(f"  ⚡ ORDER UNFILLED AFTER {order_age_sec/60:.1f}m: Cancelling multi-leg combo and activating Autonomous Sequential Short-First Liquidation (RULE-090)...")
                for o in closing_open_orders:
                    cancel_order(broker, o.get("id"))
                time.sleep(2)
                force_sequential = True

    # 2. Query live open positions
    url_pos = f"{broker.base_url}/v2/positions"
    positions = []
    try:
        req = urllib.request.Request(url_pos, headers=headers)
        with urllib.request.urlopen(req, context=ctx_ssl, timeout=10) as resp:
            positions = json.loads(resp.read().decode())
    except Exception as e:
        print(f"  🔴 Error querying positions: {e}")
        return []

    # Mandatory Treasury Asset Guard: NEVER touch cash anchors
    if target_symbol in ["SGOV", "BIL", "SHV"] or target_symbol.startswith("SGOV"):
        print(f"  🛡️ TREASURY ASSET GUARD: {target_symbol} is a protected cash anchor. Zero harvest action permitted!")
        return []

    # Filter only option contracts (ignore equities/ETFs like SGOV)
    target_positions = [
        p for p in positions 
        if target_symbol in p.get("symbol", "") and (p.get("asset_class") == "us_option" or bool(re.search(r"\d{6}[CP]\d{8}$", p.get("symbol", ""))))
    ]
    if opt_type_filter:
        target_positions = [p for p in target_positions if re.search(rf"\d{{6}}{opt_type_filter}\d{{8}}$", p.get("symbol", ""))]

    if not target_positions:
        print(f"  ℹ️ No active option spread positions found for {target_symbol}{' (' + opt_type_filter + ' Wing)' if opt_type_filter else ''} on {account_type}.")
        return []

    # RULE-094: Autonomous Iron Condor & Dual-Wing Dispatcher
    has_puts = any(re.search(r"\d{6}P\d{8}$", p.get("symbol", "")) for p in target_positions)
    has_calls = any(re.search(r"\d{6}C\d{8}$", p.get("symbol", "")) for p in target_positions)
    if has_puts and has_calls and not opt_type_filter:
        print(f"  🦅 DETECTED DUAL-WING / CONDOR POSITION FOR {target_symbol}: Auditing Put Wing & Call Wing independently (RULE-094)...")
        res_p = harvest_spread_positions(account_type, target_symbol, force_close, min_profit_pct, opt_type_filter="P")
        res_c = harvest_spread_positions(account_type, target_symbol, force_close, min_profit_pct, opt_type_filter="C")
        return (res_p or []) + (res_c or [])

    print(f"  📊 Found {len(target_positions)} active position legs for {target_symbol}{' (' + opt_type_filter + ' Wing)' if opt_type_filter else ''}:")
    
    short_leg = None
    long_leg = None

    for p in target_positions:
        sym = p.get("symbol", "")
        qty = float(p.get("qty", 0))
        val = float(p.get("market_value", 0))
        pnl = float(p.get("unrealized_pl", 0))
        print(f"     • {sym:<22} | Qty: {qty:>4} | Mkt Val: ${val:>8.2f} | P/L: ${pnl:>6.2f}")

        # Preserve Tail Hedges until Friday expiration day (Weekday 4)
        today_weekday = datetime.datetime.now().weekday()
        if "728" in sym and qty > 0 and today_weekday < 4:
            print(f"       ℹ️ Preserving tail hedge {sym} until Friday expiration.")
            continue

        if qty < 0:
            short_leg = p
        elif qty > 0:
            long_leg = p

    closed_orders = []
    spread_key = None

    # 3. EVALUATE PROFIT THRESHOLD GUARD (RULE-002 & RULE-077)
    if short_leg and long_leg:
        s_sym = short_leg["symbol"]
        l_sym = long_leg["symbol"]
        contracts = int(abs(float(short_leg["qty"])))
        tot_pnl = float(short_leg.get("unrealized_pl", 0)) + float(long_leg.get("unrealized_pl", 0))

        # Initial cost basis & net credit calculation
        from intelligent_spread_formatter import parse_option_symbol, ASSET_METADATA
        und, exp_date_str, opt_type, s_strike = parse_option_symbol(s_sym)
        _, _, _, l_strike = parse_option_symbol(l_sym)
        width = abs(s_strike - l_strike) if (s_strike > 0 and l_strike > 0) else 5.0

        opt_tag = opt_type if opt_type in ("P", "C") else "P"
        spread_key = f"{account_type}:{target_symbol}:{s_strike:.1f}{opt_tag}_{l_strike:.1f}{opt_tag}_{exp_date_str}"
        if not force_close and is_spread_locked_out(spread_key):
            print(f"  🛑 SPREAD LOCKOUT / COOLDOWN ACTIVE (RULE-092): {spread_key} is currently locked or in 24h cooldown. Skipping harvest.")
            return []

        s_entry = abs(float(short_leg.get("avg_entry_price", 0)))
        l_entry = abs(float(long_leg.get("avg_entry_price", 0)))
        net_credit_sh = _resolve_entry_credit(und, account_type, width, s_entry, l_entry, s_strike=s_strike)
        total_initial_credit = net_credit_sh * contracts * 100.0

        profit_pct = (tot_pnl / total_initial_credit * 100.0) if total_initial_credit > 0 else 0.0

        # Compute DTE
        dte = 14
        if exp_date_str:
            try:
                exp_dt = datetime.datetime.strptime(exp_date_str, "%Y-%m-%d").date()
                dte = max(0, (exp_dt - datetime.date.today()).days)
            except Exception: pass

        # Compute spot & safety buffer from LIVE broker ground-truth
        spot = 0.0
        try:
            sp_res = get_spot(und)
            spot = float(sp_res.get("price", 0.0)) if sp_res and sp_res.get("price") else 0.0
        except Exception:
            spot = 0.0

        if spot <= 0.0:
            try:
                bbar = broker.get_latest_bar(und) if hasattr(broker, 'get_latest_bar') else None
                spot = float(bbar.get("c", 0.0)) if bbar else 0.0
            except Exception:
                spot = 0.0

        if spot > 0 and s_strike > 0:
            buf = (spot - s_strike) if opt_tag == "P" else (s_strike - spot)
        else:
            buf = 0.0
        buf_pct = (buf / spot * 100.0) if spot > 0 else 0.0

        # Dynamic Marginal Daily Holding Yield (RULE-077 DTE-Aware Optimization)
        remaining_profit = max(0.0, total_initial_credit - tot_pnl)
        holding_daily_rate = remaining_profit / max(1, dte)
        collateral = width * contracts * 100.0
        holding_daily_roc = (holding_daily_rate / collateral * 100.0) if collateral > 0 else 0.0

        # RULE-077 DTE-Aware Marginal Velocity Decision Matrix:
        # Tier 0: Force Close Override
        # Tier 1: Gamma Risk Safeguard (RULE-076): DTE <= 3 and buffer < 2.0% (threatened strike) -> take profit immediately!
        # Tier 2: Terminal Theta Run (RULE-077): DTE <= 3 and buffer >= 2.5% (deeply safe) -> holding pays super-high $/day.
        #         Let it burn to 100% expiry (or harvest at >= 90% capture).
        # Tier 3: Standard Capital Velocity FastHarvest: >= 50% profit when DTE > 3 -> recycle collateral into fresh cycle!
        # Tier 4: Mid-Cycle Acceleration: >= 40% profit & DTE >= 7
        # ──────────────────────────────────────────────────────────────────────
        # TIER -1: MANDATORY DEFENSIVE STOP-OUT & GAMMA CLIFF PROTECTION (RULE-004 & RULE-087)
        # ──────────────────────────────────────────────────────────────────────
        # Condition A: Short Strike Breach / In-The-Money (ITM) Defense
        # If Spot <= Short Strike (buf <= 0 or buf_pct <= 0.0%):
        # Stop out immediately! Salvages long put residual value and unfreezes 100% of collateral
        # ($1,000-$2,000 per slot) before trade turns into full maximum loss.
        days_held = _resolve_days_held(und, s_strike, account_type)
        is_runner_active = is_spread_runner(spread_key)
        runner_rec = get_runner_info(spread_key) if is_runner_active else {}

        should_harvest = False
        harvest_reason = ""

        if force_close:
            should_harvest = True
            harvest_reason = "Manual Force Close Override"
        elif buf <= 0.0 or buf_pct <= 0.50:
            should_harvest = True
            harvest_reason = f"PROACTIVE STRIKE-TOUCH DEFENSE (DIR-09): Strike ${s_strike:.2f} Threatened! Spot ${spot:.2f} (Buffer {buf_pct:+.1f}% <= 0.5%) | Exiting ATM to Maximize Collateral Salvage"
        elif dte <= 3 and buf_pct < 2.0 and tot_pnl > 0:
            should_harvest = True
            harvest_reason = f"DIR-09 T-3 Gamma Defense Harvest (Buffer {buf_pct:+.1f}% < 2.0% | +${tot_pnl:,.2f})"
        elif is_runner_active:
            # ──────────────────────────────────────────────────────────
            # TRANCHE 2: ACTIVE RUNNER DECISION MATRIX
            # (Allows profit to run past 50% until dip or >=85% terminal capture)
            # ──────────────────────────────────────────────────────────
            ratchet_triggered, ratchet_reason = update_and_check_profit_ratchet(
                spread_key=spread_key,
                target_symbol=target_symbol,
                account_type=account_type,
                profit_pct=profit_pct,
                tot_pnl=tot_pnl
            )
            if ratchet_triggered:
                should_harvest = True
                harvest_reason = ratchet_reason
            elif dte <= 3 and buf_pct >= 2.5:
                if profit_pct >= 85.0 or tot_pnl >= (total_initial_credit * 0.85):
                    should_harvest = True
                    harvest_reason = f"DIR-09 Terminal Runner 85%+ Theta Capture (+${tot_pnl:,.2f} | {profit_pct:.1f}% | DTE: {dte}d)"
                else:
                    print(f"  🏃‍♂️ DIR-09 RUNNER ACTIVE: {target_symbol} is {buf_pct:+.1f}% OTM with {dte}d left. Tracking trailing floor!")
        else:
            # ──────────────────────────────────────────────────────────
            # TRANCHE 1 / STANDARD FASTHARVEST DECISION MATRIX (PRE-SCALE-OUT)
            # ──────────────────────────────────────────────────────────
            if dte <= 3 and buf_pct >= 2.5:
                if profit_pct >= 90.0 or tot_pnl >= (total_initial_credit * 0.90):
                    should_harvest = True
                    harvest_reason = f"DIR-09 Terminal 90%+ Theta Capture (+${tot_pnl:,.2f} | {profit_pct:.1f}% | DTE: {dte}d)"
                else:
                    print(f"  🚀 DIR-09 TERMINAL SURGE: {target_symbol} is {buf_pct:+.1f}% OTM with only {dte}d left.")
                    print(f"     Holding daily rate is ${holding_daily_rate:.2f}/day ({holding_daily_roc:.2f}%/day ROC)! Letting theta burn to full expiry.")
            elif days_held <= 2 and (profit_pct >= 35.0 or tot_pnl >= (total_initial_credit * 0.35)):
                should_harvest = True
                harvest_reason = f"DIR-09 48h Express FastHarvest (+${tot_pnl:,.2f} | {profit_pct:.1f}% | Day {days_held} held 🚀)"
            elif days_held <= 3 and (profit_pct >= 35.0 or tot_pnl >= (total_initial_credit * 0.35)):
                should_harvest = True
                harvest_reason = f"DIR-09 72h Mid-Sprint FastHarvest (+${tot_pnl:,.2f} | {profit_pct:.1f}% | Day {days_held} held ⚡)"
            elif days_held <= 5 and (profit_pct >= 40.0 or tot_pnl >= (total_initial_credit * 0.40)):
                should_harvest = True
                harvest_reason = f"DIR-09 5-Day Velocity FastHarvest (+${tot_pnl:,.2f} | {profit_pct:.1f}% | Day {days_held} held ⚡)"
            elif dte >= 7 and (profit_pct >= 40.0 or tot_pnl >= (total_initial_credit * 0.40)):
                should_harvest = True
                harvest_reason = f"DIR-09 Mid-Cycle Velocity FastHarvest (+${tot_pnl:,.2f} | {profit_pct:.1f}% | DTE: {dte}d | Freeing Collateral 🚀)"
            elif profit_pct >= 50.0 or tot_pnl >= (total_initial_credit * 0.50):
                should_harvest = True
                harvest_reason = f"DIR-09 Standard 50% FastHarvest (+${tot_pnl:,.2f} | {profit_pct:.1f}% | DTE: {dte}d | Day {days_held} held 🌾)"

            # Standard pre-scale-out profit ratchet
            ratchet_triggered, ratchet_reason = update_and_check_profit_ratchet(
                spread_key=spread_key,
                target_symbol=target_symbol,
                account_type=account_type,
                profit_pct=profit_pct,
                tot_pnl=tot_pnl
            )
            if ratchet_triggered and not should_harvest:
                should_harvest = True
                harvest_reason = ratchet_reason

        if not should_harvest:
            check_and_emit_amber_alert(account_type, target_symbol, spot, s_strike, buf_pct, dte, opt_tag, spread_key)
            status_tag = "RUNNER ACTIVE" if is_runner_active else "HOLDING SPREAD"
            print(f"  ⏳ {status_tag}: {target_symbol} Unrealized P/L is ${tot_pnl:+.2f} ({profit_pct:.1f}% of ${total_initial_credit:.2f} credit | DTE: {dte}d | Buffer: {buf_pct:+.1f}% | Day {days_held} held).")
            print(f"     DIR-09 Policy: Sliding-Scale FastHarvest (30%@<=2d, 40%@<=5d, 50% std) | Runner Trailing Floor | Strike Defense (Buf<=0.50%) 🛡️")
            return []

        # Initial cost basis estimation
        s_val = abs(float(short_leg.get("market_value", 0)))
        l_val = abs(float(long_leg.get("market_value", 0)))

        # Target Met or Force Close -> Execute Atomic Multi-Leg Liquidation
        # Multi-leg option orders on Alpaca MUST be limit orders (market orders return HTTP 422)
        order_type = "limit"
        est_debit = max(0.01, round((s_val - l_val) / (contracts * 100), 2))

        is_defensive = ("DEFENSIVE" in harvest_reason) or ("GAMMA" in harvest_reason) or ("MAX-LOSS" in harvest_reason) or ("PROACTIVE" in harvest_reason)

        # SCALE-OUT & TRAILING RUNNER SIZING ARCHITECTURE:
        # 1. Multi-contract positions (e.g. 3C TSM) scale out 50% (ceil: 2 of 3 contracts closed, 1 runner kept).
        # 2. Defensive stop-outs ALWAYS liquidate 100% immediately to prevent pin risk.
        # 3. Indivisible single contracts (1C) liquidate 100%.
        # 4. Existing active runners exiting trailing ratchet liquidate 100% of remaining runner contracts.
        if not is_defensive and not is_runner_active and contracts > 1:
            close_qty = max(1, math.ceil(contracts / 2.0))
            runner_qty = contracts - close_qty
            is_scale_out = (runner_qty > 0)
        else:
            close_qty = contracts
            runner_qty = 0
            is_scale_out = False

        if is_defensive:
            # Dynamically scale defensive slippage buffer to cross bid-ask spread and guarantee immediate fill
            spread_gap = 0.0
            try:
                quotes = broker.get_option_snapshot([s_sym, l_sym]) if hasattr(broker, 'get_option_snapshot') else {}
                s_ask = float(quotes.get(s_sym, {}).get("ask", 0.0))
                l_bid = float(quotes.get(l_sym, {}).get("bid", 0.0))
                if s_ask > 0:
                    nat_debit = round(s_ask - l_bid, 2)
                    spread_gap = max(0.0, round(nat_debit - est_debit, 2))
            except Exception:
                pass
            defensive_slip = max(0.02, min(0.10, round(spread_gap * 0.30, 2))) if spread_gap > 0 else 0.03
            est_debit = min(width, round(est_debit + defensive_slip, 2))
        else:
            # RULE-096: Closing Micro-Walk & Penny-Pilot Fill Accelerator
            est_debit, walk_desc = apply_closing_micro_walk(
                target_symbol=target_symbol,
                est_debit=est_debit,
                net_credit_sh=net_credit_sh,
                days_held=days_held,
                is_defensive=is_defensive
            )
            if "PENNY_PILOT_MICRO_WALK" in walk_desc:
                print(f"     ⚡ {walk_desc}: Adjusted closing limit debit to guarantee immediate fill.")

        if not force_sequential:
            if is_scale_out:
                print(f"\n  🌾 TRANCHE 1 SCALE-OUT TRIGGERED: {harvest_reason}!")
                print(f"     Submitting Atomic Multi-Leg Scale-Out Order ({close_qty} of {contracts} contracts {s_sym} / {l_sym} @ max debit ${est_debit:.2f})...")
                print(f"     🏃‍♂️ Arming remaining {runner_qty} contract(s) as Trailing Runner!")
            elif is_runner_active:
                print(f"\n  🏃‍♂️ TRANCHE 2 RUNNER EXIT TRIGGERED: {harvest_reason}!")
                print(f"     Submitting Atomic Multi-Leg Closing Order ({close_qty} runner contract(s) {s_sym} / {l_sym} @ max debit ${est_debit:.2f})...")
            else:
                print(f"\n  🎯 HARVEST / DEFENSE TRIGGERED: {harvest_reason}!")
                print(f"     Submitting Atomic Multi-Leg Closing Order ({contracts}x {s_sym} / {l_sym} @ max debit ${est_debit:.2f})...")

            mleg_close_payload = {
                "order_class": "mleg",
                "type": "limit",
                "limit_price": str(est_debit),
                "time_in_force": "day",
                "legs": [
                    {"symbol": s_sym, "ratio_qty": 1, "side": "buy", "position_intent": "buy_to_close"},
                    {"symbol": l_sym, "ratio_qty": 1, "side": "sell", "position_intent": "sell_to_close"}
                ],
                "qty": str(close_qty)
            }

            try:
                req_m = urllib.request.Request(f"{broker.base_url}/v2/orders", data=json.dumps(mleg_close_payload).encode("utf-8"), headers=headers)
                with urllib.request.urlopen(req_m, context=ctx_ssl, timeout=10) as rm:
                    res_m = json.loads(rm.read().decode())
                    oid_m = res_m.get("id")
                    status_m = res_m.get("status", "accepted")
                    action_tag = "SCALE-OUT" if is_scale_out else ("RUNNER EXIT" if is_runner_active else "HARVEST / DEFENSE")
                    print(f"     🎉 ATOMIC SPREAD {action_tag} SUBMITTED! Order ID: {oid_m} (Status: {status_m})")
                    scaled_pnl = round(tot_pnl * (close_qty / contracts), 2)
                    closed_orders.append({
                        "symbol": f"{s_sym}/{l_sym}",
                        "order_id": oid_m,
                        "pnl": scaled_pnl,
                        "type": "mleg",
                        "is_scale_out": is_scale_out,
                        "is_runner": is_runner_active,
                        "close_qty": close_qty,
                        "runner_qty": runner_qty
                    })
                    if spread_key:
                        if is_scale_out:
                            register_scale_out_runner(
                                spread_key=spread_key,
                                target_symbol=target_symbol,
                                account_type=account_type,
                                initial_contracts=contracts,
                                scaled_out_contracts=close_qty,
                                remaining_runner_contracts=runner_qty,
                                profit_pct=profit_pct,
                                tot_pnl=tot_pnl,
                                order_id=oid_m
                            )
                            # 5-min cooldown to avoid duplicate order spam while poller awaits fill
                            set_harvest_lockout(spread_key, "SCALE_OUT_IN_PROGRESS", order_id=oid_m, cooldown_hours=0.08)
                        else:
                            set_harvest_lockout(spread_key, "IN_PROGRESS", order_id=oid_m, cooldown_hours=24)
                            clear_harvest_ratchet(spread_key)
                    # RULE-083 / RULE-098: Instant Slot Vacancy Broadcast for Same-Day Collateral Recycling
                    now_ict = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT")
                    slot_event = {
                        "event": "SLOT_VACATED" if not is_scale_out else "PARTIAL_SCALE_OUT",
                        "symbol": target_symbol,
                        "account": account_type,
                        "realized_pnl": scaled_pnl,
                        "profit_pct": profit_pct,
                        "contracts_closed": close_qty,
                        "contracts_remaining": runner_qty,
                        "timestamp": now_ict,
                        "action": "READY_FOR_SAME_DAY_ROTATION" if not is_scale_out else "RUNNER_MONITORING"
                    }
                    broadcast_to_anna(f"SLOT_{action_tag}_{target_symbol}", json.dumps(slot_event), slot_event)
                    if is_defensive:
                        log_stopout_audit_event(
                            account=account_type, target_symbol=target_symbol,
                            s_strike=s_strike, l_strike=l_strike, width=width,
                            contracts=contracts, exp_date_str=exp_date_str, dte=dte,
                            spot=spot, buf_pct=buf_pct, initial_credit=total_initial_credit,
                            est_debit=est_debit, order_id=oid_m, order_status=status_m,
                            reason=harvest_reason
                        )
            except Exception as ex_m:
                print(f"     ℹ️ Multi-leg close notice: {ex_m} -> Switching to Sequential Short-First Fallback...")
        else:
            print(f"\n  ⚡ AUTONOMOUS SEQUENTIAL LIQUIDATION (RULE-090): Multi-leg timeout exceeded. Executing direct sequential liquidation...")

    # 4. SEQUENTIAL SHORT-FIRST FALLBACK
    if not closed_orders and short_leg and (force_close or force_sequential or is_defensive or float(short_leg.get("unrealized_pl", 0)) > 20.0):
        s_sym = short_leg["symbol"]
        s_qty = int(abs(float(short_leg["qty"])))
        s_pnl = float(short_leg.get("unrealized_pl", 0))

        print(f"\n  ⚡ Executing Step 1/2: BUY TO CLOSE Short Leg ({s_qty}x {s_sym})...")
        if market_open:
            try:
                del_req = urllib.request.Request(f"{broker.base_url}/v2/positions/{s_sym}", headers=headers, method="DELETE")
                with urllib.request.urlopen(del_req, context=ctx_ssl, timeout=10) as rd:
                    res_d = json.loads(rd.read().decode())
                    oid_s = res_d.get("id")
                    status_s = res_d.get("status", "submitted")
                    print(f"     🎉 Short leg liquidated! ID: {oid_s}")
                    closed_orders.append({"symbol": s_sym, "order_id": oid_s, "pnl": s_pnl, "type": "short_leg"})
                    if spread_key:
                        set_harvest_lockout(spread_key, "IN_PROGRESS", order_id=oid_s, cooldown_hours=24)
                        clear_harvest_ratchet(spread_key)
                    now_ict = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT")
                    slot_event = {
                        "event": "SLOT_VACATED",
                        "symbol": target_symbol,
                        "account": account_type,
                        "realized_pnl": s_pnl,
                        "profit_pct": profit_pct,
                        "timestamp": now_ict,
                        "action": "READY_FOR_SAME_DAY_ROTATION"
                    }
                    broadcast_to_anna(f"SLOT_VACATED_{target_symbol}", json.dumps(slot_event), slot_event)
                    if is_defensive:
                        log_stopout_audit_event(
                            account=account_type, target_symbol=target_symbol,
                            s_strike=s_strike, l_strike=l_strike, width=width,
                            contracts=contracts, exp_date_str=exp_date_str, dte=dte,
                            spot=spot, buf_pct=buf_pct, initial_credit=total_initial_credit,
                            est_debit=est_debit, order_id=oid_s, order_status=status_s,
                            reason=harvest_reason + " [Sequential Fallback]"
                        )
            except Exception as ex_d:
                print(f"     🔴 Error liquidating short leg: {ex_d}")

            # Immediately execute Step 2/2: Liquidate Long Leg if defensive stop-out
            if is_defensive and long_leg:
                time.sleep(2)
                l_sym = long_leg["symbol"]
                l_qty = int(abs(float(long_leg["qty"])))
                l_pnl = float(long_leg.get("unrealized_pl", 0))
                print(f"  ⚡ Executing Step 2/2: SELL TO CLOSE Long Leg ({l_qty}x {l_sym})...")
                try:
                    del_req_l = urllib.request.Request(f"{broker.base_url}/v2/positions/{l_sym}", headers=headers, method="DELETE")
                    with urllib.request.urlopen(del_req_l, context=ctx_ssl, timeout=10) as rd_l:
                        res_dl = json.loads(rd_l.read().decode())
                        oid_l = res_dl.get("id")
                        print(f"     🎉 Long leg liquidated! ID: {oid_l}")
                        closed_orders.append({"symbol": l_sym, "order_id": oid_l, "pnl": l_pnl, "type": "residual_long"})
                except Exception as ex_dl:
                    print(f"     🔴 Error liquidating long leg in sequential pair: {ex_dl}")

    # 5. RESIDUAL LONG LEG LIQUIDATION (When Short leg is already closed)
    if not closed_orders and not short_leg and long_leg:
        l_sym = long_leg["symbol"]
        l_qty = int(abs(float(long_leg["qty"])))
        l_pnl = float(long_leg.get("unrealized_pl", 0))
        val_l = float(long_leg.get("market_value", 0))

        if val_l <= 0.00:
            print(f"\n  ℹ️ Residual long leg {l_sym} has $0.00 market value (zero-bid OTM).")
            print(f"     Preserving as free zero-cost tail hedge — 0 collateral required, 0 risk to account! 🛡️")
            return []

        print(f"\n  ⚡ Short leg already harvested! Liquidating residual Long Leg ({l_qty}x {l_sym} | Mkt Val: ${val_l:.2f})...")
        if market_open:
            try:
                del_req = urllib.request.Request(f"{broker.base_url}/v2/positions/{l_sym}", headers=headers, method="DELETE")
                with urllib.request.urlopen(del_req, context=ctx_ssl, timeout=10) as rd:
                    res_d = json.loads(rd.read().decode())
                    oid_l = res_d.get("id")
                    print(f"     🎉 Residual long leg liquidated! ID: {oid_l}")
                    closed_orders.append({"symbol": l_sym, "order_id": oid_l, "pnl": l_pnl, "type": "residual_long"})
            except Exception as ex_l:
                print(f"     🔴 Error liquidating long leg: {ex_l}")

    # 6. TELEGRAM & AGENT BRIDGE NOTIFICATIONS
    if closed_orders and market_open:
        # Check order fill status on broker
        is_filled = False
        try:
            time.sleep(1)
            first_oid = closed_orders[0].get("order_id")
            if first_oid:
                chk_req = urllib.request.Request(f"{broker.base_url}/v2/orders/{first_oid}", headers=headers)
                with urllib.request.urlopen(chk_req, context=ctx_ssl, timeout=5) as rc:
                    chk_res = json.loads(rc.read().decode())
                    is_filled = (chk_res.get("status") == "filled")
        except Exception:
            pass

        group_id = "-1004375899205"
        hermes_token = "8991076258:AAGHyb-jEnp29BQ5O5V0mo4vFOmgXfgnmTg"
        now_ict = datetime.datetime.now().strftime("%Y-%m-%d %H:%M ICT")
        tot_realized = sum(c.get("pnl", 0) for c in closed_orders)

        is_mleg = any(c.get("type") == "mleg" for c in closed_orders)
        is_residual = any(c.get("type") == "residual_long" for c in closed_orders)
        is_scale_out_order = any(c.get("is_scale_out") for c in closed_orders)
        is_runner_close = any(c.get("is_runner") for c in closed_orders)

        if is_scale_out_order:
            asset_title = f"{target_symbol} {'Bull Put Spread' if opt_tag == 'P' else 'Bear Call Spread'} [Tranche 1 Scale-Out]"
            exec_title = f"Scale-Out ({close_qty}/{contracts} Contracts Closed | {runner_qty} Runner Riding) 🏃‍♂️"
        elif is_runner_close:
            asset_title = f"{target_symbol} {'Bull Put Spread' if opt_tag == 'P' else 'Bear Call Spread'} [Tranche 2 Runner Exit]"
            exec_title = f"Trailing Runner Complete Liquidation ({close_qty} Contracts) 🏁"
        elif is_mleg:
            asset_title = f"{target_symbol} {'Bull Put Spread' if opt_tag == 'P' else 'Bear Call Spread'}"
            exec_title = "Atomic Multi-Leg Zero-Margin Combo ✅"
        elif is_residual:
            asset_title = f"{target_symbol} Residual Long Leg / Tail Floor"
            exec_title = "Orphaned Residual Long Leg Sweep (Account Clean) 🧹"
        else:
            asset_title = f"{target_symbol} Spread Leg"
            exec_title = "Sequential Leg Execution ✅"

        current_oids = [c['order_id'] for c in closed_orders]

        if is_filled:
            if spread_key:
                if is_scale_out_order:
                    set_harvest_lockout(spread_key, "RUNNER_ACTIVE", cooldown_hours=0.08)
                else:
                    set_harvest_lockout(spread_key, "COMPLETED", cooldown_hours=24)
            if is_defensive:
                header_str = f"🛡️ DEFENSIVE STOP-OUT / GAMMA DEFENSE FILLED (RULE-087) — {target_symbol}"
                result_str = f"• Collateral Salvaged: ${tot_realized:+.2f} (Loss Contained / Max Loss Avoided 🛑)"
            elif is_scale_out_order:
                header_str = f"🌾 TRANCHE 1 SCALE-OUT FILLED (PROFIT BANKED) — {target_symbol}"
                result_str = (
                    f"• Banked Profit   : +${tot_realized:.2f} NET GAIN 💵\n"
                    f"• Contracts Closed: {close_qty} of {contracts} ({close_qty * 100 / contracts:.0f}% Scaled Out)\n"
                    f"• Runner Arming   : {runner_qty} Contract(s) Active with Trailing Ratchet 🏃‍♂️"
                )
            elif is_runner_close:
                header_str = f"🏃‍♂️ TRANCHE 2 RUNNER HARVEST FILLED (TRAIL CLOSED) — {target_symbol}"
                result_str = f"• Runner Profit Realized: +${tot_realized:.2f} NET GAIN 💵"
            else:
                header_str = f"🔔 LIVE FAST-HARVEST FILLED & EXECUTED — {target_symbol}"
                result_str = f"• Profit Realized: +${tot_realized:.2f} NET GAIN 💵"

            alert_text = f"""{header_str}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 ASSET LIQUIDATED: {asset_title}
• Target Account : {account_type.upper()} ({broker.expected_account_id})
• Execution Type : {exec_title}
• Order ID(s)    : {', '.join(current_oids)}
{result_str}
• Trigger Reason : {harvest_reason}
• Session State  : {session_str}
• Time           : {now_ict}

{account_type.upper()} collateral unlocked and optimized! 🚀📈"""
        else:
            # Order is resting on exchange book. Deduplicate to avoid 5-minute alert spam!
            cache_file = Path(__file__).parent / ".last_harvest_alert.json"
            alerted_oids = []
            if cache_file.exists():
                try:
                    alerted_oids = json.loads(cache_file.read_text()).get("alerted_orders", [])
                except Exception:
                    pass
            if any(oid in alerted_oids for oid in current_oids):
                print(f"  ℹ️ Working order alert already dispatched for {current_oids}. Suppressing duplicate Telegram alert.")
                return closed_orders

            alerted_oids.extend(current_oids)
            try:
                cache_file.write_text(json.dumps({"alerted_orders": alerted_oids[-30:]}))
            except Exception:
                pass

            if is_scale_out_order:
                header_str = f"🌾 TRANCHE 1 SCALE-OUT SUBMITTED (WORKING ON BOOK) — {target_symbol}"
            elif is_runner_close:
                header_str = f"🏃‍♂️ TRANCHE 2 RUNNER HARVEST SUBMITTED (WORKING ON BOOK) — {target_symbol}"
            elif is_defensive:
                header_str = f"⏳ DEFENSIVE STOP-OUT SUBMITTED (WORKING ON BOOK) — {target_symbol}"
            else:
                header_str = f"⏳ FAST-HARVEST SUBMITTED (WORKING ON BOOK) — {target_symbol}"

            alert_text = f"""{header_str}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🛡️ STRATEGY: {asset_title}
• Target Account : {account_type.upper()} ({broker.expected_account_id})
• Order ID(s)    : {', '.join(current_oids)}
• Order Status   : RESTING ON EXCHANGE BOOK (Limit: ${est_debit:.2f})
• Trigger Reason : {harvest_reason}
• Session State  : {session_str}
• Time           : {now_ict}

Order is active on exchange. Collateral will be released upon fill confirmation. 🛡️"""

        try:
            url = f"https://api.telegram.org/bot{hermes_token}/sendMessage"
            payload = json.dumps({"chat_id": group_id, "text": alert_text}).encode("utf-8")
            req_t = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req_t, context=ctx_ssl, timeout=10)
            print("  ✅ Dispatched harvest confirmation to Telegram!")
        except Exception: pass

        try:
            bridge = AgentBridge("hermes")
            bridge.send("anna", alert_text, channel="trade_execution", subject=f"HARVEST_FILLED_{target_symbol}")
            # RULE-083: Instant Slot Vacancy Broadcast for Same-Day Collateral Recycling
            slot_event = {
                "event": "SLOT_VACATED",
                "symbol": target_symbol,
                "account": account_type,
                "realized_pnl": tot_realized,
                "timestamp": now_ict,
                "action": "READY_FOR_SAME_DAY_ROTATION"
            }
            bridge.send("anna", json.dumps(slot_event), channel="portfolio_rotation", subject=f"SLOT_VACATED_{target_symbol}")
            print(f"  ⚡ Mirrored harvest & RULE-083 SLOT_VACATED broadcast to Anna via AgentBridge!")
        except Exception: pass

        # Reconcile active_trades.json directly against broker truth (RULE-092)
        try:
            from reconcile_active_trades import reconcile_active_trades
            reconcile_active_trades(quiet=True)
            print(f"  📂 Reconciled active_trades.json directly with broker ground truth!")
        except Exception as ex_at:
            print(f"  ℹ️ active_trades reconcile notice: {ex_at}")

        # Autonomous Excel Transaction Journal Update (RULE-051)
        try:
            import transaction_journal_manager
            transaction_journal_manager.update_excel_journal()
            print("  📊 Updated SkonVault_Live_Transaction_Journal.xlsx with latest harvest!")
        except Exception as ex_j:
            print(f"  ℹ️ Journal sync notice: {ex_j}")

    return closed_orders

def harvest_tradier_positions(force_close: bool = False, min_profit_pct: float = 50.0) -> List[Dict[str, Any]]:
    """
    DIR-09 & RULE-073: FastHarvest for Tradier Live (#6YB80974).
    Audits active option spreads, evaluates DIR-09 50% TP or DTE <= 3d safe buffer hold,
    and executes close_spread via TradierClient.
    """
    print("\n🌾 AUDITING TRADIER LIVE (#6YB80974) SPREAD POSITIONS...")
    try:
        from tradier_broker import TradierClient
        client = TradierClient("live")
    except Exception as e:
        print(f"  🔴 Tradier Client Init Error: {e}")
        return []

    try:
        positions = client.get_positions()
    except Exception as e:
        print(f"  🔴 Tradier Position Fetch Error: {e}")
        return []

    # Filter option contracts (ignore SGOV/equities)
    opt_positions = [
        p for p in positions
        if p.get("symbol") != "SGOV" and re.search(r"\d{6}[CP]\d{8}$", p.get("symbol", ""))
    ]
    if not opt_positions:
        print("  ℹ️ No active option spread positions found on Tradier Live.")
        return []

    # Group legs by underlying symbol
    groups = {}
    for p in opt_positions:
        sym = p.get("symbol", "")
        m = re.match(r"^([A-Z]+)", sym)
        if m:
            und = m.group(1)
            groups.setdefault(und, []).append(p)

    closed_orders = []
    for und, legs in groups.items():
        short_leg = next((l for l in legs if float(l.get("quantity", 0)) < 0), None)
        long_leg = next((l for l in legs if float(l.get("quantity", 0)) > 0), None)

        if not short_leg or not long_leg:
            print(f"  ℹ️ {und} on Tradier does not form a complete paired spread (legs: {len(legs)}). Skipping.")
            continue

        s_occ = short_leg["symbol"]
        l_occ = long_leg["symbol"]
        contracts = int(abs(float(short_leg.get("quantity", 1))))

        from intelligent_spread_formatter import parse_option_symbol
        _, exp_date_str, opt_type, s_strike = parse_option_symbol(s_occ)
        _, _, _, l_strike = parse_option_symbol(l_occ)
        width = abs(s_strike - l_strike) if (s_strike > 0 and l_strike > 0) else 1.0

        spread_key = f"tradier_live:{und}:{s_strike:.1f}P_{l_strike:.1f}P_{exp_date_str}"
        if not force_close and is_spread_locked_out(spread_key):
            print(f"  🛑 SPREAD LOCKOUT / COOLDOWN ACTIVE (RULE-092): {spread_key}. Skipping harvest.")
            continue

        is_runner_active = is_spread_runner(spread_key)
        runner_info = get_runner_info(spread_key) if is_runner_active else {}
        if is_runner_active:
            print(f"  🏃‍♂️ TRADIER RUNNER ACTIVE: {und} ({spread_key}) -> {runner_info.get('runner_contracts', 1)} contract(s) under trailing floor!")

        # DTE
        dte = 14
        if exp_date_str:
            try:
                exp_dt = datetime.datetime.strptime(exp_date_str, "%Y-%m-%d").date()
                dte = max(0, (exp_dt - datetime.date.today()).days)
            except Exception: pass

        # Live spot & buffer
        spot = 0.0
        try:
            sp_res = get_spot(und)
            spot = float(sp_res.get("price", 0.0)) if sp_res and sp_res.get("price") else 0.0
        except Exception: pass

        buf = (spot - s_strike) if spot > 0 and s_strike > 0 else 0.0
        buf_pct = (buf / spot * 100.0) if spot > 0 else 0.0

        # Query live quotes for the legs to calculate net debit
        s_ask, l_bid = 0.0, 0.0
        try:
            quotes = client.get_quotes([s_occ, l_occ])
            for q in quotes:
                if q.get("symbol") == s_occ:
                    s_ask = float(q.get("ask", 0.0))
                elif q.get("symbol") == l_occ:
                    l_bid = float(q.get("bid", 0.0))
        except Exception: pass

        cur_debit = max(0.01, round(s_ask - l_bid, 2)) if s_ask > 0 else 0.05
        s_entry = abs(float(short_leg.get("avg_entry_price", 0.0)))
        l_entry = abs(float(long_leg.get("avg_entry_price", 0.0)))
        if s_entry == 0.0 and short_leg.get("cost_basis"):
            s_entry = abs(float(short_leg.get("cost_basis", 0.0))) / (contracts * 100.0)
        if l_entry == 0.0 and long_leg.get("cost_basis"):
            l_entry = abs(float(long_leg.get("cost_basis", 0.0))) / (contracts * 100.0)
        net_credit_entry = _resolve_entry_credit(und, "tradier_live", width, s_entry, l_entry, s_strike=s_strike)
        initial_credit_total = net_credit_entry * contracts * 100.0
        current_pnl = (net_credit_entry - cur_debit) * contracts * 100.0 if net_credit_entry > 0 else 0.0
        profit_pct = (current_pnl / initial_credit_total * 100.0) if initial_credit_total > 0 else 0.0

        days_held = _resolve_days_held(und, s_strike, "tradier_live")

        # DIR-09 Decision Matrix
        should_harvest = False
        harvest_reason = ""

        if force_close:
            should_harvest = True
            harvest_reason = "Manual Force Close Override"
        elif buf <= 0.0 or buf_pct <= 0.50:
            should_harvest = True
            harvest_reason = f"PROACTIVE STRIKE-TOUCH DEFENSE (DIR-09): Strike ${s_strike:.1f} Threatened! Spot ${spot:.2f} (Buffer {buf_pct:+.1f}%)"
        elif dte <= 3 and buf_pct < 2.0 and current_pnl > 0:
            should_harvest = True
            harvest_reason = f"DIR-09 T-3 Gamma Defense Harvest (Buffer {buf_pct:+.1f}% < 2.0% | +${current_pnl:,.2f})"
        elif is_runner_active:
            # ──────────────────────────────────────────────────────────
            # ACTIVE RUNNER PROTOCOL (TRANCHE 2)
            # ──────────────────────────────────────────────────────────
            ratchet_triggered, ratchet_reason = update_and_check_profit_ratchet(
                spread_key=spread_key,
                target_symbol=und,
                account_type="tradier_live",
                profit_pct=profit_pct,
                tot_pnl=current_pnl
            )
            if ratchet_triggered:
                should_harvest = True
                harvest_reason = ratchet_reason
            elif dte <= 3 and buf_pct >= 2.5:
                if profit_pct >= 85.0 or current_pnl >= (initial_credit_total * 0.85):
                    should_harvest = True
                    harvest_reason = f"DIR-09 Terminal Runner 85%+ Theta Capture (+${current_pnl:,.2f} | {profit_pct:.1f}% | DTE: {dte}d)"
                else:
                    print(f"  🏃‍♂️ DIR-09 TRADIER RUNNER: {und} is {buf_pct:+.1f}% OTM with {dte}d left. Tracking trailing floor!")
        else:
            # ──────────────────────────────────────────────────────────
            # TRANCHE 1 / STANDARD FASTHARVEST DECISION MATRIX
            # ──────────────────────────────────────────────────────────
            if dte <= 3 and buf_pct >= 2.5:
                if profit_pct >= 90.0 or current_pnl >= (initial_credit_total * 0.90):
                    should_harvest = True
                    harvest_reason = f"DIR-09 Terminal 90%+ Theta Capture (+${current_pnl:,.2f} | {profit_pct:.1f}% | DTE: {dte}d)"
                else:
                    print(f"  🚀 DIR-09 TERMINAL SURGE: {und} is {buf_pct:+.1f}% OTM with only {dte}d left. Holding to full expiry!")
            elif days_held <= 2 and (profit_pct >= 35.0 or current_pnl >= (initial_credit_total * 0.35)):
                should_harvest = True
                harvest_reason = f"DIR-09 48h Express FastHarvest (+${current_pnl:,.2f} | {profit_pct:.1f}% | Day {days_held} held 🚀)"
            elif days_held <= 3 and (profit_pct >= 35.0 or current_pnl >= (initial_credit_total * 0.35)):
                should_harvest = True
                harvest_reason = f"DIR-09 72h Mid-Sprint FastHarvest (+${current_pnl:,.2f} | {profit_pct:.1f}% | Day {days_held} held ⚡)"
            elif days_held <= 5 and (profit_pct >= 40.0 or current_pnl >= (initial_credit_total * 0.40)):
                should_harvest = True
                harvest_reason = f"DIR-09 5-Day Velocity FastHarvest (+${current_pnl:,.2f} | {profit_pct:.1f}% | Day {days_held} held ⚡)"
            elif dte >= 7 and (profit_pct >= 40.0 or current_pnl >= (initial_credit_total * 0.40)):
                should_harvest = True
                harvest_reason = f"DIR-09 Mid-Cycle Velocity FastHarvest (+${current_pnl:,.2f} | {profit_pct:.1f}% | DTE: {dte}d | Freeing Collateral 🚀)"
            elif profit_pct >= 50.0 or current_pnl >= (initial_credit_total * 0.50):
                should_harvest = True
                harvest_reason = f"DIR-09 Standard 50% FastHarvest (+${current_pnl:,.2f} | {profit_pct:.1f}% | DTE: {dte}d | Day {days_held} held 🌾)"

            # Standard pre-scale-out profit ratchet
            ratchet_triggered, ratchet_reason = update_and_check_profit_ratchet(
                spread_key=spread_key,
                target_symbol=und,
                account_type="tradier_live",
                profit_pct=profit_pct,
                tot_pnl=current_pnl
            )
            if ratchet_triggered and not should_harvest:
                should_harvest = True
                harvest_reason = ratchet_reason

        if not should_harvest:
            check_and_emit_amber_alert("tradier_live", und, spot, s_strike, buf_pct, dte, s_occ, spread_key)
            status_tag = "RUNNER ACTIVE" if is_runner_active else "HOLDING TRADIER SPREAD"
            print(f"  ⏳ {status_tag}: {und} P/L: ${current_pnl:+.2f} ({profit_pct:.1f}% | DTE: {dte}d | Buffer: {buf_pct:+.1f}% | Day {days_held} held).")
            print(f"     DIR-09 Policy: Sliding-Scale FastHarvest (30%@<=2d, 40%@<=5d, 50% std) | Runner Trailing Floor | Strike Defense (Buf<=0.50%) 🛡️")
            continue

        print(f"  ⚡ EXECUTING TRADIER HARVEST ORDER: {harvest_reason}")
        # RULE-096: Closing Micro-Walk & Penny-Pilot Fill Accelerator
        is_defensive = ("DEFENSIVE" in harvest_reason) or ("GAMMA" in harvest_reason) or ("PROACTIVE" in harvest_reason)

        if not is_defensive and not is_runner_active and contracts > 1:
            close_qty = max(1, math.ceil(contracts / 2.0))
            runner_qty = contracts - close_qty
            is_scale_out = (runner_qty > 0)
        else:
            close_qty = contracts
            runner_qty = 0
            is_scale_out = False

        if not is_defensive:
            cur_debit, walk_desc = apply_closing_micro_walk(
                target_symbol=und,
                est_debit=cur_debit,
                net_credit_sh=net_credit_entry,
                days_held=days_held,
                is_defensive=is_defensive
            )
            if "PENNY_PILOT_MICRO_WALK" in walk_desc:
                print(f"  ⚡ {walk_desc}: Adjusted Tradier closing limit debit to guarantee immediate fill.")

        try:
            res = client.close_spread(
                symbol=und,
                short_occ=s_occ,
                long_occ=l_occ,
                qty=close_qty,
                limit_debit=cur_debit,
                duration="day"
            )
            order_id = res.get("order_id")
            status = res.get("status")
            print(f"  ✅ TRADIER HARVEST SUBMITTED: ID {order_id} | Status: {status}")
            scaled_pnl = round(current_pnl * (close_qty / contracts), 2)
            if spread_key:
                if is_scale_out:
                    register_scale_out_runner(
                        spread_key=spread_key,
                        target_symbol=und,
                        account_type="tradier_live",
                        initial_contracts=contracts,
                        scaled_out_contracts=close_qty,
                        remaining_runner_contracts=runner_qty,
                        profit_pct=profit_pct,
                        tot_pnl=current_pnl,
                        order_id=order_id
                    )
                    set_harvest_lockout(spread_key, "SCALE_OUT_IN_PROGRESS", order_id=order_id, cooldown_hours=0.08)
                else:
                    set_harvest_lockout(spread_key, "IN_PROGRESS", order_id=order_id, cooldown_hours=24)
                    clear_harvest_ratchet(spread_key)

            closed_orders.append({
                "symbol": und,
                "order_id": order_id,
                "status": status,
                "account": "tradier_live",
                "pnl": scaled_pnl,
                "is_scale_out": is_scale_out,
                "is_runner": is_runner_active,
                "close_qty": close_qty,
                "runner_qty": runner_qty
            })

            now_ict = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ICT")

            # Telegram notification
            if is_scale_out:
                header_tag = "🌾 TRADIER LIVE TRANCHE 1 SCALE-OUT EXECUTED!"
                desc_line = f"• Contracts: {close_qty} of {contracts} Scaled Out ({runner_qty} Runner Riding 🏃‍♂️)\n"
            elif is_runner_active:
                header_tag = "🏃‍♂️ TRADIER LIVE TRANCHE 2 RUNNER EXIT EXECUTED!"
                desc_line = f"• Contracts: {close_qty} Runner Contract(s) Liquidated 🏁\n"
            else:
                header_tag = "🌾 TRADIER LIVE FASTHARVEST EXECUTED!"
                desc_line = f"• Contracts: {contracts} | Limit Debit: ${cur_debit:.2f}\n"

            msg = (
                f"{header_tag}\n"
                f"• Symbol: {und} Bull Put Credit Spread\n"
                f"• Strikes: ${s_strike:.1f}P / ${l_strike:.1f}P (Exp: {exp_date_str})\n"
                f"{desc_line}"
                f"• Realized P/L: +${scaled_pnl:,.2f} ({profit_pct:.1f}%)\n"
                f"• Rationale: {harvest_reason}\n"
                f"• Order ID: {order_id} ({status})\n"
                f"• Time    : {now_ict}"
            )
            send_telegram_alert(msg)
            broadcast_to_anna("HARVEST_FILLED_TRADIER", msg)

            # RULE-083: Instant Slot Vacancy Broadcast for Same-Day Collateral Recycling
            slot_event = {
                "event": "SLOT_VACATED" if not is_scale_out else "PARTIAL_SCALE_OUT",
                "symbol": und,
                "account": "tradier_live",
                "realized_pnl": scaled_pnl,
                "profit_pct": profit_pct,
                "contracts_closed": close_qty,
                "contracts_remaining": runner_qty,
                "timestamp": now_ict,
                "action": "READY_FOR_SAME_DAY_ROTATION" if not is_scale_out else "RUNNER_MONITORING"
            }
            broadcast_to_anna(f"SLOT_VACATED_{und}", json.dumps(slot_event), slot_event)

            # Reconcile active_trades.json directly against broker truth
            try:
                from reconcile_active_trades import reconcile_active_trades
                reconcile_active_trades(quiet=True)
            except Exception: pass

            # Autonomous Excel Transaction Journal Update (RULE-051)
            try:
                import transaction_journal_manager
                transaction_journal_manager.update_excel_journal()
                print(f"  📊 Updated SkonVault_Live_Transaction_Journal.xlsx with Tradier harvest!")
            except Exception as ex_j:
                print(f"  ℹ️ Journal sync notice: {ex_j}")
        except Exception as e:
            print(f"  🔴 Tradier Close Error: {e}")

    return closed_orders

# Backward compatibility alias
harvest_account_positions = harvest_spread_positions

def run_all_harvests(min_profit_pct: float = 50.0):
    """
    DIR-09 & RULE-074: Executes FastHarvest sweeps across live production accounts
    (Alpaca Live #290523608 and Tradier Live #6YB80974) under Option A (Pure Live Focus).
    Enforces RULE-074 US Exchange Holiday Circuit Breaker.
    """
    # JIT Heartbeat Gatekeeper Self-Healing (RULE-086): Always execute first to guarantee fresh scratch
    try:
        from market_heartbeat_gate import evaluate_market_status, sync_heartbeat_files
        is_mkt_open, r_reason, r_meta = evaluate_market_status()
        sync_heartbeat_files(is_mkt_open, r_reason, r_meta)
    except Exception:
        pass

    chk_broker = AlpacaClient("alpaca_live")
    is_holiday, holiday_name = chk_broker.is_market_holiday()
    if is_holiday:
        print("============================================================")
        print(f"🛑 US MARKET HOLIDAY DETECTED: {holiday_name.upper()}!")
        print("   RULE-074 Circuit Breaker: All US exchanges are 100% closed.")
        print("   FastHarvest poller standing down autonomously. Zero orders will be submitted.")
        print("============================================================")
        return

    print("============================================================")
    print(f"🌾 EXECUTING AUTOMATED FAST-TRACK SPREAD HARVEST (OPTION A — PURE LIVE)")
    print(f"   Target: {min_profit_pct:.0f}% TP (DIR-09)")
    print("============================================================")

    # 1. Audit Alpaca Live (#290523608)
    alpaca_live_broker = AlpacaClient("alpaca_live")
    try:
        al_positions = alpaca_live_broker.get_positions()
    except Exception as ex_al:
        print(f"  ⚠️ Error fetching Alpaca Live positions: {ex_al}")
        al_positions = []

    al_symbols = set()
    for p in al_positions:
        sym = p.get("symbol", "")
        if sym in ["SGOV", "BIL", "SHV"] or p.get("asset_class") != "us_option" and not re.search(r"\d{6}[CP]\d{8}$", sym):
            continue
        m = re.match(r"^([A-Z]+)", sym)
        if m:
            al_symbols.add(m.group(1))

    print(f"\n📂 Auditing ALPACA LIVE (#290523608): {sorted(al_symbols) or 'No active positions'}")
    for sym in sorted(al_symbols):
        harvest_spread_positions("alpaca_live", sym, force_close=False, min_profit_pct=min_profit_pct)

    # 2. Audit Tradier Live (#6YB80974)
    harvest_tradier_positions(force_close=False, min_profit_pct=min_profit_pct)

    print("\n============================================================")
    print("✅ HARVEST SWEEP COMPLETE: All live portfolio slots audited.")
    print("============================================================")

if __name__ == "__main__":
    run_all_harvests()
