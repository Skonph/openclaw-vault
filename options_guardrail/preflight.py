"""
Preflight connectivity check — run on the server to confirm the wiring works
BEFORE relying on any scheduled job. Validates:

    - Tradier  : market clock + a quote (proves token + entitlement)
    - OpenRouter: API key present (and, with --ping, a tiny live call)
    - Telegram : sends a confirmation message to your chat

    ./run.sh preflight.py            # checks + posts a Telegram summary
    ./run.sh preflight.py --ping     # also makes one tiny OpenRouter call

Pure check_* functions take injected clients so they're unit-testable offline.
Exit code 0 = all good, non-zero = something failed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class Check:
    name: str
    ok: bool
    detail: str

    @property
    def line(self) -> str:
        return f"{'✅' if self.ok else '❌'} {self.name}: {self.detail}"


def check_tradier(client, symbol: str = "SPY") -> Check:
    try:
        clock = client.clock()
        q = client.quote_summary([symbol]).get(symbol, {})
        last = q.get("last")
        if last is None:
            return Check("Tradier", False, "connected but no quote (entitlement?)")
        return Check("Tradier", True,
                     f"{clock.get('state','?')}, {symbol} last={last}")
    except Exception as e:
        return Check("Tradier", False, f"{e}")


def check_telegram(tg) -> Check:
    # The send itself is the test; NullTelegram returns by printing.
    try:
        tg.notify("✅ guardrail preflight: Telegram reachable.")
        return Check("Telegram", True, "test message sent")
    except Exception as e:
        return Check("Telegram", False, f"{e}")


def check_openrouter(cfg, ping: bool = False, caller=None) -> Check:
    if cfg.strategist_provider != "openrouter":
        return Check("Model", True, f"provider={cfg.strategist_provider} (skipped)")
    if not cfg.openrouter_api_key:
        return Check("OpenRouter", False, "OPENROUTER_API_KEY not set")
    if not ping:
        return Check("OpenRouter", True, f"key present, model={cfg.strategist_model}")
    try:
        from strategist_run import caller_from_config
        call = caller or caller_from_config(cfg)
        out = call("You reply with one word.", "Say OK.", cfg.strategist_model)
        return Check("OpenRouter", True, f"live call ok ({out[:20]!r})")
    except Exception as e:
        return Check("OpenRouter", False, f"live call failed: {e}")


def run_checks(cfg, tradier_client, tg, ping: bool = False,
               caller=None) -> List[Check]:
    checks = [check_telegram(tg)]
    if tradier_client is not None:
        checks.append(check_tradier(tradier_client))
    else:
        checks.append(Check("Tradier", False, "no token configured"))
    checks.append(check_openrouter(cfg, ping=ping, caller=caller))
    return checks


def summarize(checks: List[Check]) -> str:
    head = "🔎 *Guardrail preflight*"
    body = "\n".join(c.line for c in checks)
    allok = all(c.ok for c in checks)
    return f"{head}\n{body}\n{'All systems go.' if allok else '⚠️ Fix the ❌ items.'}"


def main() -> int:
    import argparse
    from config import Config
    from telegram_notify import from_config as telegram_from_config
    from tradier_feed import TradierClient

    ap = argparse.ArgumentParser()
    ap.add_argument("--ping", action="store_true",
                    help="make one tiny live OpenRouter call (costs a few tokens)")
    args = ap.parse_args()

    cfg = Config.load()
    tg = telegram_from_config(cfg)
    client = (TradierClient(cfg.tradier_token, cfg.tradier_base_url)
              if cfg.tradier_token else None)

    checks = run_checks(cfg, client, tg, ping=args.ping)
    report = summarize(checks)
    print(report)
    try:
        tg.notify(report)
    except Exception as e:
        print(f"(could not post summary to Telegram: {e})")
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
