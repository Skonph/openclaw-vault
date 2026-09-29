# Server Infrastructure

## Host
- **IP**: 43.156.9.185
- **Type**: Tencent Cloud LightHouse (轻量应用服务器)
- **OS**: Ubuntu 24.04.5 LTS (Kernel 6.8.0-142-generic)
- **Specs**: 2 vCPUs, 2GB RAM, 40GB SSD
- **Node Runtime**: Node.js v24.16.0 LTS (Krypton)
- **Timezone**: ICT (UTC+7, Bangkok)
- **US Market overlap**: 20:30–04:00 ICT

## Systemd Services

### Active (Enabled & Auto-Starting on Boot)
| Service | Type | Runtime | Purpose | Notes |
|---------|------|---------|---------|-------|
| `hermes-gateway.service` | System | active/running | Hermes Agent Telegram gateway | Port: 9222 browser debugging |
| `tradier-bot.service` | System | active/running | Tradier Telegram command bot | Python, always-on, enabled |
| `openclaw-gateway.service` | User (`systemd --user`) | active/running | OpenClaw Anna Telegram gateway | Node v24.16.0, v2026.9.6, user lingering enabled |

### Inactive / Disabled
| Service | Runtime | Purpose | Notes |
|---------|---------|---------|-------|
| `openclaw-upgrade-guard.service` | inactive/dead | Server boot guard | Oneshot, runs on boot |
| `ibc-gateway.service` | **stopped+disabled** | IBKR Gateway | Decommissioned Jun 27, was auto-restart-looping |

## File System Layout
```
/home/ubuntu/
├── .hermes/               # Hermes Agent (config, skills, memories, logs)
├── trading-bot/           # Tradier bot (scanner, monitor, telegram, logs)
├── openclaw/              # OpenClaw (scanner, screener, vault, logs)
├── shared/                # Cross-project infra
│   ├── market_context_writer.py
│   ├── portfolio_tracker.py
│   ├── credit_watchdog.py
│   ├── conviction_scorer.py
│   ├── graduation_scorecard.py
│   ├── hermes_preflight.py
│   ├── hooks/             # Correlation guard hooks
│   └── logs/
├── guardrail/             # Decommissioned IBKR system (historical ref only)
├── docs/                  # This documentation (Jun 2026)
├── openclaw-vault/        # Obsidian vault backup
└── status/                # Consolidated context files
```

## API Keys and Secrets

### Storage Locations
| Credential | File | Used By |
|-----------|------|---------|
| Telegram Bot Token | `~/.hermes/.env` | hermes-gateway |
| TokenHub API Key | `~/.hermes/config.yaml` | Hermes agent |
| Anthropic API Key | `~/.hermes/.env` (legacy) | — |
| OpenRouter API Key | `~/.hermes/.env` | Fallback |
| Tradier Sandbox Token | `~/trading-bot/.env` | daily_scan.py |
| Tradier Prod Token | `~/trading-bot/.env` | Market data |
| Alpaca API Key | `~/openclaw/.env` | OpenClaw executor |
| Alpaca Secret Key | `~/openclaw/.env` | OpenClaw executor |
| Finnhub API Key | `~/guardrail/.env` | Events data |

**Security**: `.env` files are protected by defense-in-depth — `read_file` tool denies access. Use `grep` via `terminal` for non-secret values.

## Cron Jobs (full schedule)

```
20:55 Mon–Fri   → shared/market_context_writer.py
21:05 Mon–Fri   → openclaw_scanner.py
21:15 Mon–Fri   → run_scan.sh (Tradier)
21:20 Mon–Fri   → vault_updater.py
21:25 Mon–Fri   → openclaw_signals.py
21:30 Mon–Fri   → position_monitor.py (Tradier exit #1)
00:05 Tue–Sat   → openclaw_scanner.py (midday)
00:15 Tue–Sat   → run_scan.sh (Tradier midday)
00:20 Tue–Sat   → vault_updater.py (midday)
00:00 Tue–Sat   → position_monitor.py (exit #2)
02:30 Tue–Sat   → position_monitor.py (exit #3)
07:30 Tue–Sat   → morning_report.py
08:00 Tue–Sat   → daily_summary.py
09:00 Every 2d   → credit_watchdog.py
08:00 Sunday     → graduation_scorecard.py
21:45 Mon–Fri    → Hermes watchdog cron
```

## Budget & Cost Tracking

### TokenHub (Primary — 2026 Sep)
- OpenClaw Primary: `tokenhub/glm-5.3-flash` (Fallbacks: `tokenhub/deepseek-v4-flash`, `tokenhub/glm-5`)
- Hermes Primary: `glm-5.2` (Fallback: `deepseek-v4-flash`)
- Self-tracked via `~/.hermes/memories/budget.json`

### Anthropic (Legacy)
- Last snapshot: $12.08 (Jun 13, 2026)
- Est. daily burn: ~$0.35/day
- Alert thresholds: $8 🟡 → $5 🔴 → $2 💀

### External Accounts
| Service | Plan | Cost | Used For |
|---------|------|------|----------|
| Claude Pro (Anthropic) | $20/mo | $20 | Interactive sessions |
| Antigravity Pro | $20/mo | $20 | Interactive sessions |
| TokenHub (Tencent) | Free trial | $0 | Current agent + cron |

### Total Monthly Spend (Jun 2026 ~ estimate)
- TokenHub: $0 (free trial)
- Anthropic API: ~$10-15 (declining post-switch)
- Human plans: $40 (Claude Pro + Antigravity Pro)
- **Total**: ~$50-55/mo

## Playwright Browser
- Used by Hermes for web navigation (`browser_navigate`)
- Headless Chromium, remote debugging on port 9222
- Orphaned instances from decommissioned OpenClaw vault system cleaned up Jun 27
- Service mode: local (no cloud proxy)
