#!/usr/bin/env bash
# shutdown.sh — Stop the GSA Gateway bots (Discord + dashboard + Telegram) and Ollama.
# Usage: bash scripts/shutdown.sh [--keep-llm]
#   --keep-llm   leave Ollama running (only stop the bots + dashboard).
#
# Mirrors the stop half of restart.sh: SIGTERM first, wait up to 10s for a graceful
# exit, then SIGKILL any straggler and confirm none survive.

set -euo pipefail

# ── flags ─────────────────────────────────────────────────────────────────────
KEEP_LLM=false
case " $* " in *" --keep-llm "*) KEEP_LLM=true ;; esac

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC}  $1"; }
fail() { echo -e "  ${RED}✗${NC}  $1"; }
info() { echo -e "  ${BLUE}→${NC}  $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  $1"; }

# stop_all PATTERN NAME — kill EVERY process matching PATTERN. SIGTERM first, wait
# up to 10s for a graceful exit, then SIGKILL any straggler and confirm none survive.
stop_all() {
    local pattern="$1" name="$2" i
    if pgrep -f "$pattern" > /dev/null 2>&1; then
        info "Stopping $name (PIDs: $(pgrep -f "$pattern" | tr '\n' ' '))..."
        pkill -TERM -f "$pattern" 2>/dev/null || true
        for i in $(seq 1 10); do
            pgrep -f "$pattern" > /dev/null 2>&1 || break
            sleep 1
        done
        if pgrep -f "$pattern" > /dev/null 2>&1; then
            warn "$name did not stop gracefully — forcing SIGKILL"
            pkill -KILL -f "$pattern" 2>/dev/null || true
            sleep 1
        fi
        if pgrep -f "$pattern" > /dev/null 2>&1; then
            fail "$name STILL running after SIGKILL: $(pgrep -f "$pattern" | tr '\n' ' ')"
        else
            ok "$name stopped (all instances)"
        fi
    else
        info "$name was not running"
    fi
}

cd "$(dirname "$0")/.."

echo ""
echo "══════════════════════════════════════════════"
echo "  GSA Gateway Shutdown — $(date '+%Y-%m-%d %H:%M:%S')"
echo "══════════════════════════════════════════════"

# ── 1. Discord bot + dashboard child ─────────────────────────────────────────
echo ""
echo "[ Discord Bot ]"
stop_all "python.*bot\.main" "Discord bot"
# The dashboard server is launched as the bot's child; stop any instance (any port)
# so we never leave an orphan holding :5555.
stop_all "v2/local_server\.py" "Dashboard server"
rm -f /tmp/gsa-gateway-discord.pid

# ── 2. Telegram bot ──────────────────────────────────────────────────────────
echo ""
echo "[ Telegram Bot ]"
stop_all "python.*run_telegram" "Telegram bot"
rm -f /tmp/gsa-gateway-telegram.pid

# ── 3. Ollama LLM ────────────────────────────────────────────────────────────
echo ""
echo "[ Ollama LLM ]"
if [ "$KEEP_LLM" = true ]; then
    info "--keep-llm: leaving Ollama running"
else
    if curl -sf --max-time 5 http://localhost:11434/api/tags > /dev/null 2>&1; then
        if sudo systemctl stop ollama 2>/dev/null; then
            ok "Ollama stopped (resources freed)"
        else
            warn "Could not stop Ollama via systemctl — stop it manually if needed"
        fi
    else
        info "Ollama was not responding (already stopped)"
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════"
echo -e "  ${GREEN}Shutdown complete.${NC}"
echo "  Restart with: bash scripts/restart.sh"
echo "══════════════════════════════════════════════"
echo ""
