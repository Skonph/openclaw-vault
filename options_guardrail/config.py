"""
Central ops configuration — everything read from environment variables.

Secrets (API keys, bot tokens) NEVER live in code or git. On the Ubuntu box put
them in /opt/guardrail/.env (chmod 600) and load via systemd EnvironmentFile or
`set -a; . .env; set +a`. See .env.example.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _get(name: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.getenv(name, default)
    if required and not val:
        raise RuntimeError(f"required env var {name} is not set")
    return val


@dataclass
class Config:
    # --- strategist model access ---
    strategist_provider: str            # "openrouter", "anthropic" or "tokenhub"
    anthropic_api_key: str | None
    openrouter_api_key: str | None
    strategist_model: str
    strategist_prompt_path: Path

    # --- Telegram ---
    telegram_token: str | None
    telegram_chat_id: str | None
    approval_timeout_sec: int

    # --- IBKR ---
    ibkr_host: str
    ibkr_port: int
    ibkr_client_id: int
    ibkr_paper_only: bool

    # --- Tradier (data feed for the strategist context) ---
    tradier_env: str            # "sandbox" or "prod"
    tradier_token: str | None
    tradier_base_url: str
    tradier_account: str | None

    # --- economic calendar (Finnhub preferred, FRED fallback) ---
    finnhub_api_key: str | None
    fred_api_key: str | None

    # --- runtime ---
    mode: str               # "semi" (Telegram approval) or "auto"
    equity: float
    data_dir: Path
    market_data_provider: str = "ibkr"
    tokenhub_api_key: str | None = None

    @staticmethod
    def load() -> "Config":
        here = Path(__file__).parent
        data_dir = Path(_get("GUARDRAIL_DATA_DIR", str(here / "data")))
        data_dir.mkdir(parents=True, exist_ok=True)

        tokenhub_key = _get("TOKENHUB_API_KEY")
        openrouter_key = _get("OPENROUTER_API_KEY")
        anthropic_key = _get("ANTHROPIC_API_KEY")
        # auto-detect provider unless explicitly set; prefer TokenHub, then OpenRouter
        provider = _get("STRATEGIST_PROVIDER",
                        "tokenhub" if tokenhub_key
                        else "openrouter" if openrouter_key
                        else "anthropic").lower()
        default_model = ("deepseek-v4-flash" if provider == "tokenhub"
                         else "anthropic/claude-haiku-4.5" if provider == "openrouter"
                         else "claude-haiku-4-5")
        # equity: GUARDRAIL_EQUITY wins, else fall back to STARTING_CAPITAL
        equity = float(_get("GUARDRAIL_EQUITY") or _get("STARTING_CAPITAL", "100000"))

        # Tradier feed: sandbox by default. Token falls back across the names you have.
        tradier_env = _get("TRADIER_ENV", "sandbox").lower()
        if tradier_env == "prod":
            tradier_base = _get("TRADIER_BASE_URL", "https://api.tradier.com/v1")
            tradier_token = _get("TRADIER_PROD_TOKEN") or _get("TRADIER_API_KEY")
        else:
            tradier_base = _get("TRADIER_BASE_URL", "https://sandbox.tradier.com/v1")
            tradier_token = _get("TRADIER_SANDBOX_TOKEN") or _get("TRADIER_API_KEY")

        return Config(
            strategist_provider=provider,
            anthropic_api_key=anthropic_key,
            openrouter_api_key=openrouter_key,
            strategist_model=_get("STRATEGIST_MODEL", default_model),
            tokenhub_api_key=tokenhub_key,
            strategist_prompt_path=Path(
                _get("STRATEGIST_PROMPT_PATH", str(here / "strategist_prompt.md"))),
            telegram_token=_get("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_get("TELEGRAM_CHAT_ID"),
            approval_timeout_sec=int(_get("APPROVAL_TIMEOUT_SEC", "900")),  # 15 min
            ibkr_host=_get("IBKR_HOST", "127.0.0.1"),
            ibkr_port=int(_get("IBKR_PORT", "7497")),       # paper
            ibkr_client_id=int(_get("IBKR_CLIENT_ID", "17")),
            ibkr_paper_only=_get("IBKR_PAPER_ONLY", "true").lower() != "false",
            tradier_env=tradier_env,
            tradier_token=tradier_token,
            tradier_base_url=tradier_base,
            tradier_account=_get("TRADIER_SANDBOX_ACCOUNT"),
            finnhub_api_key=_get("FINNHUB_API_KEY"),
            fred_api_key=_get("FRED_API_KEY"),
            mode=_get("GUARDRAIL_MODE", "auto").lower(),  # paper phase: fully autonomous
            equity=equity,
            data_dir=data_dir,
            market_data_provider=_get("MARKET_DATA_PROVIDER", "ibkr").lower(),
        )

    # convenient derived paths (state/positions/strategist output live together)
    @property
    def state_path(self) -> Path:
        return self.data_dir / "session_state.json"

    @property
    def positions_path(self) -> Path:
        return self.data_dir / "session_positions.json"

    @property
    def strategist_output_path(self) -> Path:
        return self.data_dir / "strategist_output.json"
