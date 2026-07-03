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
PIDFILE="$PWD/logs/autoeval.pid"
LOGFILE="$PWD/logs/autoeval.out"
cmd="${1:-run}"; shift || true
case "$cmd" in
  # single finite batch of N items (default 50 for run), then exits
  run)    exec "$PY" -m autoeval.harness "$@" ;;
  smoke)  exec "$PY" -m autoeval.harness --smoke --items "${1:-50}" ;;

  # continuous background loop: N items PER BATCH (default 200), runs until `stop`
  start)
    mkdir -p "$PWD/logs"
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "already running (pid $(cat "$PIDFILE")). run 'autoeval.sh stop' first."; exit 1
    fi
    items="${1:-200}"
    # `setsid --fork` runs the supervisor loop in its OWN session/process-group (its pgid ==
    # the loop's pid), so `stop` can take down the whole tree — the loop AND the batch it is
    # supervising — with one group signal, and the loop can't respawn a killed batch. The loop
    # writes its own $$ to the pidfile (setsid --fork's parent exits, so `$!` is not the loop).
    # `|| sleep 60` keeps a transient batch failure (e.g. Ollama blip) from spinning hot.
    setsid --fork bash -c \
      'echo $$ > "'"$PIDFILE"'"; while true; do "'"$PWD"'/scripts/autoeval.sh" run --items '"$items"' || sleep 60; done' \
      >> "$LOGFILE" 2>&1
    sleep 0.4
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "started continuous eval — pid $(cat "$PIDFILE"), ${items} items/batch → $LOGFILE"
      echo "watch: bash scripts/autoeval.sh status   |   stop: bash scripts/autoeval.sh stop"
    else
      echo "failed to start (see $LOGFILE)"; exit 1
    fi
    ;;
  stop)
    if [ ! -f "$PIDFILE" ]; then echo "not running (no pidfile at $PIDFILE)."; exit 0; fi
    pid="$(cat "$PIDFILE")"
    if kill -0 "$pid" 2>/dev/null; then
      # kill the whole process group (leading '-' before the pid) so the loop can't
      # restart the batch it's supervising; fall back to a plain kill if that fails.
      kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
      echo "stopped (pid group $pid)."
    else
      echo "process $pid not alive; cleaning up stale pidfile."
    fi
    rm -f "$PIDFILE"
    ;;

  status)   exec "$PY" -m autoeval.live_cli status ;;
  tail)     exec "$PY" -m autoeval.live_cli tail "${1:-20}" ;;
  failures) exec "$PY" -m autoeval.live_cli failures ;;
  *) echo "usage: autoeval.sh {start [N/batch] | stop | run [--items N] | smoke [N] | status | tail [N] | failures}"; exit 2 ;;
esac
