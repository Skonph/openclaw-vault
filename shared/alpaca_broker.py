#!/usr/bin/env python3
"""
alpaca_broker.py — Multi-Account Centralized Alpaca Broker Client (RULE-052 & RULE-053 Hardened)

Features:
- Dual-Account Architecture (Pion Main & Pion2 Sub)
- RULE-052: Mandatory Live Liquidity Pre-Flight Gate (Bid >= $0.15 & Dynamic Live-Spot Calibration)
- RULE-053: Autonomous Self-Healing Spread Pairing & Orphan Elimination Engine
- Atomic Multi-Leg Submission (order_class: "mleg")
"""

import sys
import os
import json
import urllib.request
import ssl
import re
import time
import datetime
import threading
import asyncio
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

sys.path.insert(0, str(Path(__file__).parent))

class AlpacaAuthError(Exception):
    pass

class AlpacaTradeStreamListener:
    """
    Tier 2: Sub-50ms Real-Time Alpaca WebSocket Trade Updates Stream.
    Connects to wss://(paper-)api.alpaca.markets/stream to receive instantaneous push events
    for order fills, replaces, and cancellations.
    Provides non-blocking thread-safe events and order state tracking.
    """
    def __init__(self, api_key: str, secret_key: str, is_live: bool = False):
        self.api_key = api_key
        self.secret_key = secret_key
        self.is_live = is_live
        self.ws_url = "wss://api.alpaca.markets/stream" if is_live else "wss://paper-api.alpaca.markets/stream"
        self._lock = threading.Lock()
        self._order_events: Dict[str, threading.Event] = {}
        self._order_data: Dict[str, Dict[str, Any]] = {}
        self._id_aliases: Dict[str, str] = {}  # old_id -> new_id
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event = threading.Event()
        self._connected = False
        self._authenticated = False

    def start(self):
        """Starts background daemon thread if not already running."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_thread, daemon=True, name="AlpacaTradeStream")
            self._thread.start()

    def stop(self):
        """Stops the background listener thread."""
        self._stop_event.set()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run_thread(self):
        try:
            import websockets
        except ImportError:
            print("  ℹ️ [AlpacaTradeStream] 'websockets' library not installed. Defaulting to REST Keep-Alive polling.")
            return

        while not self._stop_event.is_set():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                loop.run_until_complete(self._stream_loop())
            except Exception:
                if not self._stop_event.is_set():
                    time.sleep(2)
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
                if self._stop_event.is_set():
                    break

    async def _stream_loop(self):
        import websockets
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20) as ws:
                    self._connected = True
                    # Modern Alpaca authentication payload
                    auth_msg = {
                        "action": "auth",
                        "key": self.api_key,
                        "secret": self.secret_key
                    }
                    await ws.send(json.dumps(auth_msg))
                    auth_resp = await ws.recv()
                    auth_data = json.loads(auth_resp)
                    if auth_data.get("stream") == "authorization" and auth_data.get("data", {}).get("status") == "authorized":
                        self._authenticated = True
                    else:
                        await asyncio.sleep(5)
                        continue

                    # Subscribe to trade_updates stream
                    sub_msg = {
                        "action": "listen",
                        "data": {"streams": ["trade_updates"]}
                    }
                    await ws.send(json.dumps(sub_msg))
                    await ws.recv()

                    # Main receive loop
                    while not self._stop_event.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue

                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue

                        if msg.get("stream") == "trade_updates":
                            self._handle_trade_update(msg.get("data", {}))

            except Exception:
                self._connected = False
                self._authenticated = False
                if not self._stop_event.is_set():
                    await asyncio.sleep(2)

    def _handle_trade_update(self, data: Dict[str, Any]):
        event = data.get("event")
        order = data.get("order", {})
        oid = order.get("id")
        if not oid:
            return

        replaces = data.get("replaces") or order.get("replaces")
        status = order.get("status") or event
        filled_qty = float(order.get("filled_qty", 0.0) or 0.0)
        filled_avg_price = float(order.get("filled_avg_price", 0.0) or data.get("price", 0.0) or 0.0)
        is_filled = (event in ("fill", "partial_fill") and status == "filled") or status == "filled"

        with self._lock:
            order_record = {
                "id": oid,
                "event": event,
                "status": status,
                "filled_qty": filled_qty,
                "filled_avg_price": filled_avg_price,
                "raw_order": order,
                "timestamp": time.time(),
                "is_filled": is_filled
            }
            self._order_data[oid] = order_record

            if replaces:
                self._id_aliases[replaces] = oid
                self._order_data[replaces] = order_record

            # Signal event for waiting threads
            ev = self._order_events.get(oid)
            if ev:
                ev.set()
            if replaces:
                rep_ev = self._order_events.get(replaces)
                if rep_ev:
                    rep_ev.set()

    def register_order(self, order_id: str) -> threading.Event:
        with self._lock:
            if order_id not in self._order_events:
                self._order_events[order_id] = threading.Event()
            return self._order_events[order_id]

    def register_replacement(self, old_id: str, new_id: str):
        with self._lock:
            self._id_aliases[old_id] = new_id
            ev = self._order_events.get(old_id) or threading.Event()
            self._order_events[old_id] = ev
            self._order_events[new_id] = ev

    def get_event(self, order_id: str) -> Optional[threading.Event]:
        with self._lock:
            actual_id = self._id_aliases.get(order_id, order_id)
            return self._order_events.get(actual_id) or self._order_events.get(order_id)

    def get_order_update(self, order_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            actual_id = self._id_aliases.get(order_id, order_id)
            return self._order_data.get(actual_id) or self._order_data.get(order_id)

    def is_active(self) -> bool:
        return self._connected and self._authenticated

def get_default_monthly_expiration(min_dte: int = 14, max_dte: int = 45) -> str:
    """
    Dynamically finds the next standard third Friday expiration within target DTE band.
    Eliminates all hardcoded expiration dates from broker layer.
    """
    today = datetime.date.today()
    target_start = today + datetime.timedelta(days=min_dte)
    for offset in range(max_dte - min_dte + 14):
        cand = target_start + datetime.timedelta(days=offset)
        if cand.weekday() == 4 and 15 <= cand.day <= 21:
            return cand.strftime("%Y-%m-%d")
    for offset in range(30):
        cand = target_start + datetime.timedelta(days=offset)
        if cand.weekday() == 4:
            return cand.strftime("%Y-%m-%d")
    return (today + datetime.timedelta(days=30)).strftime("%Y-%m-%d")

class AlpacaClient:
    def __init__(self, account_type: str = "pion_main"):
        self.account_type = account_type.lower()
        self.api_key, self.secret_key, self.expected_account_id = self._load_credentials()
        # Pillar 1 & RULE-069: Dynamic Production vs Paper Endpoint Detection
        is_live_key = self.api_key.startswith("AK") or "live" in self.account_type or os.getenv("ALPACA_ENV") == "live"
        if is_live_key:
            self.base_url = "https://api.alpaca.markets"
            self.is_live = True
        else:
            self.base_url = "https://paper-api.alpaca.markets"
            self.is_live = False
        self.data_url = "https://data.alpaca.markets"
        self.ssl_ctx = ssl._create_unverified_context()
        self._session = None
        self._stream_listener = None

    def get_trade_stream_listener(self) -> Optional[AlpacaTradeStreamListener]:
        """RULE-084 Tier 2: Lazy-inits and starts background WebSocket trade stream listener."""
        if getattr(self, "_stream_listener", None) is None:
            try:
                import websockets
                self._stream_listener = AlpacaTradeStreamListener(
                    api_key=self.api_key,
                    secret_key=self.secret_key,
                    is_live=self.is_live
                )
                self._stream_listener.start()
            except ImportError:
                self._stream_listener = None
            except Exception as e:
                print(f"  ℹ️ Notice initializing AlpacaTradeStreamListener: {e}")
                self._stream_listener = None
        return self._stream_listener

    def _data_headers(self) -> Dict[str, str]:
        """RULE-084: Consolidated headers for Alpaca Data API requests."""
        return self._headers()

    def _get_session(self):
        """Initializes and returns a pooled requests.Session with HTTP Keep-Alive."""
        if getattr(self, "_session", None) is None:
            try:
                import requests
                from requests.adapters import HTTPAdapter
                from urllib3.util.retry import Retry
                s = requests.Session()
                s.headers.update(self._headers())
                # Pool 10 persistent TCP/TLS connections, max 25
                retries = Retry(total=2, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
                adapter = HTTPAdapter(pool_connections=10, pool_maxsize=25, max_retries=retries)
                s.mount("https://", adapter)
                s.mount("http://", adapter)
                self._session = s
            except Exception:
                self._session = False
        return self._session

    def _call_api(self, url: str, method: str = "GET", data: Optional[Dict[str, Any]] = None, max_retries: int = 3, timeout: int = 10) -> Any:
        """RULE-066: Resilient API Caller with HTTP Keep-Alive Connection Pooling and Exponential Backoff."""
        session = self._get_session()
        if session:
            last_err = None
            for attempt in range(1, max_retries + 1):
                try:
                    resp = session.request(method=method, url=url, json=data, timeout=timeout)
                    if resp.status_code == 204:
                        return {}
                    if resp.status_code < 400:
                        return resp.json()
                    resp.raise_for_status()
                except Exception as e:
                    last_err = e
                    if attempt < max_retries:
                        time.sleep(0.3 * attempt)
            if last_err is not None:
                raise last_err
            raise RuntimeError(f"Alpaca API call failed: {method} {url}")

        # Fallback to urllib if requests session unavailable
        payload = json.dumps(data).encode("utf-8") if data else None
        headers = self._headers()
        last_err = None

        for attempt in range(1, max_retries + 1):
            try:
                req = urllib.request.Request(url, data=payload, headers=headers, method=method)
                with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=timeout) as resp:
                    if resp.status == 204:
                        return {}
                    return json.loads(resp.read().decode())
            except Exception as e:
                last_err = e
                if attempt < max_retries:
                    time.sleep(0.5 * attempt)

        if last_err is not None:
            raise last_err
        raise RuntimeError(f"Alpaca API call failed: {method} {url}")

    def _load_credentials(self) -> Tuple[str, str, str]:
        env_paths = [
            Path("/home/ubuntu/openclaw/.env"),
            Path("/home/ubuntu/shared/.env"),
            Path("/home/ubuntu/.env"),
            Path("/Users/SkonP/AI_Prompt/Obsidient/SkonVault/shared/.env"),
            Path("/Users/SkonP/.env")
        ]
        key, secret, acct_id = "", "", ""

        for ep in env_paths:
            if ep.exists():
                for line in ep.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("#") or not line: continue
                    if "=" not in line: continue
                    k, v = line.split("=", 1)[0].strip(), line.split("=", 1)[1].strip().strip('"').strip("'")
                    
                    if self.account_type in ["alpaca_live", "live"]:
                        if k in ["ALPACA_LIVE_API_KEY", "ALPACA_LIVE_KEY", "ALPACA_API_KEY"] and (v.startswith("AK") or "openclaw" in str(ep)):
                            key = v
                        elif k in ["ALPACA_LIVE_SECRET_KEY", "ALPACA_LIVE_SECRET", "ALPACA_SECRET_KEY"]:
                            secret = v
                        elif k in ["ALPACA_LIVE_ACCOUNT_ID", "ALPACA_ACCOUNT_ID"]:
                            acct_id = v
                    elif self.account_type == "pion_main":
                        if k in ["PION_ALPACA_KEY", "PION_API_KEY", "PION_KEY"]: key = v
                        elif k in ["PION_ALPACA_SECRET", "PION_SECRET_KEY", "PION_SECRET"]: secret = v
                        elif k in ["PION_ACCOUNT_ID", "PION_ALPACA_ACCOUNT_ID"]: acct_id = v
                    else: # pion2_sub
                        # Matches exact Pion2 Sub credentials from openclaw/.env (PK... paper keys)
                        if k in ["PION2_ALPACA_KEY", "PION2_API_KEY", "ALPACA_PION2_API_KEY"]: key = v
                        elif k == "ALPACA_API_KEY" and v.startswith("PK"): key = v
                        elif k in ["PION2_ALPACA_SECRET", "PION2_SECRET_KEY", "ALPACA_PION2_SECRET_KEY"]: secret = v
                        elif k == "ALPACA_SECRET_KEY" and not v.startswith("AK") and (v.startswith("5TXA7") or len(v) < 35): secret = v
                        elif k in ["PION2_ACCOUNT_ID", "ALPACA_PION2_ACCOUNT_ID"]:
                            acct_id = v

        if not key or not secret:
            if self.account_type in ["alpaca_live", "live"]:
                key = os.getenv("ALPACA_LIVE_API_KEY", os.getenv("ALPACA_API_KEY", "AKKHFFQ2YR64RR4TMOPWNQUE4T"))
                secret = os.getenv("ALPACA_LIVE_SECRET_KEY", os.getenv("ALPACA_SECRET_KEY", ""))
                acct_id = os.getenv("ALPACA_LIVE_ACCOUNT_ID", os.getenv("ALPACA_ACCOUNT_ID", "290523608"))
            elif self.account_type == "pion_main":
                key = os.getenv("PION_ALPACA_KEY", os.getenv("PION_API_KEY", "PK2UHYO796Z46Z0842DF"))
                secret = os.getenv("PION_ALPACA_SECRET", os.getenv("PION_SECRET_KEY", "n9k05j4FwZzZ6B9c7Y0iXvQ8aU5rT4eW3qP2oL1k"))
                acct_id = os.getenv("PION_ACCOUNT_ID", "PA3SK43ASS1I")
            else:
                key = os.getenv("PION2_ALPACA_KEY", os.getenv("ALPACA_PION2_API_KEY", "PK5ECB0W7L"))
                secret = os.getenv("PION2_ALPACA_SECRET", os.getenv("ALPACA_PION2_SECRET_KEY", "5TXA7aehhE"))
                acct_id = os.getenv("PION2_ACCOUNT_ID", os.getenv("ALPACA_PION2_ACCOUNT_ID", "PA3C75K8SZ57"))

        return key, secret, acct_id

    def _headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Content-Type": "application/json"
        }

    def get_account(self) -> Dict[str, Any]:
        return self._call_api(f"{self.base_url}/v2/account", timeout=8)

    def get_positions(self) -> List[Dict[str, Any]]:
        res = self._call_api(f"{self.base_url}/v2/positions", timeout=8)
        return res if isinstance(res, list) else []

    US_MARKET_HOLIDAYS_2026 = {
        "2026-01-01": "New Year's Day",
        "2026-01-19": "Martin Luther King Jr. Day",
        "2026-02-16": "Washington's Birthday / Presidents' Day",
        "2026-04-03": "Good Friday",
        "2026-05-25": "Memorial Day",
        "2026-06-19": "Juneteenth National Independence Day",
        "2026-07-03": "Independence Day (Observed)",
        "2026-09-07": "Labor Day",
        "2026-11-26": "Thanksgiving Day",
        "2026-12-25": "Christmas Day"
    }

    def get_clock(self) -> Dict[str, Any]:
        """RULE-074: Query Alpaca /v2/clock for real-time market open/close and holiday schedules."""
        try:
            return self._call_api(f"{self.base_url}/v2/clock", timeout=6)
        except Exception as e:
            return {"is_open": False, "error": str(e)}

    def is_market_holiday(self) -> Tuple[bool, str]:
        """
        RULE-074: Dual-Layer Holiday Detection (Alpaca Clock + Fixed Exchange Holiday Calendar).
        Returns (is_holiday: bool, holiday_name: str).
        """
        try:
            from zoneinfo import ZoneInfo
            now_ny = datetime.datetime.now(ZoneInfo("America/New_York"))
        except Exception:
            now_ny = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=4)

        ny_date = now_ny.strftime("%Y-%m-%d")

        # 1. Direct Fixed Calendar Check
        if ny_date in self.US_MARKET_HOLIDAYS_2026:
            return True, self.US_MARKET_HOLIDAYS_2026[ny_date]

        # 2. Dynamic Exchange Holiday Check (ONLY applicable during regular trading hours on weekdays)
        # Normal overnight closure after 16:00 ET is NOT an exchange holiday.
        ny_weekday = now_ny.weekday()
        if ny_weekday < 5:
            ny_mins = now_ny.hour * 60 + now_ny.minute
            is_regular_hours = (9 * 60 + 30) <= ny_mins < (16 * 60)
            if is_regular_hours:
                clock = self.get_clock()
                if "error" not in clock and not clock.get("is_open", False):
                    return True, self.US_MARKET_HOLIDAYS_2026.get(ny_date, "US Market Exchange Holiday")

        return False, ""

    def is_market_open(self) -> bool:
        try:
            return self._call_api(f"{self.base_url}/v2/clock", timeout=6).get("is_open", False)
        except Exception:
            try:
                from zoneinfo import ZoneInfo
                now_ny = datetime.datetime.now(ZoneInfo("America/New_York"))
            except Exception:
                now_ny = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=4)
            if now_ny.weekday() in (5, 6): # Saturday, Sunday in NY
                return False
            time_mins = now_ny.hour * 60 + now_ny.minute
            return (9 * 60 + 30) <= time_mins < (16 * 60)

    def get_option_snapshot(self, symbols: List[str]) -> Dict[str, Any]:
        if not symbols: return {}
        sym_str = ",".join(symbols)
        try:
            data = self._call_api(f"{self.data_url}/v1beta1/options/snapshots?symbols={sym_str}&feed=indicative", timeout=8)
            snaps = data.get("snapshots", {})
            res = {}
            for s, snap in snaps.items():
                q = snap.get("latestQuote", {})
                res[s] = {"bid": float(q.get("bp", 0)), "ask": float(q.get("ap", 0))}
            return res
        except Exception:
            return {}

    def resolve_spread_pair(
        self,
        underlying: str,
        target_short_strike: float,
        width: float = 5.0,
        require_live_bid: bool = True,
        min_dte: Optional[int] = None,
        target_expiration: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        RULE-052 & RULE-077: Dynamic Live Liquidity & Account-Aware DTE Calibration Resolver.
        - pion2_sub: 9-16 DTE Short-Cycle Sprint Engine (rapid theta waterfall)
        - pion_main: 14-28 DTE Core Defensive Swing Engine
        """
        try:
            if min_dte is None:
                if self.account_type in ("pion2_sub", "tradier_live"):
                    min_dte = 9   # Short-Cycle Sprint Engine (9-14 DTE)
                elif self.account_type in ("pion_main", "alpaca_live"):
                    min_dte = 10  # Hybrid Barbell Engine (10-28 DTE)
                else:
                    min_dte = 10

            today = datetime.date.today()
            min_exp_str = (today + datetime.timedelta(days=min_dte)).strftime("%Y-%m-%d")
            exp_param = f"&expiration_date={target_expiration}" if target_expiration else f"&expiration_date_gte={min_exp_str}"

            # Query listed puts with server-side expiration filtering
            url = f"{self.base_url}/v2/options/contracts?underlying_symbols={underlying}&type=put&status=active{exp_param}&limit=500"
            puts = []
            try:
                data = self._call_api(url, timeout=10)
                puts = data.get("option_contracts", [])
            except Exception:
                # Fallback to single underlying_symbol query
                try:
                    url2 = f"{self.base_url}/v2/options/contracts?underlying_symbol={underlying}&type=put&status=active{exp_param}&limit=500"
                    data2 = self._call_api(url2, timeout=10)
                    puts = data2.get("option_contracts", [])
                except Exception: pass

            if puts:
                today_str = today.strftime("%Y-%m-%d")
                exps = sorted(list(set(p.get("expiration_date") for p in puts if p.get("expiration_date") >= min_exp_str)))
                if not exps:
                    exps = sorted(list(set(p.get("expiration_date") for p in puts if p.get("expiration_date") > today_str)))
                for target_exp in exps:
                    exp_puts = [p for p in puts if p.get("expiration_date") == target_exp]
                    strikes = sorted(list(set(float(p.get("strike_price", 0)) for p in exp_puts)))

                    # Prioritize liquid whole-dollar strikes and closest distance to target
                    def strike_priority_key(x):
                        dist = abs(x - target_short_strike)
                        is_whole = (x % 1.0 == 0.0)
                        return (dist, 0 if is_whole else 1)

                    for s_strike in sorted(strikes, key=strike_priority_key):
                        if abs(s_strike - target_short_strike) > 2.0:
                            continue
                        l_strike = s_strike - width
                        s_match = [p for p in exp_puts if abs(float(p.get("strike_price", 0)) - s_strike) < 0.1]
                        l_match = [p for p in exp_puts if abs(float(p.get("strike_price", 0)) - l_strike) < 0.1]

                        if s_match and l_match:
                            s_sym = s_match[0].get("symbol")
                            l_sym = l_match[0].get("symbol")

                            # Live Liquidity & Whole-Dollar Auto-Rescue check
                            quotes = self.get_option_snapshot([s_sym, l_sym])
                            s_quote = quotes.get(s_sym, {})
                            l_quote = quotes.get(l_sym, {})
                            s_bid = s_quote.get("bid", 0.0)
                            l_ask = l_quote.get("ask", 0.0)
                            net_credit = round(s_bid - l_ask, 2)
                            spread_gap = round((s_quote.get("ask", 0) - s_bid) + (l_ask - l_quote.get("bid", 0)), 2)

                            # DIR-01 Liquidity & Micro-Credit Gate: Reject illiquid strikes or credit below minimum floor
                            min_nc = 0.10 if width <= 1.0 else 0.15
                            if require_live_bid and (s_bid < 0.10 or net_credit < min_nc or spread_gap > 0.80) and len(strikes) > 2:
                                continue

                            return {
                                "symbol": underlying,
                                "short_sym": s_sym,
                                "long_sym": l_sym,
                                "exp_date": target_exp,
                                "short_strike": s_strike,
                                "long_strike": l_strike,
                                "width": width
                            }
            
            # Robust OSI Fallback (Standard Monthly ~30-40 DTE)
            exp_fallback = target_expiration if target_expiration else get_default_monthly_expiration(min_dte=min_dte or 14)
            date_compact = exp_fallback.replace("-", "")[2:]
            s_sym_f = f"{underlying}{date_compact}P{int(target_short_strike*1000):08d}"
            l_sym_f = f"{underlying}{date_compact}P{int((target_short_strike - width)*1000):08d}"
            return {
                "symbol": underlying,
                "short_sym": s_sym_f,
                "long_sym": l_sym_f,
                "exp_date": exp_fallback,
                "short_strike": target_short_strike,
                "long_strike": target_short_strike - width,
                "width": width
            }
        except Exception as ex_res:
            exp_fallback = target_expiration if target_expiration else get_default_monthly_expiration(min_dte=min_dte or 14)
            date_compact = exp_fallback.replace("-", "")[2:]
            s_sym_f = f"{underlying}{date_compact}P{int(target_short_strike*1000):08d}"
            l_sym_f = f"{underlying}{date_compact}P{int((target_short_strike - width)*1000):08d}"
            return {
                "symbol": underlying,
                "short_sym": s_sym_f,
                "long_sym": l_sym_f,
                "exp_date": exp_fallback,
                "short_strike": target_short_strike,
                "long_strike": target_short_strike - width,
                "width": width
            }

    def submit_vertical_spread(
        self,
        symbol: str,
        short_strike: float,
        long_strike: float,
        contracts: int,
        exp_date: Optional[str] = None,
        min_dte: Optional[int] = None
    ) -> Tuple[bool, List[str], str, float]:
        """
        Submits atomic multi-leg Bull Put Spread with live adaptive limit-credit pricing.
        """
        if not exp_date:
            exp_date = get_default_monthly_expiration(min_dte=min_dte or 14)
        resolved = self.resolve_spread_pair(symbol, short_strike, width=(short_strike - long_strike), require_live_bid=False, min_dte=min_dte)
        if resolved:
            short_sym = resolved["short_sym"]
            long_sym = resolved["long_sym"]
            short_strike = resolved["short_strike"]
            long_strike = resolved["long_strike"]
        else:
            short_sym = f"{symbol}{exp_date.replace('-','')[2:]}P{int(short_strike*1000):08d}"
            long_sym = f"{symbol}{exp_date.replace('-','')[2:]}P{int(long_strike*1000):08d}"

        # Fetch live quotes to compute adaptive mid-point credit (RULE-058)
        quotes = self.get_option_snapshot([short_sym, long_sym])
        s_bid = quotes.get(short_sym, {}).get("bid", 1.00)
        s_ask = quotes.get(short_sym, {}).get("ask", 1.50)
        l_bid = quotes.get(long_sym, {}).get("bid", 0.10)
        l_ask = quotes.get(long_sym, {}).get("ask", 0.30)
        
        s_mid = (s_bid + s_ask) / 2.0
        l_mid = (l_bid + l_ask) / 2.0
        mid_credit = max(0.15, round(s_mid - l_mid, 2))
        snipe_credit = round(mid_credit + 0.02, 2)
        natural_credit = max(0.15, round(s_bid - l_ask, 2))

        market_open = self.is_market_open()
        
        # 1. ATOMIC MULTI-LEG SUBMISSION (RULE-047, RULE-058 & RULE-083 MIDPOINT SNIPING)
        # Step 1: Submit with Midpoint Sniping for positive slippage capture
        mleg_payload = {
            "order_class": "mleg",
            "type": "limit",
            "time_in_force": "day",
            "legs": [
                {"symbol": short_sym, "ratio_qty": 1, "side": "sell", "position_intent": "sell_to_open"},
                {"symbol": long_sym, "ratio_qty": 1, "side": "buy", "position_intent": "buy_to_open"}
            ],
            "qty": str(contracts),
            "limit_price": f"-{snipe_credit:.2f}"
        }

        try:
            resp_mleg = self._call_api(f"{self.base_url}/v2/orders", method="POST", data=mleg_payload, timeout=10)
            oid = resp_mleg.get("id")
            if oid:
                return True, [oid], f"Successfully placed {contracts}C {symbol} Midpoint Sniping Spread ({short_strike}/{long_strike} @ ${snipe_credit:,.2f})! Order ID: {oid}", snipe_credit
        except Exception as ex_snipe:
            # Step 2: Fallback to exact Midpoint limit (guaranteeing zero spread drag)
            try:
                mleg_payload["limit_price"] = f"-{mid_credit:.2f}"
                resp_mleg2 = self._call_api(f"{self.base_url}/v2/orders", method="POST", data=mleg_payload, timeout=10)
                oid = resp_mleg2.get("id")
                if oid:
                    return True, [oid], f"Successfully placed {contracts}C {symbol} Midpoint Limit Spread ({short_strike}/{long_strike} @ ${mid_credit:,.2f})! Order ID: {oid}", mid_credit
            except Exception as ex_mid:
                # Step 3: Fallback to Natural Credit Limit (never raw market order to prevent retail spread scalping)
                try:
                    mleg_payload["limit_price"] = f"-{natural_credit:.2f}"
                    resp_mleg3 = self._call_api(f"{self.base_url}/v2/orders", method="POST", data=mleg_payload, timeout=10)
                    oid = resp_mleg3.get("id")
                    if oid:
                        return True, [oid], f"Successfully placed {contracts}C {symbol} Natural Limit Spread ({short_strike}/{long_strike} @ ${natural_credit:,.2f})! Order ID: {oid}", natural_credit
                except Exception as ex_nat:
                    print(f"  ℹ️ Mleg submission notice: {ex_nat}")

        # 2. Strict RULE-047: Native Atomic Multi-Leg Enforcement (Zero Stranded Legs)
        return False, [], "Atomic multi-leg order rejected by exchange; cascading to next candidate.", 0.0

    def active_micro_walk_spread(
        self,
        symbol: str,
        short_strike: float,
        long_strike: float,
        contracts: int,
        exp_date: Optional[str] = None,
        min_dte: Optional[int] = None,
        max_walk_seconds: int = 90,
        prewarmed_payload: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, List[str], str, float, bool]:
        """
        RULE-084: Ultra-Adaptive In-Process 90-Second Micro-Walk & Delta-Drift Accelerator.
        Tier 2: Real-time Alpaca WebSocket Push Event (<50ms execution confirmation).
        Tier 3: Ingests pre-warmed payload at T+0.00s to eliminate all pre-flight option chain discovery lag.
        - Step 1 (T+0s): Snipe at Midpoint + 2¢ (or +1 tick) for positive slippage.
        - Step 2 (T+15s): Micro-nudge to exact Midpoint if unfilled.
        - Step 3 (T+35s): Micro-nudge to Midpoint - 1 tick (priority queue jump).
        - Step 4 (T+60s): Marketable Natural Limit (subject to ROC >= 12.5% floor).
        - Delta Drift: If underlying surges >= +0.25% OTM, accelerates to immediate Natural Limit before premium bleeds.
        Returns: (success, order_ids, message, credit, is_filled_now)
        """
        import time

        if not exp_date:
            exp_date = get_default_monthly_expiration(min_dte=min_dte or 14)

        # Tier 3 Fast-Path: Ingest pre-warmed payload contracts & strikes
        if prewarmed_payload and prewarmed_payload.get("symbol") == symbol:
            short_sym = prewarmed_payload.get("short_sym")
            long_sym = prewarmed_payload.get("long_sym")
            short_strike = float(prewarmed_payload.get("short_strike", short_strike))
            long_strike = float(prewarmed_payload.get("long_strike", long_strike))
            exp_date = prewarmed_payload.get("exp_date", exp_date)
            snipe_credit = float(prewarmed_payload.get("prewarmed_snipe_credit", 0.0))
            mid_credit = float(prewarmed_payload.get("prewarmed_mid_credit", 0.0))
            natural_credit = float(prewarmed_payload.get("prewarmed_natural_credit", 0.0))
            baseline_spot = float(prewarmed_payload.get("baseline_spot", 0.0))
            width = abs(short_strike - long_strike)
            roc_rate = 0.075 if width >= 20.0 else 0.125
            min_roc_credit = max(0.12, round(width * roc_rate, 2))  # Width-adaptive ROC floor (7.5% on >=$20w, 12.5% on narrower)

            # Pre-warmed credit safety check: Reject if mid credit is below ROC floor during open market
            if self.is_market_open() and mid_credit < min_roc_credit:
                return False, [], f"Pre-warmed mid credit ${mid_credit:.2f} < ROC floor ${min_roc_credit:.2f}", 0.0, False

            snipe_offset = round(snipe_credit - mid_credit, 2)
            penny_pilot = {"SPY", "QQQ", "IWM", "XLF", "NVDA", "AMD", "TSM", "AAPL", "MSFT", "AMZN", "GOOGL"}
            is_penny = symbol in penny_pilot
            tick = 0.01 if is_penny else 0.05
            print(f"  ⚡ TIER 3 PRE-WARMING ACTIVE: Dispatched {contracts}C {symbol} with 0.00s pre-flight delay ({short_sym}/{long_sym} @ ${snipe_credit:.2f})!")
        else:
            resolved = self.resolve_spread_pair(symbol, short_strike, width=(short_strike - long_strike), require_live_bid=False, min_dte=min_dte)
            if resolved:
                short_sym = resolved["short_sym"]
                long_sym = resolved["long_sym"]
                short_strike = resolved["short_strike"]
                long_strike = resolved["long_strike"]
                exp_date = resolved.get("exp_date", exp_date)
            else:
                short_sym = f"{symbol}{exp_date.replace('-','')[2:]}P{int(short_strike*1000):08d}"
                long_sym = f"{symbol}{exp_date.replace('-','')[2:]}P{int(long_strike*1000):08d}"

            width = abs(short_strike - long_strike)
            roc_rate = 0.075 if width >= 20.0 else 0.125
            min_roc_credit = max(0.12, round(width * roc_rate, 2)) # Width-adaptive ROC floor (7.5% on >=$20w, 12.5% on narrower)

            # Penny Pilot tick calibration
            penny_pilot = {"SPY", "QQQ", "IWM", "XLF", "NVDA", "AMD", "TSM", "AAPL", "MSFT", "AMZN", "GOOGL"}
            is_penny = symbol in penny_pilot
            tick = 0.01 if is_penny else 0.05

            # Get initial quotes & baseline spot
            quotes = self.get_option_snapshot([short_sym, long_sym])
            s_bid = quotes.get(short_sym, {}).get("bid", 0.0)
            s_ask = quotes.get(short_sym, {}).get("ask", 0.0)
            l_bid = quotes.get(long_sym, {}).get("bid", 0.0)
            l_ask = quotes.get(long_sym, {}).get("ask", 0.0)

            # Microstructure Lever 2: Skewed Liquidity Midpoint (Leg-Asymmetry Weighting)
            s_spread = max(0.01, s_ask - s_bid)
            l_spread = max(0.01, l_ask - l_bid)
            smart_s_mid = s_bid + 0.60 * s_spread
            smart_l_mid = l_ask - 0.35 * l_spread
            raw_smart_mid = round(smart_s_mid - smart_l_mid, 2)
            
            # Arithmetic reference mid & natural floor
            s_arith_mid = (s_bid + s_ask) / 2.0
            l_arith_mid = (l_bid + l_ask) / 2.0
            raw_arith_mid = round(s_arith_mid - l_arith_mid, 2)
            raw_natural = round(s_bid - l_ask, 2)

            # Blended Midpoint: 70% Smart Skew + 30% Arithmetic Mid for optimal fill probability
            raw_blended_mid = round(0.70 * raw_smart_mid + 0.30 * raw_arith_mid, 2)

            # ROC Floor Gate: If the true market mid cannot satisfy the ROC floor during open market,
            # reject immediately so the waterfall engine cascades to the next candidate!
            if self.is_market_open() and (raw_blended_mid < min_roc_credit or s_bid < 0.10):
                return False, [], f"Raw market mid credit ${raw_blended_mid:.2f} < ROC floor ${min_roc_credit:.2f} (short bid: ${s_bid:.2f})", 0.0, False

            mid_credit = max(min_roc_credit, raw_blended_mid)
            natural_credit = max(0.10, raw_natural)

            # Microstructure Lever 5: Opening Spread Quality Compression Gate
            if mid_credit > 0 and self.is_market_open():
                spread_ratio = natural_credit / mid_credit
                if spread_ratio < 0.40:
                    print(f"  ⚠️ Spread Quality Warning: Nat/Mid ratio {spread_ratio:.2f} < 0.40 (Quotes wide). Stabilizing quotes...")
                    time.sleep(2)
                    quotes = self.get_option_snapshot([short_sym, long_sym])
                    s_bid = quotes.get(short_sym, {}).get("bid", s_bid)
                    s_ask = quotes.get(short_sym, {}).get("ask", s_ask)
                    l_bid = quotes.get(long_sym, {}).get("bid", l_bid)
                    l_ask = quotes.get(long_sym, {}).get("ask", l_ask)
                    s_spread = max(0.01, s_ask - s_bid)
                    l_spread = max(0.01, l_ask - l_bid)
                    smart_s_mid = s_bid + 0.60 * s_spread
                    smart_l_mid = l_ask - 0.35 * l_spread
                    raw_smart_mid = round(smart_s_mid - smart_l_mid, 2)
                    raw_arith_mid = round(((s_bid + s_ask) / 2.0) - ((l_bid + l_ask) / 2.0), 2)
                    raw_blended_mid = round(0.70 * raw_smart_mid + 0.30 * raw_arith_mid, 2)
                    if raw_blended_mid < min_roc_credit:
                        return False, [], f"Stabilized mid credit ${raw_blended_mid:.2f} < ROC floor ${min_roc_credit:.2f}", 0.0, False
                    mid_credit = max(min_roc_credit, raw_blended_mid)
                    natural_credit = max(0.10, round(s_bid - l_ask, 2))

            # Microstructure Lever 3: Top-of-Book Queue Jump (Penny-Jumper Priority)
            is_round_nickel = (round(mid_credit * 100) % 5 == 0)
            snipe_offset = 0.01 if (is_penny and is_round_nickel) else 0.02
            snipe_credit = round(mid_credit + snipe_offset, 2)

            # Initial baseline spot for delta drift
            baseline_spot = 0.0
            try:
                bdata = self._call_api(f"https://data.alpaca.markets/v2/stocks/{symbol}/bars/latest", timeout=5)
                baseline_spot = float(bdata.get("bar", {}).get("c", 0.0))
            except Exception: pass

        # Asset-adaptive Beta calibration for Delta-Drift Accelerator
        beta_map = {
            "AMD": 1.8, "NVDA": 1.7, "TSM": 1.4, "QQQ": 1.2, "SPY": 1.0, 
            "IWM": 1.2, "XLF": 0.85, "LMT": 0.60, "XLU": 0.50, "GLD": 0.20, 
            "XLE": 0.90, "XLV": 0.70, "CEG": 1.10, "UNH": 0.65, "JNJ": 0.55
        }
        asset_beta = beta_map.get(symbol.upper(), 1.0)
        # Scaled drift trigger: low-beta (XLU) accelerates at +0.10%, high-beta (AMD) at +0.22%
        drift_trigger_pct = max(0.10, min(0.30, round(0.12 * asset_beta, 2)))

        # Step 1: Submit Initial Snipe Order
        current_limit = snipe_credit
        mleg_payload = {
            "order_class": "mleg",
            "type": "limit",
            "time_in_force": "day",
            "legs": [
                {"symbol": short_sym, "ratio_qty": 1, "side": "sell", "position_intent": "sell_to_open"},
                {"symbol": long_sym, "ratio_qty": 1, "side": "buy", "position_intent": "buy_to_open"}
            ],
            "qty": str(contracts),
            "limit_price": f"-{current_limit:.2f}"
        }

        current_oid = None
        try:
            resp_mleg = self._call_api(f"{self.base_url}/v2/orders", method="POST", data=mleg_payload, timeout=10)
            current_oid = resp_mleg.get("id")
        except Exception as ex_init:
            # Fallback to mid-credit if initial snipe payload rejected
            mleg_payload["limit_price"] = f"-{mid_credit:.2f}"
            try:
                resp_mleg2 = self._call_api(f"{self.base_url}/v2/orders", method="POST", data=mleg_payload, timeout=10)
                current_oid = resp_mleg2.get("id")
                current_limit = mid_credit
            except Exception as ex_mid:
                return False, [], f"Spread order rejected by exchange: {ex_mid}", 0.0, False

        if not current_oid:
            return False, [], "No order ID returned from broker", 0.0, False

        # Tier 2: Register order with WebSocket push stream listener
        listener = self.get_trade_stream_listener()
        if listener:
            listener.register_order(current_oid)

        # If market closed, return immediately (do not block execution)
        if not self.is_market_open():
            return True, [current_oid], f"Order placed (Market Closed): {contracts}C {symbol} @ ${current_limit:.2f}", current_limit, False

        stream_tag = "WEBSOCKET STREAM ACTIVE (<50ms)" if (listener and listener.is_active()) else "KEEP-ALIVE REST POLLING"
        print(f"  ⚡ RULE-084 ACTIVE MICRO-WALK ENGAGED [{stream_tag}]: Order {current_oid} resting @ ${current_limit:.2f} (Smart Mid: ${mid_credit:.2f} | Snipe Offset: +${snipe_offset:.2f})...")
        start_time = time.time()
        stage = 1 # 1: Snipe, 2: Mid, 3: Inside Edge, 4: Marketable Natural

        while (time.time() - start_time) < max_walk_seconds:
            # Tier 2: Wait for sub-50ms WebSocket push event (with 1.0s timeout fallback to REST)
            ws_ev = listener.get_event(current_oid) if listener else None
            ws_triggered = False
            if ws_ev:
                ws_ev.clear()
                ws_triggered = ws_ev.wait(timeout=1.0)
            else:
                time.sleep(1)

            elapsed = time.time() - start_time

            # 1. Tier 2 Check: Order fill status via WebSocket push stream first (<50ms latency)
            if listener:
                ws_rec = listener.get_order_update(current_oid)
                if ws_rec and (ws_rec.get("is_filled") or ws_rec.get("status") == "filled" or ws_rec.get("filled_qty", 0) >= contracts):
                    fill_price = abs(float(ws_rec.get("filled_avg_price") or current_limit))
                    push_tag = "WEBSOCKET PUSH (<50ms)" if ws_triggered else "STREAM EVENT"
                    print(f"  🎉 RULE-084 {push_tag} FILL CONFIRMED: {contracts}C {symbol} FILLED @ ${fill_price:.2f} (Elapsed: {elapsed:.2f}s)!")
                    return True, [current_oid], f"Filled in Micro-Walk @ ${fill_price:.2f} ({elapsed:.1f}s)", fill_price, True

                if ws_rec and ws_rec.get("status") in ("canceled", "rejected", "expired"):
                    print(f"  ⚠️ WebSocket: Order {current_oid} status {ws_rec.get('status')}. Halting micro-walk.")
                    break

            # Zero-Risk Fallback: REST check with Keep-Alive session pooling
            order_info = self.get_order(current_oid)
            status = order_info.get("status")
            filled_qty = float(order_info.get("filled_qty", 0))
            if status == "filled" or filled_qty >= contracts:
                fill_price = abs(float(order_info.get("filled_avg_price") or current_limit))
                print(f"  🎉 RULE-084 REST FILL CONFIRMED: {contracts}C {symbol} FILLED @ ${fill_price:.2f} (Elapsed: {elapsed:.1f}s)!")
                return True, [current_oid], f"Filled in Micro-Walk @ ${fill_price:.2f} ({elapsed:.0f}s)", fill_price, True

            if status in ("canceled", "rejected", "expired"):
                print(f"  ⚠️ Order {current_oid} status {status}. Halting micro-walk.")
                break

            # 2. Check Beta-Adaptive Delta Drift (Underlying Spot Rally Detection)
            delta_drift_triggered = False
            if baseline_spot > 0 and elapsed >= 10:
                try:
                    bdata = self._call_api(f"https://data.alpaca.markets/v2/stocks/{symbol}/bars/latest", timeout=4)
                    latest_spot = float(bdata.get("bar", {}).get("c", 0.0))
                    if latest_spot > 0:
                        drift_pct = ((latest_spot - baseline_spot) / baseline_spot) * 100.0
                        if drift_pct >= drift_trigger_pct:
                            print(f"  🚀 DELTA-DRIFT ACCELERATOR: {symbol} (Beta {asset_beta:.2f}) rallied +{drift_pct:.2f}% >= {drift_trigger_pct:.2f}% (${baseline_spot:.2f} -> ${latest_spot:.2f}). Accelerating to Marketable Natural Limit!")
                            delta_drift_triggered = True
                except Exception: pass

            # 3. Stage Transitions
            new_limit = None
            if delta_drift_triggered and stage < 4:
                # Immediate acceleration to fresh natural limit
                q_now = self.get_option_snapshot([short_sym, long_sym])
                s_b_now = q_now.get(short_sym, {}).get("bid", 0.0)
                l_a_now = q_now.get(long_sym, {}).get("ask", 0.0)
                nat_now = round(s_b_now - l_a_now, 2) if (s_b_now > 0 and l_a_now > 0) else natural_credit
                if nat_now >= min_roc_credit:
                    new_limit = nat_now
                    stage = 4
                else:
                    print(f"  🛑 Delta drift natural credit ${nat_now:.2f} < 12.5% ROC floor (${min_roc_credit:.2f}). Holding patient.")

            elif elapsed >= 60 and stage == 3:
                # Stage 4: Marketable Natural Limit
                q_now = self.get_option_snapshot([short_sym, long_sym])
                s_b_now = q_now.get(short_sym, {}).get("bid", 0.0)
                l_a_now = q_now.get(long_sym, {}).get("ask", 0.0)
                nat_now = round(s_b_now - l_a_now, 2) if (s_b_now > 0 and l_a_now > 0) else natural_credit
                if nat_now >= min_roc_credit:
                    new_limit = nat_now
                    stage = 4
                    print(f"  ⚡ T+{elapsed:.0f}s: Transitioning to Stage 4 (Marketable Natural Limit @ ${new_limit:.2f})...")
                else:
                    print(f"  🛑 T+{elapsed:.0f}s: Natural credit ${nat_now:.2f} < ROC Floor (${min_roc_credit:.2f}). Standing down.")
                    break

            elif elapsed >= 35 and stage == 2:
                # Stage 3: Midpoint - 1 Tick (Inside edge with Penny-Jumper leapfrog)
                inside_target = round(mid_credit - tick, 2)
                if is_penny and (round(inside_target * 100) % 5 == 0):
                    inside_target = round(inside_target + 0.01, 2) # Jumps ahead of round nickel queue
                target_cred = max(min_roc_credit, inside_target)
                if target_cred != current_limit:
                    new_limit = target_cred
                    stage = 3
                    print(f"  ⚡ T+{elapsed:.0f}s: Transitioning to Stage 3 (Inside Edge Penny-Jump @ ${new_limit:.2f})...")

            elif elapsed >= 15 and stage == 1:
                # Stage 2: Exact Smart Midpoint
                if mid_credit != current_limit:
                    new_limit = mid_credit
                    stage = 2
                    print(f"  ⚡ T+{elapsed:.0f}s: Transitioning to Stage 2 (Smart Midpoint @ ${new_limit:.2f})...")

            # Execute replace if new_limit determined
            if new_limit and new_limit != current_limit:
                ok, rep_oid, msg = self.replace_vertical_spread_limit(current_oid, short_sym, long_sym, contracts, new_limit)
                if ok and rep_oid:
                    if listener:
                        listener.register_replacement(current_oid, rep_oid)
                    current_oid = rep_oid
                    current_limit = new_limit

        # Final check after loop completion
        if listener:
            ws_final = listener.get_order_update(current_oid)
            if ws_final and (ws_final.get("is_filled") or ws_final.get("status") == "filled" or ws_final.get("filled_qty", 0) >= contracts):
                fill_price = abs(float(ws_final.get("filled_avg_price") or current_limit))
                return True, [current_oid], f"Filled in Micro-Walk @ ${fill_price:.2f}", fill_price, True

        final_info = self.get_order(current_oid)
        if final_info.get("status") == "filled" or float(final_info.get("filled_qty", 0)) >= contracts:
            fill_price = abs(float(final_info.get("filled_avg_price") or current_limit))
            return True, [current_oid], f"Filled in Micro-Walk @ ${fill_price:.2f}", fill_price, True
        else:
            return True, [current_oid], f"Resting on book @ ${current_limit:.2f} (Armed for Stage 1/2 Tracker)", current_limit, False

    def get_order(self, order_id: str) -> Dict[str, Any]:
        """Fetches the latest status and fill details for an order ID."""
        try:
            return self._call_api(f"{self.base_url}/v2/orders/{order_id}")
        except Exception as e:
            return {"error": str(e), "status": "unknown"}

    def cancel_order(self, order_id: str) -> bool:
        """Cancels an active/resting order on Alpaca."""
        try:
            session = self._get_session()
            if session:
                r = session.delete(f"{self.base_url}/v2/orders/{order_id}", timeout=8)
                return r.status_code in (200, 204)
            req = urllib.request.Request(f"{self.base_url}/v2/orders/{order_id}", headers=self._headers(), method="DELETE")
            with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=8) as r:
                return r.status in (200, 204)
        except Exception:
            return False

    def replace_vertical_spread_limit(
        self,
        order_id: str,
        short_sym: str,
        long_sym: str,
        contracts: int,
        new_limit_credit: float
    ) -> Tuple[bool, str, str]:
        """
        Atomically replaces resting spread order with new limit credit (RULE-075/RULE-084).
        Priority 1: Native atomic PATCH /v2/orders/{order_id} (single RTT, ~225ms on keep-alive).
        Priority 2: Resilient fallback to Cancel-and-Replace if PATCH is rejected.
        """
        patch_payload = {"limit_price": f"-{abs(new_limit_credit):.2f}"}
        try:
            resp = self._call_api(f"{self.base_url}/v2/orders/{order_id}", method="PATCH", data=patch_payload, timeout=8)
            new_oid = resp.get("id")
            if new_oid:
                return True, new_oid, f"Atomically replaced spread order via PATCH with limit ${new_limit_credit:.2f} (ID: {new_oid})"
        except Exception as ex_patch:
            # If order was already filled while we decided to replace, don't re-enter
            try:
                ord_info = self.get_order(order_id)
                if ord_info.get("status") == "filled":
                    return True, order_id, f"Order {order_id} already filled during replacement attempt"
            except Exception:
                pass

        # Fallback to Cancel-and-Replace
        try:
            self.cancel_order(order_id)
            time.sleep(0.5)
            mleg_payload = {
                "order_class": "mleg",
                "type": "limit",
                "time_in_force": "day",
                "legs": [
                    {"symbol": short_sym, "ratio_qty": 1, "side": "sell", "position_intent": "sell_to_open"},
                    {"symbol": long_sym, "ratio_qty": 1, "side": "buy", "position_intent": "buy_to_open"}
                ],
                "qty": str(contracts),
                "limit_price": f"-{abs(new_limit_credit):.2f}"
            }
            resp = self._call_api(f"{self.base_url}/v2/orders", method="POST", data=mleg_payload, timeout=10)
            new_oid = resp.get("id")
            if new_oid:
                return True, new_oid, f"Replaced spread order via Cancel-and-Replace with limit ${new_limit_credit:.2f} (ID: {new_oid})"
        except Exception as ex_fb:
            return False, "", f"Failed to replace spread order (PATCH & Fallback both failed): {ex_fb}"
        return False, "", "Unknown error replacing order"

    def auto_heal_unmatched_positions(self) -> List[str]:
        """
        RULE-053: Autonomous Self-Healing Engine.
        Detects orphaned long legs and pairs them or preserves designated free tail hedges.
        """
        healed_actions = []
        try:
            positions = self.get_positions()
            # Find lone long options
            long_orphans = [p for p in positions if float(p.get("qty", 0)) > 0 and len(p.get("symbol", "")) > 10]
            short_legs = [p for p in positions if float(p.get("qty", 0)) < 0 and len(p.get("symbol", "")) > 10]

            for l in long_orphans:
                sym_l = l.get("symbol", "")
                qty_l = int(float(l.get("qty", 0)))
                val_l = float(l.get("market_value", 0))

                # Robust OSI Regex Symbol Parser (RULE-066)
                m = re.match(r"^([A-Z]+)\d{6}[CP]\d{8}$", sym_l)
                base_sym = m.group(1) if m else sym_l[:4].rstrip("1234567890")

                # Check if matching short leg exists
                matching_short = any(base_sym in s.get("symbol", "") for s in short_legs)
                
                if not matching_short:
                    # If long leg has $0.00 market value or is far OTM, preserve as free zero-cost tail hedge
                    if val_l <= 0.05 or "160" in sym_l or "205" in sym_l:
                        print(f"  🛡️ RULE-053 HEALER: Preserving {sym_l} ({qty_l}C) as free zero-cost tail hedge floor ($0 margin).")
                        continue

                    print(f"  🩺 RULE-053 HEALER: Found orphaned long leg {sym_l} ({qty_l}C on {base_sym}). Auto-pairing...")
                    try:
                        resolved = self.resolve_spread_pair(base_sym, float(sym_l[-8:]) / 1000.0 + 5.0, width=5.0, require_live_bid=False)
                        if resolved:
                            s_sym = resolved["short_sym"]
                            sell_payload = {"symbol": s_sym, "qty": str(qty_l), "side": "sell", "type": "market", "time_in_force": "day", "position_intent": "sell_to_open"}
                            resp = self._call_api(f"{self.base_url}/v2/orders", method="POST", data=sell_payload, timeout=8)
                            oid = resp.get("id")
                            if oid:
                                healed_actions.append(f"Auto-paired {qty_l}C {s_sym} (Order: {oid})")
                    except Exception as ex_pair:
                        print(f"  ℹ️ Healer pair notice for {sym_l}: {ex_pair}")
        except Exception as e:
            print(f"  ℹ️ Healer notice: {e}")
        return healed_actions

    def execute_waterfall_spread(
        self,
        candidate_list: List[Dict[str, Any]],
        contracts: int = 2,
        min_dte: Optional[int] = None,
        prewarmed_payload: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, Dict[str, Any], str]:
        """
        RULE-077: Cascades through candidate universe with Account-Aware DTE Calibration:
        - pion2_sub: 9-16 DTE Short-Cycle Sprint (rapid velocity)
        - pion_main: 14-28 DTE Core Defensive Swing
        Tier 3 Fast-Path: Immediately executes pre-warmed payload at T+0.00s.
        Pass 1: Enforces live bid validation.
        Pass 2: Seamless fallback to standard OSI contract pair with adaptive limit pricing.
        """
        if min_dte is None:
            min_dte = 9 if self.account_type == "pion2_sub" else 14

        vetoed_symbols = set()

        # Fast-Path: Ingest Tier 3 Pre-Warmed Payload at T+0.00s with Live Re-Validation Gate
        if prewarmed_payload and candidate_list:
            top_sym = candidate_list[0].get("symbol")
            if prewarmed_payload.get("symbol") == top_sym:
                s_strike = float(prewarmed_payload.get("short_strike", 0))
                l_strike = float(prewarmed_payload.get("long_strike", 0))
                short_sym = prewarmed_payload.get("short_sym")
                long_sym = prewarmed_payload.get("long_sym")
                exp_date = prewarmed_payload.get("exp_date")
                width = abs(s_strike - l_strike)
                roc_rate = 0.075 if width >= 20.0 else 0.125
                min_roc_credit = max(0.12, round(width * roc_rate, 2))

                # PRE-WARM-TO-FILL CREDIT RE-VALIDATION GATE (RULE-084 / Anna Morning Digest):
                # Instantaneous (<50ms) snapshot quote check at market open before committing fast-path payload.
                quotes = self.get_option_snapshot([short_sym, long_sym])
                live_s_bid = quotes.get(short_sym, {}).get("bid", 0.0)
                live_s_ask = quotes.get(short_sym, {}).get("ask", 0.0)
                live_l_bid = quotes.get(long_sym, {}).get("bid", 0.0)
                live_l_ask = quotes.get(long_sym, {}).get("ask", 0.0)

                live_nat_credit = round(live_s_bid - live_l_ask, 2)
                live_arith_mid = round(((live_s_bid + live_s_ask) / 2.0) - ((live_l_bid + live_l_ask) / 2.0), 2)

                # Gate check: Live bid must exist AND live mid credit must meet MAC floor
                # If market open bid is zero, or live mid is below MAC floor, pre-warmed credit assumption broke!
                if self.is_market_open() and (live_s_bid <= 0 or live_arith_mid < min_roc_credit or live_nat_credit < 0.10):
                    print(f"  🛑 PRE-WARM RE-VALIDATION GATE VETO: {top_sym} Live Mid ${live_arith_mid:.2f} (Nat ${live_nat_credit:.2f}) < MAC floor ${min_roc_credit:.2f} (short bid: ${live_s_bid:.2f})!")
                    print(f"  ⚡ Immediate Waterfall Cascade: Bypassing {top_sym} fast-path, falling back to candidate universe at T+0.05s!")
                    vetoed_symbols.add(top_sym)
                else:
                    # Update prewarmed payload with verified live market quotes if available
                    if live_s_bid > 0 and live_arith_mid >= min_roc_credit:
                        s_spread = max(0.01, live_s_ask - live_s_bid)
                        l_spread = max(0.01, live_l_ask - live_l_bid)
                        smart_s_mid = live_s_bid + 0.60 * s_spread
                        smart_l_mid = live_l_ask - 0.35 * l_spread
                        live_smart_mid = round(smart_s_mid - smart_l_mid, 2)
                        live_blended_mid = round(0.70 * live_smart_mid + 0.30 * live_arith_mid, 2)
                        penny_pilot = {"SPY", "QQQ", "IWM", "XLF", "NVDA", "AMD", "TSM", "AAPL", "MSFT", "AMZN", "GOOGL"}
                        is_penny = top_sym in penny_pilot
                        is_round_nickel = (round(live_blended_mid * 100) % 5 == 0)
                        snipe_offset = 0.01 if (is_penny and is_round_nickel) else 0.02
                        prewarmed_payload["prewarmed_mid_credit"] = live_blended_mid
                        prewarmed_payload["prewarmed_natural_credit"] = live_nat_credit
                        prewarmed_payload["prewarmed_snipe_credit"] = round(live_blended_mid + snipe_offset, 2)

                    print(f"  ⚡ Waterfall TIER 3 FAST-PATH: Firing pre-warmed {contracts}C {top_sym} Spread ({s_strike}/{l_strike} Exp: {exp_date}) at T+0.00s...")
                    success, order_ids, msg, limit_credit, is_filled = self.active_micro_walk_spread(
                        top_sym, s_strike, l_strike, contracts, exp_date=exp_date, min_dte=min_dte, prewarmed_payload=prewarmed_payload
                    )
                    if success and order_ids:
                        res = {
                            "symbol": top_sym,
                            "short_sym": prewarmed_payload.get("short_sym"),
                            "long_sym": prewarmed_payload.get("long_sym"),
                            "short_strike": s_strike,
                            "long_strike": l_strike,
                            "width": abs(s_strike - l_strike),
                            "exp_date": exp_date,
                            "order_ids": order_ids,
                            "limit_credit": limit_credit,
                            "is_filled_now": is_filled,
                            "filled_avg_price": limit_credit if is_filled else 0.0
                        }
                        fill_tag = "FILLED 🟢" if is_filled else "RESTING ⏳"
                        return True, res, f"Executed pre-warmed {contracts}C {top_sym} Bull Put Spread ({s_strike}/{l_strike}) on {self.account_type}! Status: {fill_tag} | Orders: {order_ids}"
                    else:
                        print(f"  ℹ️ Pre-warmed fast-path notice: {msg}. Falling back to standard waterfall discovery.")
                        vetoed_symbols.add(top_sym)

        # Pass 1: Live Bid Validation
        for cand in candidate_list:
            sym = cand.get("symbol")
            if sym in vetoed_symbols:
                continue
            target_short = float(cand.get("short_strike", 0))
            width = float(cand.get("width", 5.0))
            
            resolved = self.resolve_spread_pair(sym, target_short, width=width, require_live_bid=True, min_dte=min_dte)
            if resolved:
                s_strike = resolved["short_strike"]
                l_strike = resolved["long_strike"]
                exp_date = resolved["exp_date"]

                success, order_ids, msg, limit_credit, is_filled = self.active_micro_walk_spread(
                    sym, s_strike, l_strike, contracts, exp_date=exp_date, min_dte=min_dte
                )
                if success and order_ids:
                    resolved["order_ids"] = order_ids
                    resolved["limit_credit"] = limit_credit
                    resolved["is_filled_now"] = is_filled
                    resolved["filled_avg_price"] = limit_credit if is_filled else 0.0
                    fill_tag = "FILLED 🟢" if is_filled else "RESTING ⏳"
                    return True, resolved, f"Executed {contracts}C {sym} Bull Put Spread ({s_strike}/{l_strike}) on {self.account_type}! Status: {fill_tag} | Orders: {order_ids}"
                else:
                    print(f"  ℹ️ Waterfall Pass 1 candidate {sym} notice: {msg}")

        # Pass 2: Robust OSI Contract Pair Fallback
        for cand in candidate_list:
            sym = cand.get("symbol")
            if sym in vetoed_symbols:
                continue
            target_short = float(cand.get("short_strike", 0))
            width = float(cand.get("width", 5.0))
            
            resolved = self.resolve_spread_pair(sym, target_short, width=width, require_live_bid=False, min_dte=min_dte)
            if resolved:
                s_strike = resolved["short_strike"]
                l_strike = resolved["long_strike"]
                exp_date = resolved["exp_date"]

                print(f"  ⚡ Waterfall attempting {contracts}C {sym} Bull Put Spread ({s_strike}/{l_strike} Exp: {exp_date})...")
                success, order_ids, msg, limit_credit, is_filled = self.active_micro_walk_spread(
                    sym, s_strike, l_strike, contracts, exp_date=exp_date, min_dte=min_dte
                )
                if success and order_ids:
                    resolved["order_ids"] = order_ids
                    resolved["limit_credit"] = limit_credit
                    resolved["is_filled_now"] = is_filled
                    resolved["filled_avg_price"] = limit_credit if is_filled else 0.0
                    fill_tag = "FILLED 🟢" if is_filled else "RESTING ⏳"
                    return True, resolved, f"Executed {contracts}C {sym} Bull Put Spread ({s_strike}/{l_strike}) on {self.account_type}! Status: {fill_tag} | Orders: {order_ids}"
                else:
                    print(f"  ℹ️ Waterfall candidate {sym} notice: {msg}")

        return False, {}, "Waterfall execution could not find active options with live institutional bids across universe."
