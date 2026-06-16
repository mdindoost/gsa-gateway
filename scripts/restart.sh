#!/usr/bin/env bash
# restart.sh — Stop and restart the GSA Gateway bot + Ollama LLM
# Usage: bash scripts/restart.sh [--no-llm]
#   --no-llm   stop Ollama and start the bot with generation disabled.
#              Saves resources; semantic search degrades to fuzzy/keyword,
#              and /ask answers come without AI-written summaries.

set -euo pipefail

# ── flags ─────────────────────────────────────────────────────────────────────
NO_LLM=false
case " $* " in *" --no-llm "*) NO_LLM=true ;; esac

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC}  $1"; }
fail() { echo -e "  ${RED}✗${NC}  $1"; }
info() { echo -e "  ${BLUE}→${NC}  $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  $1"; }

# stop_all PATTERN NAME — kill EVERY process matching PATTERN before we start a
# new one, so we can never end up with two copies of a bot answering the same
# chat. SIGTERM first, wait up to 10s for a graceful exit, then SIGKILL any
# straggler and confirm none survive.
stop_all() {
    local pattern="$1" name="$2" i
    if pgrep -f "$pattern" > /dev/null 2>&1; then
        info "Stopping existing $name (PIDs: $(pgrep -f "$pattern" | tr '\n' ' '))..."
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
echo "  GSA Gateway Restart — $(date '+%Y-%m-%d %H:%M:%S')"
echo "══════════════════════════════════════════════"

# ── 1. Ollama LLM ─────────────────────────────────────────────────────────────
echo ""
echo "[ Ollama LLM ]"
if [ "$NO_LLM" = true ]; then
    warn "--no-llm: starting WITHOUT the LLM."
    warn "          Generation off; semantic search falls back to fuzzy/keyword."
    export OLLAMA_ENABLED=false                 # bot skips the generation client
    if sudo systemctl stop ollama 2>/dev/null; then
        ok "Ollama stopped (resources freed)"
    else
        warn "Could not stop Ollama via systemctl (it may already be stopped)"
    fi
else
    # Health-check first: a working Ollama is left alone (no needless bounce, no
    # sudo prompt). Only start it if it is actually down.
    if curl -sf --max-time 5 http://localhost:11434/api/tags > /dev/null 2>&1; then
        ok "Ollama already up and responding on :11434"
    else
        info "Ollama not responding — starting it..."
        if sudo systemctl start ollama 2>/dev/null; then
            sleep 3
            if curl -sf --max-time 5 http://localhost:11434/api/tags > /dev/null 2>&1; then
                ok "Ollama is up and responding on :11434"
            else
                fail "Ollama started but API not responding — check: sudo journalctl -u ollama -n 20"
            fi
        else
            fail "Ollama is down and could not be started via systemctl. Start it manually: ollama serve"
        fi
    fi
fi

# ── 2. Stop existing Discord bot ─────────────────────────────────────────────
echo ""
echo "[ Discord Bot ]"
stop_all "python.*bot\.main" "Discord bot"
# Always stop any running dashboard server too (any port) — the bot relaunches it
# as its child on startup, so this clears stale/manual instances and never leaves
# an orphan holding the port.
stop_all "v2/local_server\.py" "Dashboard server"

info "Starting Discord bot..."
nohup .venv/bin/python -m bot.main > /dev/null 2>&1 &
BOT_PID=$!
echo $BOT_PID > /tmp/gsa-gateway-discord.pid
sleep 5

if kill -0 "$BOT_PID" 2>/dev/null; then
    if grep -q "GSA Gateway ready" <(tail -20 gsa_gateway.log 2>/dev/null); then
        ok "Discord bot running (PID $BOT_PID)"
        READY_LINE=$(grep "GSA Gateway ready" gsa_gateway.log | tail -1)
        ok "$READY_LINE"
    else
        warn "Discord bot process up (PID $BOT_PID) — 'ready' not confirmed yet"
        info "Watch logs: tail -f gsa_gateway.log"
    fi
else
    fail "Discord bot failed to start — last 10 log lines:"
    tail -10 gsa_gateway.log 2>/dev/null || true
fi

# Dashboard control plane: the bot launches local_server as its child when
# DASHBOARD_SERVER_ENABLED=true — confirm it came up on :5555.
if grep -qi '^DASHBOARD_SERVER_ENABLED=true' .env 2>/dev/null; then
    info "Waiting for dashboard backend on :5555..."
    for i in $(seq 1 15); do
        curl -sf --max-time 3 http://127.0.0.1:5555/api/health > /dev/null 2>&1 && break
        sleep 1
    done
    if curl -sf --max-time 3 http://127.0.0.1:5555/api/health > /dev/null 2>&1; then
        ok "Dashboard backend up on :5555"
    else
        warn "Dashboard backend not up on :5555 yet — check: tail -f gsa_gateway.log"
    fi
fi

# ── 3. Stop existing Telegram bot ────────────────────────────────────────────
echo ""
echo "[ Telegram Bot ]"
stop_all "python.*run_telegram" "Telegram bot"

info "Starting Telegram bot..."
nohup .venv/bin/python run_telegram.py > /dev/null 2>&1 &
TG_PID=$!
echo $TG_PID > /tmp/gsa-gateway-telegram.pid
sleep 5

if kill -0 "$TG_PID" 2>/dev/null; then
    if grep -q "Telegram bot polling" <(tail -20 telegram_bot.log 2>/dev/null); then
        ok "Telegram bot running (PID $TG_PID)"
    else
        warn "Telegram bot process up (PID $TG_PID) — polling not confirmed yet"
        info "Watch logs: tail -f telegram_bot.log"
    fi
else
    fail "Telegram bot failed to start — last 10 log lines:"
    tail -10 telegram_bot.log 2>/dev/null || true
fi

# ── 4. GroupMe bot (optional) ────────────────────────────────────────────────
echo ""
echo "[ GroupMe Bot ]"
stop_all "python.*run_groupme" "GroupMe bot"
if grep -qi '^GROUPME_ENABLED=true' .env 2>/dev/null; then
    info "Starting GroupMe bot..."
    nohup .venv/bin/python run_groupme.py > /dev/null 2>&1 &
    GM_PID=$!
    echo $GM_PID > /tmp/gsa-gateway-groupme.pid
    sleep 5
    if kill -0 "$GM_PID" 2>/dev/null; then
        if grep -q "GroupMe polling started" <(tail -20 groupme_bot.log 2>/dev/null); then
            ok "GroupMe bot running (PID $GM_PID)"
        else
            warn "GroupMe bot process up (PID $GM_PID) — polling not confirmed yet"
            info "Watch logs: tail -f groupme_bot.log"
        fi
    else
        fail "GroupMe bot failed to start — last 10 log lines:"
        tail -10 groupme_bot.log 2>/dev/null || true
    fi
else
    info "GROUPME_ENABLED is not true in .env — skipping GroupMe bot"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════"
if [ "$NO_LLM" = true ]; then
    echo -e "  ${YELLOW}LLM: OFF${NC}  (started with --no-llm — search uses fuzzy/keyword)"
else
    echo -e "  ${GREEN}LLM: ON${NC}"
fi
echo -e "  ${GREEN}Done.${NC} To watch live logs:"
echo "  tail -f gsa_gateway.log        (Discord)"
echo "  tail -f telegram_bot.log       (Telegram)"
echo "  tail -f groupme_bot.log        (GroupMe, if enabled)"
echo "══════════════════════════════════════════════"
echo ""
