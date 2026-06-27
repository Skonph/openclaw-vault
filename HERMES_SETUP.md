# Hermes Agent Setup

## What is Hermes
Hermes Agent (by Nous Research) is the AI co-pilot for this trading VPS. It operates via a Telegram gateway — all user messages arrive from Telegram, responses go back to Telegram. It has a full toolset: terminal, file I/O, browser, web search, cron scheduling, subagent delegation, and persistent memory.

## Architecture

```
Telegram User (Skon)
    │
    ▼
hermes-gateway (systemd service)
    │ Reads: TELEGRAM_BOT_TOKEN from ~/.hermes/.env
    │ Listens: long polling, Telegram Bot API
    ▼
Hermes Agent Core
    │ Model: glm-5.2 (via TokenHub, OpenAI-compatible)
    │ Provider: openai (custom base_url)
    │ Config: ~/.hermes/config.yaml
    │ Skills: ~/.hermes/skills/
    │ Memory: ~/.hermes/memories/
    ▼
VPS Tools (terminal, file, browser, web, cron, delegate)
```

## Key Paths
| Path | Purpose |
|------|---------|
| `~/.hermes/` | Hermes home directory |
| `~/.hermes/config.yaml` | Agent configuration |
| `~/.hermes/.env` | Environment variables (secrets) |
| `~/.hermes/skills/` | Reusable skills/procedures |
| `~/.hermes/memories/` | Persistent memory, budget tracking |
| `~/.hermes/skills/research/trading-analysis/` | Post-mortem analysis skill |
| `~/.hermes/skills/software-development/trading-bot-ops/` | Bot operations skill |
| `~/.hermes/logs/gateway.log` | Gateway message log |

## Gateway Details
- **Service name**: `hermes-gateway.service`
- **Service file**: `/etc/systemd/system/hermes-gateway.service`
- **Environment file**: `~/.hermes/.env`
- **Exec**: `python -m hermes_cli.main gateway run --replace`
- **Restart policy**: `always` (RestartSec=5)
- **Kill mode**: `mixed` (SIGTERM to main, cleanup to all children)
- **Timeout**: 210s graceful stop, 180s gateway drain

### To update Telegram bot token:
1. Edit `~/.hermes/.env` → find `TELEGRAM_BOT_TOKEN=...`
2. Replace with new token from @BotFather
3. `sudo systemctl restart hermes-gateway`

## Model Switching

### Currently active: `glm-5.2` via TokenHub
Switch record at `~/.hermes/memories/model_switch.json`

### To switch model:
```bash
hermes config set model.default <model-name>
sudo systemctl restart hermes-gateway
```

### TokenHub free quota: 1M tokens per model
- Tracked in `~/.hermes/memories/budget.json`
- No programmatic usage query API — self-managed tracking
- Switch back to `deepseek-v4-flash-202605` before exceeding quota

## Telegram Group Integration
- **AOTS Steering Committee**: Shared group with Skon, Hermes, Anna
- **Chat ID**: `-1004375899205` (negative = group/supergroup)
- **Tag protocol**: `@hermesSkon_bot` → Hermes, `@annabel12_bot` → Anna
- **Annabel bot username**: `annabel12_bot` (Anna, "The Flowmaster")
- **Bot info saved at**: `~/shared/annabel_bot_info.txt`

## Budget Tracking
- **Primary cost**: TokenHub glm-5.2 (950K free tokens — 1M per model)
- **Legacy cost**: Anthropic (pay-per-use, ~$12.08 last snapshot Jun 13)
- **Watchdog**: `~/shared/credit_watchdog.py` (checks both TokenHub + Anthropic)
- **Auto-throttle**: TokenHub >80% → 🟡 alert, >95% → 💀 critical

## Key Commands
```bash
# Check gateway status
sudo systemctl status hermes-gateway

# Restart gateway
sudo systemctl restart hermes-gateway

# View gateway logs
journalctl -u hermes-gateway -n 50

# Check Telegram messages
grep 'inbound message' ~/.hermes/logs/gateway.log

# Check model
hermes config get model.default

# Switch model
hermes config set model.default deepseek-v4-flash-202605
sudo systemctl restart hermes-gateway
```

## Cron Jobs Managed by Hermes
| Job ID | Schedule | Purpose |
|--------|----------|---------|
| `afaaf2b40c47` | 21:45 Mon–Fri | Trading bot watchdog (scan outcome report) |

## Memory System
- **Memory char limit**: 2,200
- **User profile char limit**: 1,375
- **Provider**: Local (not external)
- **Persistence**: Survives session restarts and gateway restarts
