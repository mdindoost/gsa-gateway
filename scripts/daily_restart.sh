#!/usr/bin/env bash
# daily_restart.sh — cron-safe nightly bounce of everything this project runs.
#
# WHY: on this 14 GB box the three bot processes + the dashboard accumulate over a day, and Ollama
# holds a 4.2 GB model whenever it has been used recently. A daily bounce returns the machine to a
# known-good baseline. Installed as a crontab entry (see `--install`).
#
# WHAT IT DOES, in order:
#   1. Records RSS for every project process BEFORE (so growth becomes measurable, not guessed).
#   2. Runs scripts/restart.sh — stops + restarts Discord, Telegram, GroupMe and the dashboard.
#   3. Asks Ollama to UNLOAD every loaded model (keep_alive=0 over the API). restart.sh deliberately
#      leaves a healthy Ollama alone (it would need sudo), so the model would otherwise stay resident.
#      This needs no sudo and no service bounce.
#   4. Records RSS AFTER, and appends one summary line to logs/daily_restart.log.
#
# SAFE BY DESIGN:
#   * flock — never runs twice concurrently (a slow restart cannot pile up).
#   * Never uses sudo. If Ollama is DOWN it is left down and reported; restart.sh's sudo path is
#     only reached when Ollama is not responding, and in cron that would fail silently — so we
#     check first and skip the LLM section rather than hang.
#   * Read-only with respect to the databases. No crawl, no embed, no DB write.
#   * Log is rotated at 2 MB (keeps one .1 backup) so it cannot grow without bound.
#
# Usage:
#   bash scripts/daily_restart.sh            # run it now
#   bash scripts/daily_restart.sh --install  # add/refresh the 5am crontab entry
#   bash scripts/daily_restart.sh --dry-run  # show what would happen, change nothing
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$REPO/logs/daily_restart.log"
LOCK="$REPO/.daily_restart.lock"
CRON_LINE="0 5 * * * /usr/bin/flock -n $REPO/.daily_restart.lock $REPO/scripts/daily_restart.sh >> $REPO/logs/daily_restart.log 2>&1"
mkdir -p "$REPO/logs"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

# TWO separate numbers on purpose. llama-server swings by ~4 GB depending on whether a model
# happens to be resident, which completely swamps the signal we actually want: whether the long-lived
# PYTHON processes grow across a day. Logging them apart makes real growth measurable over time.
rss_bots() {      # Discord + Telegram + GroupMe + dashboard (the processes that live all day)
  ps -eo rss,args --no-headers 2>/dev/null \
    | grep -E "bot\.main|run_telegram\.py|run_groupme\.py|local_server\.py" \
    | grep -v grep | awk '{s+=$1} END {printf "%.0f", s/1024}'
}
rss_llama() {     # the Ollama model process, 0 when no model is resident
  ps -eo rss,args --no-headers 2>/dev/null | grep "llama-server" | grep -v grep \
    | awk '{s+=$1} END {printf "%.0f", s/1024}'
}

rotate() {
  [ -f "$LOG" ] || return 0
  local size; size=$(stat -c%s "$LOG" 2>/dev/null || echo 0)
  [ "$size" -gt 2097152 ] && mv -f "$LOG" "$LOG.1"
  return 0
}

# ── --install: register the cron entry (idempotent) ───────────────────────────
if [ "${1:-}" = "--install" ]; then
  current="$(crontab -l 2>/dev/null || true)"
  cleaned="$(printf '%s\n' "$current" | grep -v 'daily_restart.sh' || true)"
  printf '%s\n%s\n%s\n' "$cleaned" "# GSA Gateway: nightly bounce of the bots + unload the Ollama model" "$CRON_LINE" \
    | grep -v '^$' | crontab -
  echo "installed. active GSA entries:"
  crontab -l | grep -A1 "GSA Gateway"
  exit 0
fi

DRY=false
[ "${1:-}" = "--dry-run" ] && DRY=true

rotate
before_bots="$(rss_bots)"; before_llama="$(rss_llama)"
log "=== daily restart starting (bots+dashboard: ${before_bots:-0} MB | llama-server: ${before_llama:-0} MB) ==="

if $DRY; then
  log "DRY RUN — would run scripts/restart.sh, then unload Ollama models. Nothing changed."
  exit 0
fi

# ── 1. bots + dashboard ───────────────────────────────────────────────────────
if bash "$REPO/scripts/restart.sh" >> "$LOG" 2>&1; then
  log "restart.sh: OK"
else
  log "restart.sh: FAILED (exit $?) — see the lines above in this log"
fi

# ── 2. free the Ollama model (no sudo, no service bounce) ─────────────────────
if curl -sf --max-time 5 http://localhost:11434/api/tags >/dev/null 2>&1; then
  loaded="$(curl -sf --max-time 5 http://localhost:11434/api/ps 2>/dev/null \
            | grep -oE '"name":"[^"]+"' | cut -d'"' -f4 | sort -u)"
  if [ -z "$loaded" ]; then
    log "ollama: no model resident — nothing to unload"
  else
    for m in $loaded; do
      curl -sf --max-time 15 http://localhost:11434/api/generate \
        -d "{\"model\":\"$m\",\"keep_alive\":0}" >/dev/null 2>&1 \
        && log "ollama: unloaded $m" || log "ollama: could not unload $m"
    done
  fi
else
  log "ollama: not responding — left alone (starting it needs sudo, which cron cannot supply)"
fi

sleep 5
after_bots="$(rss_bots)"; after_llama="$(rss_llama)"
# The bots line is the one to watch: a fresh start is the baseline, so "before" creeping upward
# day after day is the evidence of a real leak (and how big it is).
log "=== done (bots+dashboard: ${before_bots:-0} -> ${after_bots:-0} MB | llama-server: ${before_llama:-0} -> ${after_llama:-0} MB) ==="
log "    swap in use: $(free -m | awk '/Swap/{print $3"/"$2" MB"}')  |  RAM available: $(free -m | awk '/Mem/{print $7" MB"}')"
