# System Architecture

## Overview

A Bangkok-hosted (UTC+7) autonomous options trading ecosystem running on a single Ubuntu VPS (`43.156.9.185`, 2GB RAM, 2 vCPUs). Two active trading systems + one shared infrastructure layer + one Hermes AI agent for coordination.

```
┌─────────────────────────────────────────────────────┐
│                    HERMES AI AGENT                   │
│  (Nous Research Hermes Agent — Telegram gateway)     │
│  CSO: post-mortem analysis, system audit, steering    │
├─────────────────────────────────────────────────────┤
│                    SHARED LAYER                       │
│  market_context_writer │ portfolio_tracker           │
│  credit_watchdog       │ conviction_scorer           │
│  graduation_scorecard  │ bridge (inter-agent)        │
├──────────────────┬──────────────────────────────────┤
│  ~~TRADIER BOT~~ │        OPENCLAW v3               │
│  (daily_scan.py) │  (openclaw_scanner.py)           │
│  Bull Put only   │  Iron Condor + debit spreads     │
│  Trade: $320 max │  IC when VIX≥18, debit otherwise │
│  Broker: Tradier │  Broker: Alpaca                  │
│  paper sandbox   │  paper account (~$2,898 equity)  │
└──────────────────┴──────────────────────────────────┘
```

## Physical vs Logical Architecture

### Physical Deployment (what lives on disk)
| System | Directory | Broker | Type | Status |
|--------|-----------|--------|------|--------|
| Tradier Bot | `~/trading-bot/` | Tradier (paper) | Credit spreads | Active |
| OpenClaw | `~/openclaw/` | Alpaca (paper) | Debit + IC | Active |
| Shared | `~/shared/` | — | Cross-project infra | Active |
| Guardrail | `~/guardrail/` | ~~IBKR~~ | Decommissioned | Historical only |

### 5-Agent Framework (logical layers — every bot implements all 5)
| Layer | Role | Who |
|-------|------|-----|
| 1. Execution | Executes trades on broker APIs | Bot-specific executor |
| 2. Research | Market analysis, regime, data collection | Scanner scripts |
| 3. Portfolio Risk | Cross-portfolio risk, correlation, kill-switches | `portfolio_tracker.py` |
| 4. Conviction | Scoring, ranking, strategy selection | `conviction_scorer.py` |
| 5. Post-Mortem (CSO) | P&L analysis, system audit, improvement tracking | **Hermes** |

## Communication Channels

- **Telegram DM**: Skon ↔ Hermes (primary command channel)
- **AOTS Steering Committee** (Telegram group): Skon + Hermes + Anna (macro analyst)
- **Agent Bridge** (`~/shared/bridge.db`): Hermes ↔ Anna structured data exchange
- **Bridge CLI**: `~/shared/bridge_cli.py` — read/write structured messages
- **Tag protocol**: `@annabel12_bot` → Anna, `@hermesSkon_bot` → Hermes

## Model/LLM Providers

| Provider | Endpoint | Model | Used By |
|----------|----------|-------|---------|
| **TokenHub** (Primary) | `tokenhub-intl.tencentcloudmaas.com/v1` | glm-5.2 / deepseek-v4-flash | This agent, cron jobs |
| **Anthropic** (Legacy) | Direct SDK | Claude Haiku/Sonnet | Fallback, old cron jobs |
| **OpenRouter** | `openrouter.ai` | Various | Fallback |

TokenHub free quota: 1M tokens per model. No programmatic usage API — self-tracked via `~/.hermes/memories/budget.json`.

## Key Metrics (as of Jun 26, 2026)

- VIX: 18.87 (moderate regime)
- Tradier sandbox balance: $100,000 (paper)
- Alpaca paper equity: ~$2,898
- Active trades: 1 (IWM Bull Put, entered Jun 24, exp Jul 24)
- Trade log: 22 entries
- Candidate pool: 7 tickers (SPY, QQQ, XLF, XLI, XLY, TLT, DIA)
- Server timezone: ICT (UTC+7)
- US market overlap: 20:30–04:00 ICT
