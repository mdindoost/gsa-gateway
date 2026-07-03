#!/usr/bin/env bash
# Kavosh auto-eval harness launcher. Pins the required env BEFORE python imports bot.config.
set -euo pipefail
cd /home/md724/gsa-gateway
export ROUTER_V21=1
export ROUTER_V21_SHADOW=0
export LIVE_ENABLED=0
export ROUTER_V21_SLOT_RECOVERY=0
export PYTHONPATH="$PWD"
PY=".venv/bin/python"
cmd="${1:-run}"; shift || true
case "$cmd" in
  run)    exec "$PY" -m autoeval.harness "$@" ;;
  smoke)  exec "$PY" -m autoeval.harness --smoke --items "${1:-50}" ;;
  status) exec "$PY" -m autoeval.live_cli status ;;
  tail)   exec "$PY" -m autoeval.live_cli tail "${1:-20}" ;;
  *) echo "usage: autoeval.sh {run|smoke [N]|status|tail [N]}"; exit 2 ;;
esac
