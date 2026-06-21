#!/usr/bin/env bash
# Turnkey deploy/update on the Ubuntu server.
# Run FROM the project dir on the server (after rsync/git pull):
#     cd /opt/guardrail && ./deploy.sh
# It creates/refreshes the venv, installs deps, runs the test suite, and prints
# a readiness summary. It does NOT touch your .env or systemd units.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

echo "==> venv"
[[ -d venv ]] || python3 -m venv venv
. venv/bin/activate

echo "==> dependencies"
pip install -q --upgrade pip
pip install -q -r requirements.txt
# live extras: ib_async (IBKR) + anthropic (only if using Anthropic directly)
pip install -q ib_async anthropic || true

echo "==> directories"
mkdir -p data logs

echo "==> tests"
python3 -m pytest -q

echo "==> readiness"
if [[ -f .env ]]; then
  set -a; . .env; set +a
  python3 - <<'PY'
from config import Config
c = Config.load()
def ok(b): return "OK" if b else "MISSING"
print(f"  strategist provider : {c.strategist_provider} ({c.strategist_model})")
print(f"  model key           : {ok(c.openrouter_api_key or c.anthropic_api_key)}")
print(f"  telegram            : {ok(c.telegram_token and c.telegram_chat_id)}")
print(f"  mode / equity       : {c.mode} / ${c.equity:,.0f}")
print(f"  ibkr                : {c.ibkr_host}:{c.ibkr_port} paper_only={c.ibkr_paper_only}")
print(f"  data dir            : {c.data_dir}")
PY
else
  echo "  .env not found — copy .env.example to .env and fill it in."
fi

echo "==> done. Next: confirm IB Gateway/IBC is up, then run a manual session:"
echo "    set -a; . .env; set +a; python3 run_ops_session.py"
