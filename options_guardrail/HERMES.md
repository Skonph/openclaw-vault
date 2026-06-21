# Project Handover & Briefing for Hermes

Welcome, Hermes! This document provides a complete briefing on the **Options Guardrail** project currently running on this Ubuntu server. You can use this context to propose or execute improvement plans later.

---

## ⚠️ Active System Status (June 2026 Migration)

Please note the following active system states to avoid running redundant checks, wasting API queries, or attempting recovery actions:

1. **IBKR Gateway (`ibc-gateway.service`) is Intentionally Stopped & Disabled**:
   - **Reason**: The VPS has been migrated to a lower-spec 2GB RAM Singapore instance. Running Zulu Java, GUI components, and the `Xvfb` headless display consumes ~1GB of RAM, causing severe memory pressure and potential OOM issues on the server.
   - **Status**: Do NOT attempt to restart, enable, or troubleshoot the `ibc-gateway.service`. All trading operations are currently focused strictly on Alpaca (OpenClaw) and Tradier.
   
2. **OpenClaw Cron Logs (`~/openclaw/logs/`) are Currently Empty**:
   - **Reason**: The VPS migration was completed on Sunday while the US markets were closed.
   - **Status**: Do NOT troubleshoot the lack of files in `~/openclaw/logs/`. These log files are redirected during cron execution and will automatically generate starting Monday when the scheduled jobs fire.

3. **Tradier SMA Test-Mode Filter Bug is Patched**:
   - **Status**: The bug where SPY's `$730.00` SMA was applied to all tickers in test mode has been resolved in the latest codebase update.

---


## 🔎 Project Overview
**Options Guardrail** is a production-grade automated options execution and safety system. It manages multi-leg options execution, real-time portfolio marking, and custom marked-equity drawdown kill-switches for risk control.

### Core Architecture
1. **Leg-Level Position Persistence** ([positions.py](file:///Users/SkonP/Downloads/options_guardrail/positions.py)): Stores option execution leg details (strike, expiration, right, ratio) inside the persisted `Position` schema to survive process/daemon restarts.
2. **Decoupled Exit Pipeline** ([pipeline.py](file:///Users/SkonP/Downloads/options_guardrail/pipeline.py)): Option closures are decoupled from the volatile in-memory `plan_registry`. The pipeline passes the `Position` object directly to the executor to reconstruct combo contracts.
3. **Real-time Midpoint Marking** ([market_data.py](file:///Users/SkonP/Downloads/options_guardrail/market_data.py)): Bypasses paid OPRA subscriptions on IBKR by using the **Tradier API** (`TradierMarketData`) to fetch real-time bid/ask spreads for option legs, compute midpoint combo valuations, and calculate net USD P&L.
4. **Marked-Equity Drawdown Kill-Switch** ([state.py](file:///Users/SkonP/Downloads/options_guardrail/state.py) & [exit_monitor.py](file:///Users/SkonP/Downloads/options_guardrail/exit_monitor.py)): Monitors live unrealized drawdown based on marked prices. Instantly triggers a force-close of all positions and halts new entries if daily/weekly drawdown limits are breached.

---

## ⚙️ Server Deployment & Configuration

### Headless IB Gateway Service (`ibc-gateway`)
- **Systemd Service:** `/etc/systemd/system/ibc-gateway.service`
- **Execution Strategy:** Runs `/opt/ibc/scripts/ibcstart.sh` directly in the foreground, wrapped in `xvfb-run` for headless display management (bypasses `xterm` and backgrounding deactivation bugs).
- **Zulu OpenJDK JRE:** Runs using the full JRE bundled with IB Gateway (`17.0.16.0.101-zulu_64`) to support the Swing GUI components headless.
- **Symlink Mappings:** Since the standalone installer puts jars in `/home/guardrail/ibgateway`, we linked the version folder `/home/guardrail/ibgateway/1045` containing symlinks to `jars`, `.install4j`, and `ibgateway.vmoptions`.
- **API Port:** Gateway runs paper trading on port `7497`, resolving to Margin paper account `DUQ548647`.

---

## 🚀 Running Verification & Commands
- **Run Unit Tests:**
  ```bash
  ./deploy.sh
  ```
- **Preflight connectivity check (Tradier, Telegram, OpenRouter):**
  ```bash
  ./run.sh preflight.py --ping
  ```
- **Run Manual Paper Trading Session:**
  ```bash
  set -a; . .env; set +a; python3 run_ops_session.py
  ```

---

## 💡 Potential Areas for Contribution & Improvement

If you are looking to contribute to the improvement plan, here are the key candidate areas:

1. **Option Position Rollers:**
   - Implement intelligent rolling logic for options nearing expiration (e.g., roll delta or credit).
2. **Dashboard Web Interface:**
   - Create a premium Next.js or Vite-based UI (dark mode, glassmorphism) displaying live marked P&L, current positions, and risk metrics, with interactive manual override buttons.
3. **Execution Speed Optimization:**
   - Introduce event-driven execution using WebSockets instead of polling for quicker fills on leg executions.
4. **Fallback Quote Provider:**
   - Integrate a secondary fallback market data provider (e.g. Polygon or Yahoo Finance) in `market_data.py` to handle Tradier rate-limiting or API outages.
5. **Multi-Account Manager:**
   - Extend `config.py` and executors to support routing trades across multiple distinct accounts (e.g., both paper and live setups running concurrently).
