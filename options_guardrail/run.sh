#!/usr/bin/env bash
# Cron/systemd wrapper: loads .env then runs a python script in the venv.
# Usage:  run.sh strategist_run.py --context data/context.json
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# load environment (.env in the project dir)
if [[ -f "$HERE/.env" ]]; then
  set -a; . "$HERE/.env"; set +a
fi

exec "$HERE/venv/bin/python" "$HERE/$@"
