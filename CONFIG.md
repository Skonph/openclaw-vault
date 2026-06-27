# Configuration & Cron Schedule

## Environment Variables (`~/.hermes/.env`)

**REDACTED** — actual values stored on server. Template for reference:

```bash
# ─── LLM PROVIDERS ───
# TokenHub (Primary — Tencent Cloud MAAS, OpenAI-compatible)
OPENAI_API_KEY=sk-...    # TokenHub API key
# TokenHub base URL is in config.yaml: https://tokenhub-intl.tencentcloudmaas.com/v1

# Anthropic (Legacy)
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_TOKEN=...       # Deprecated — was used for Telegram gateway token

# OpenRouter (Fallback)
OPENROUTER_API_KEY=sk-or-v1-...

# ─── TELEGRAM ───
TELEGRAM_BOT_TOKEN=899107...    # From @BotFather
TELEGRAM_ALLOWED_USERS=8069530075
TELEGRAM_HOME_CHANNEL=8069530075

# ─── TRADIER ───
# (stored in ~/trading-bot/.env)
# TRADIER_TOKEN=...          # Sandbox API token
# TRADIER_PROD_TOKEN=...     # Production API token (market data only)
# TRADIER_ACCOUNT_ID=...     # Sandbox account number

# ─── ALPACA ───
# (stored in ~/openclaw/.env)
# ALPACA_API_KEY=PK...
# ALPACA_SECRET_KEY=...

# ─── FINNHUB (Events Data) ───
# (stored in ~/guardrail/.env)
# FINNHUB_API_KEY=...

# ─── SERVER ───
TERMINAL_MODAL_IMAGE=nikolaik/python-nodejs:python3.11-nodejs20
TERMINAL_TIMEOUT=60
TERMINAL_LIFETIME_SECONDS=300
```

## Hermes Agent Config (`~/.hermes/config.yaml`)

Key settings:
- **Model**: glm-5.2 (Model switch: deepseek-v4-flash-202605 → glm-5.2 on Jun 27)
- **Provider**: openai (points to TokenHub)
- **Gateway**: hermes-gateway (systemd service, listens for Telegram messages)
- **Personalities**: helpful, concise, technical, creative, teacher, kawaii, catgirl, pirate, shakespeare, surfer, noir, uwu, philosopher, hype
- **Skills**: trading-analysis, trading-bot-ops, hermes-agent, plan, etc.
- **Telegram**: reactions off, allowed_chats: 8069530075 (Skon's DM)
- **Budget tracking**: `~/.hermes/memories/budget.json` (self-managed TokenHub quota)

## Cron Schedule (Bangkok ICT = UTC+7)

### US Morning (10 AM ET = 21:00 ICT)
| Time ICT | Days | Job | Notes |
|----------|------|-----|-------|
| 20:55 | Mon–Fri | `shared/market_context_writer.py` | Pre-scan regime snapshot |
| 21:05 | Mon–Fri | `openclaw_scanner.py` | OpenClaw morning scan |
| 21:15 | Mon–Fri | `run_scan.sh` (Tradier) | Primary Tradier morning scan |
| 21:20 | Mon–Fri | `vault_updater.py` | OpenClaw vault persist |
| 21:25 | Mon–Fri | `openclaw_signals.py` | Signals to vault |
| 21:30 | Mon–Fri | `position_monitor.py` | Tradier exit check #1 |

### US Midday (1 PM ET = 00:00 ICT)
| Time ICT | Days | Job |
|----------|------|-----|
| 00:05 | Tue–Sat | `openclaw_scanner.py` (midday) |
| 00:15 | Tue–Sat | `run_scan.sh` (Tradier midday) |
| 00:20 | Tue–Sat | `vault_updater.py` (midday persist) |
| 00:00 | Tue–Sat | `position_monitor.py` (exit check #2) |

### US Afternoon (3:30 PM ET = 02:30 ICT)
| Time ICT | Days | Job |
|----------|------|-----|
| 02:30 | Tue–Sat | `position_monitor.py` (exit check #3) |

### Morning Reports
| Time ICT | Days | Job |
|----------|------|-----|
| 07:30 | Tue–Sat | `morning_report.py` (OpenClaw) |
| 08:00 | Tue–Sat | `daily_summary.py` (Tradier) |

### Maintenance
| Time ICT | Days | Job |
|----------|------|-----|
| 09:00 | Every 2 days | `shared/credit_watchdog.py` |
| 08:00 | Sunday | `shared/graduation_scorecard.py` |

### Day-of-Week Legend
| Abbrev | Days | Actual |
|--------|------|--------|
| Mon–Fri | 1–5 | Industry standard |
| Tue–Sat | 2–6 | Mon–Fri US market (Bangkok gets results next day) |

## Systemd Services
| Service | Status | Purpose |
|---------|--------|---------|
| `hermes-gateway.service` | ✅ active | Hermes Agent Telegram gateway |
| `tradier-bot.service` | ✅ active | Telegram bot command dispatcher |
| `openclaw-upgrade-guard.service` | 💤 inactive | Boot guard (oneshot) |
| `ibc-gateway.service` | ❌ stopped+disabled | Was IBKR Gateway — permanently decommissioned Jun 27 |
