#!/usr/bin/env bash
# restart.sh — Stop and restart the GSA Gateway bot + Ollama LLM
# Usage: bash scripts/restart.sh

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC}  $1"; }
fail() { echo -e "  ${RED}✗${NC}  $1"; }
info() { echo -e "  ${BLUE}→${NC}  $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  $1"; }

cd "$(dirname "$0")/.."

echo ""
echo "══════════════════════════════════════════════"
echo "  GSA Gateway Restart — $(date '+%Y-%m-%d %H:%M:%S')"
echo "══════════════════════════════════════════════"

# ── 1. Restart Ollama ─────────────────────────────────────────────────────────
echo ""
echo "[ Ollama LLM ]"
info "Restarting ollama..."
if sudo systemctl restart ollama 2>/dev/null; then
    sleep 3
    if curl -sf --max-time 5 http://localhost:11434/api/tags > /dev/null 2>&1; then
        ok "Ollama is up and responding on :11434"
    else
        fail "Ollama restarted but API not responding — check: sudo journalctl -u ollama -n 20"
    fi
else
    warn "Could not restart ollama via systemctl — trying direct check..."
    if curl -sf --max-time 5 http://localhost:11434/api/tags > /dev/null 2>&1; then
        ok "Ollama API already responding on :11434"
    else
        fail "Ollama is not reachable. Start it manually: ollama serve"
    fi
fi

# ── 2. Stop existing Discord bot ─────────────────────────────────────────────
echo ""
echo "[ Discord Bot ]"
if pgrep -f "python.*bot.main" > /dev/null 2>&1; then
    info "Stopping existing Discord bot..."
    pkill -f "python.*bot.main" || true
    sleep 2
    ok "Discord bot stopped"
else
    info "Discord bot was not running"
fi

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

# ── 3. Stop existing Telegram bot ────────────────────────────────────────────
echo ""
echo "[ Telegram Bot ]"
if pgrep -f "python.*run_telegram" > /dev/null 2>&1; then
    info "Stopping existing Telegram bot..."
    pkill -f "python.*run_telegram" || true
    sleep 2
    ok "Telegram bot stopped"
else
    info "Telegram bot was not running"
fi

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

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════"
echo -e "  ${GREEN}Done.${NC} To watch live logs:"
echo "  tail -f gsa_gateway.log        (Discord)"
echo "  tail -f telegram_bot.log       (Telegram)"
echo "══════════════════════════════════════════════"
echo ""
