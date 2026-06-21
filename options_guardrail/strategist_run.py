"""
Strategist runner — calls Opus to produce the session plan JSON.

Evening (ICT) cron job: assembles the strategist system prompt + tonight's market
context, calls the Opus API, extracts/validates the JSON envelope, and writes it
to disk for the session orchestrator to pick up at the open.

The Anthropic client is injected so this is testable offline. If the model emits
junk, the bridge drops bad plans; if NOTHING parses, we write a safe no_trade
envelope rather than letting the session run on garbage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from config import Config
from strategist_bridge import parse_strategist_output, StrategistEnvelope

# A callable (system_prompt, user_content, model) -> raw_text. Lets tests inject.
ModelCaller = Callable[[str, str, str], str]


def _anthropic_caller(api_key: str) -> ModelCaller:
    def call(system_prompt: str, user_content: str, model: str) -> str:
        from anthropic import Anthropic  # imported lazily; only needed for live runs
        client = Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=model,
            max_tokens=4000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        # concatenate text blocks
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    return call


def _openrouter_caller(api_key: str) -> ModelCaller:
    """OpenAI-compatible chat completions via OpenRouter. Stdlib HTTP, no SDK."""
    import urllib.request

    def call(system_prompt: str, user_content: str, model: str) -> str:
        body = json.dumps({
            "model": model,
            "max_tokens": 4000,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions", data=body,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json",
                     "X-Title": "guardrail-strategist"})
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read().decode())
        return resp["choices"][0]["message"]["content"]
    return call


def caller_from_config(cfg: Config) -> ModelCaller:
    if cfg.strategist_provider == "openrouter":
        if not cfg.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set")
        return _openrouter_caller(cfg.openrouter_api_key)
    if not cfg.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    return _anthropic_caller(cfg.anthropic_api_key)


def build_user_content(context: Dict[str, Any]) -> str:
    """Render tonight's inputs into the user message. `context` is whatever your
    data collection produced: overnight flow, IV, calendar, account, watchlist."""
    today = context.get("session_date") or datetime.utcnow().date().isoformat()
    parts = [f"SESSION DATE (next RTH): {today}", ""]
    for key in ("account", "overnight_flow", "iv", "economic_calendar",
                "watchlist", "open_positions", "notes"):
        if key in context and context[key] not in (None, "", [], {}):
            parts.append(f"## {key.upper()}")
            val = context[key]
            parts.append(val if isinstance(val, str) else json.dumps(val, indent=2))
            parts.append("")
    parts.append("Produce the JSON envelope per your instructions. JSON only.")
    return "\n".join(parts)


def _safe_no_trade(session_date: str, reason: str) -> dict:
    return {
        "session_date": session_date,
        "generated_at_iso": datetime.utcnow().isoformat(),
        "regime": "unknown",
        "reasoning": f"Auto no-trade: {reason}",
        "no_trade": True,
        "plans": [],
    }


@dataclass
class StrategistRunResult:
    envelope: StrategistEnvelope
    raw_text: str
    output_path: Path
    wrote_safe_default: bool


def run_strategist(context: Dict[str, Any], cfg: Optional[Config] = None,
                   caller: Optional[ModelCaller] = None) -> StrategistRunResult:
    cfg = cfg or Config.load()
    session_date = context.get("session_date") or datetime.utcnow().date().isoformat()
    system_prompt = cfg.strategist_prompt_path.read_text()
    user_content = build_user_content(context)

    if caller is None:
        caller = caller_from_config(cfg)

    wrote_safe = False
    try:
        raw_text = caller(system_prompt, user_content, cfg.strategist_model)
        envelope = parse_strategist_output(raw_text)
        payload = envelope.raw
    except Exception as e:
        # Model error or unparseable -> fail safe to no_trade, never crash the cron.
        raw_text = ""
        payload = _safe_no_trade(session_date, f"strategist error: {e}")
        envelope = parse_strategist_output(json.dumps(payload))
        wrote_safe = True

    cfg.strategist_output_path.write_text(json.dumps(payload, indent=2))
    return StrategistRunResult(
        envelope=envelope, raw_text=raw_text,
        output_path=cfg.strategist_output_path, wrote_safe_default=wrote_safe,
    )


def _load_context(path: Optional[str]) -> Dict[str, Any]:
    if path and Path(path).exists():
        return json.loads(Path(path).read_text())
    # minimal default; real cron should pass a context file built from your feeds
    return {"notes": "No context file provided; strategist runs on priors only."}


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--context", help="path to JSON context file (flow/iv/calendar)")
    args = ap.parse_args()

    res = run_strategist(_load_context(args.context))
    env = res.envelope
    print(f"Strategist wrote {res.output_path}")
    print(f"  session {env.session_date} | no_trade={env.no_trade} | "
          f"plans={len(env.plans)} | dropped={len(env.dropped)}"
          + ("  (SAFE DEFAULT)" if res.wrote_safe_default else ""))


if __name__ == "__main__":
    main()
