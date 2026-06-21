"""
Telegram notify + approve.

Two jobs:
  notify(text)                 -> fire-and-forget status messages (opens, closes, halts)
  request_approval(text, id)   -> post a plan with [Approve]/[Reject] buttons and
                                  block until the user taps one (or times out -> reject)

Stdlib HTTP only (urllib), so no extra dependency on the server. The HTTP layer
is injected so tests run offline. Approval defaults to REJECT on timeout/error —
fail safe: silence never opens a trade.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, Optional

API = "https://api.telegram.org/bot{token}/{method}"


def _http_post(url: str, payload: dict, timeout: float = 20.0) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _http_get(url: str, timeout: float = 35.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


HttpPost = Callable[[str, dict], dict]
HttpGet = Callable[[str], dict]


class TelegramClient:
    def __init__(self, token: str, chat_id: str,
                 http_post: HttpPost = _http_post,
                 http_get: HttpGet = _http_get):
        self.token = token
        self.chat_id = str(chat_id)
        self._post = http_post
        self._get = http_get
        self._offset = 0  # getUpdates cursor

    def _url(self, method: str) -> str:
        return API.format(token=self.token, method=method)

    # ---------- notifications ----------
    def notify(self, text: str) -> None:
        # Try Markdown first; if Telegram rejects the entities (common with dynamic
        # content like debit_call_spread or model reasoning), resend as plain text
        # so the message is ALWAYS delivered. The Markdown miss is expected, not an
        # error, so we don't log it — only a total failure is worth a line.
        if self._send(text, markdown=True):
            return
        if not self._send(text, markdown=False):
            print("[telegram] notify failed (markdown and plain both rejected).")

    def _send(self, text: str, markdown: bool) -> bool:
        payload = {"chat_id": self.chat_id, "text": text,
                   "disable_web_page_preview": True}
        if markdown:
            payload["parse_mode"] = "Markdown"
        try:
            resp = self._post(self._url("sendMessage"), payload)
            # Telegram returns {"ok": false, ...} on parse errors without raising
            if isinstance(resp, dict) and resp.get("ok") is False:
                return False
            return True
        except Exception:
            return False

    # ---------- approval ----------
    def request_approval(self, text: str, request_id: str,
                         timeout_sec: int = 900, poll_sec: float = 3.0) -> bool:
        """Post text + Approve/Reject buttons; block until tapped or timeout.
        Returns True only on an explicit Approve. Any failure/timeout -> False."""
        keyboard = {
            "inline_keyboard": [[
                {"text": "✅ Approve", "callback_data": f"approve:{request_id}"},
                {"text": "❌ Reject", "callback_data": f"reject:{request_id}"},
            ]]
        }
        try:
            self._post(self._url("sendMessage"), {
                "chat_id": self.chat_id, "text": text, "parse_mode": "Markdown",
                "reply_markup": keyboard, "disable_web_page_preview": True,
            })
        except Exception as e:
            print(f"[telegram] approval send failed: {e}")
            return False

        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            decision = self._poll_decision(request_id)
            if decision is not None:
                self.notify(f"_Recorded: {'APPROVED' if decision else 'REJECTED'} "
                            f"({request_id})_")
                return decision
            time.sleep(poll_sec)
        self.notify(f"_Approval timed out for {request_id} -> REJECTED (fail-safe)._")
        return False

    def _poll_decision(self, request_id: str) -> Optional[bool]:
        """One getUpdates pass; return True/False if a matching callback arrived."""
        url = self._url("getUpdates") + "?timeout=10&offset=" + str(self._offset)
        try:
            resp = self._get(url)
        except Exception as e:
            print(f"[telegram] getUpdates failed: {e}")
            return None
        for upd in resp.get("result", []):
            self._offset = max(self._offset, upd.get("update_id", 0) + 1)
            cb = upd.get("callback_query")
            if not cb:
                continue
            data = cb.get("data", "")
            if data.endswith(f":{request_id}"):
                # acknowledge the tap so the button stops spinning
                try:
                    self._post(self._url("answerCallbackQuery"),
                               {"callback_query_id": cb["id"]})
                except Exception:
                    pass
                return data.startswith("approve:")
        return None


# Null object so the pipeline can run without Telegram configured.
class NullTelegram:
    def notify(self, text: str) -> None:
        print(f"[notify] {text}")

    def request_approval(self, text: str, request_id: str,
                         timeout_sec: int = 900, poll_sec: float = 3.0) -> bool:
        print(f"[approval:auto-reject:no-telegram] {request_id}")
        return False


def from_config(cfg) -> "TelegramClient | NullTelegram":
    if cfg.telegram_token and cfg.telegram_chat_id:
        return TelegramClient(cfg.telegram_token, cfg.telegram_chat_id)
    return NullTelegram()
