# CRON TIMEOUT PREVENTION & DECOUPLED VERIFICATION PROTOCOL
**Directive for Hermes (CEO/COO) and Anna (VP Market Intelligence)**

## 1. Executive Summary & Root Cause
During session execution, crons that combined heavy LLM model calls with multi-turn tool loops (e.g. `W27 Battle Plan — Mon Close Verification`) suffered from **`cron: job execution timed out`** failures. 

To guarantee 100% execution reliability, all cron jobs are now governed by the **Decoupled Verification Architecture**.

---

## 2. The 3 Non-Negotiable Cron Architecture Rules

### Rule 1: Zero-Token Script Verification (`no_agent: true`)
- All position checks, trade verification, and fallback clearance scripts MUST run as headless Python scripts ([closed_loop_executor.py](file:///Users/SkonP/AI_Prompt/Obsidient/SkonVault/shared/closed_loop_executor.py)).
- **Execution Speed**: < 0.5 seconds.
- **Zero Token Cost / Zero Timeout Risk**: Does not invoke LLM model calls during verification.
- **Alerting**: Stays 100% silent on success; alerts `bridge.db` only if a position is unexpectedly still open.

### Rule 2: Decouple Verification from Report Generation
- **Phase 1 (Verification at 20:30 ICT)**: Python script audits Alpaca positions, updates `intent_graph.json` to `VERIFIED_SUCCESS`, and exits immediately.
- **Phase 2 (Async Reporting at 20:35 ICT)**: Anna reads `intent_graph.json` from disk and posts her Telegram report asynchronously without holding the execution cron open.

### Rule 3: 180s Timeout Floor for Strategic Reasoning Jobs
- Any cron job requiring multi-turn LLM reasoning (like Anna's 19:30 ICT Flowmaster Brief) must have its timeout explicit threshold set to **180 seconds** (`"timeout_seconds": 180`) to accommodate TokenHub latency spikes.
