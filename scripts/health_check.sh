#!/usr/bin/env bash
# health_check.sh — Check GSA Gateway services status
# Usage:
#   bash scripts/health_check.sh          # check only
#   bash scripts/health_check.sh --fix    # check + auto-restart on issues

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

FIX=false
[[ "${1:-}" == "--fix" ]] && FIX=true

ISSUES=0

ok()   { echo -e "  ${GREEN}✓${NC}  $1"; }
fail() { echo -e "  ${RED}✗${NC}  $1"; ISSUES=$((ISSUES + 1)); }
warn() { echo -e "  ${YELLOW}⚠${NC}  $1"; ISSUES=$((ISSUES + 1)); }
info() { echo -e "  ${BLUE}→${NC}  $1"; }

cd "$(dirname "$0")/.."
LOG_FILE="$(pwd)/gsa_gateway.log"
ENV_FILE="$(pwd)/.env"

echo ""
echo "══════════════════════════════════════════════"
echo "  GSA Gateway Health Check — $(date '+%Y-%m-%d %H:%M:%S')"
echo "══════════════════════════════════════════════"

# ── 1. Ollama LLM ─────────────────────────────────────────────────────────────
echo ""
echo "[ Ollama LLM ]"

if systemctl is-active --quiet ollama 2>/dev/null; then
    ok "ollama.service is running"
else
    fail "ollama.service is NOT running"
    if $FIX; then
        info "Restarting Ollama..."
        sudo systemctl restart ollama && sleep 3
        systemctl is-active --quiet ollama && ok "Ollama restarted" || fail "Ollama failed to restart"
    else
        info "Fix: bash scripts/restart.sh"
    fi
fi

if curl -sf --max-time 5 http://localhost:11434/api/tags > /dev/null 2>&1; then
    ok "Ollama API responding on :11434"
else
    fail "Ollama API not responding on :11434"
fi

MODEL=$(grep -E '^OLLAMA_MODEL=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"')
MODEL="${MODEL:-llama3.1:8b}"
if curl -sf --max-time 5 http://localhost:11434/api/tags 2>/dev/null | grep -q "${MODEL%%:*}"; then
    ok "Model '$MODEL' is loaded"
else
    warn "Model '$MODEL' not found — run: ollama pull $MODEL"
fi

# ── 2. Discord Bot ────────────────────────────────────────────────────────────
echo ""
echo "[ Discord Bot ]"

BOT_PID=$(pgrep -f "python.*bot.main" 2>/dev/null | head -1 || true)

if [[ -n "$BOT_PID" ]]; then
    ok "Discord bot running (PID $BOT_PID)"
    if [[ -f /proc/$BOT_PID/stat ]]; then
        START_TICKS=$(awk '{print $22}' /proc/$BOT_PID/stat 2>/dev/null || echo "")
        if [[ -n "$START_TICKS" ]]; then
            CLK=$(getconf CLK_TCK 2>/dev/null || echo 100)
            BOOT=$(awk '/btime/{print $2}' /proc/stat)
            START_EPOCH=$(( BOOT + START_TICKS / CLK ))
            UPTIME_SEC=$(( $(date +%s) - START_EPOCH ))
            UPTIME_MIN=$(( UPTIME_SEC / 60 ))
            UPTIME_HR=$(( UPTIME_MIN / 60 ))
            if (( UPTIME_HR > 0 )); then
                info "Uptime: ${UPTIME_HR}h $((UPTIME_MIN % 60))m"
            else
                info "Uptime: ${UPTIME_MIN}m"
            fi
        fi
    fi
    MEM_KB=$(grep VmRSS /proc/$BOT_PID/status 2>/dev/null | awk '{print $2}' || echo "")
    if [[ -n "$MEM_KB" ]]; then
        MEM_MB=$(( MEM_KB / 1024 ))
        if (( MEM_MB > 500 )); then
            warn "Memory high: ${MEM_MB} MB"
        else
            ok "Memory: ${MEM_MB} MB"
        fi
    fi
else
    fail "Discord bot is NOT running"
    if $FIX; then
        info "Starting Discord bot..."
        nohup .venv/bin/python -m bot.main > /dev/null 2>&1 &
        sleep 5
        NEW_PID=$(pgrep -f "python.*bot.main" 2>/dev/null | head -1 || true)
        if [[ -n "$NEW_PID" ]]; then
            ok "Discord bot started (PID $NEW_PID)"
        else
            fail "Discord bot failed to start — check: tail -20 gsa_gateway.log"
        fi
    else
        info "Fix: bash scripts/restart.sh"
    fi
fi

# ── 3. Telegram Bot ───────────────────────────────────────────────────────────
echo ""
echo "[ Telegram Bot ]"

TG_PID=$(pgrep -f "python.*run_telegram" 2>/dev/null | head -1 || true)

if [[ -n "$TG_PID" ]]; then
    ok "Telegram bot running (PID $TG_PID)"
    MEM_KB=$(grep VmRSS /proc/$TG_PID/status 2>/dev/null | awk '{print $2}' || echo "")
    if [[ -n "$MEM_KB" ]]; then
        MEM_MB=$(( MEM_KB / 1024 ))
        ok "Memory: ${MEM_MB} MB"
    fi
else
    fail "Telegram bot is NOT running"
    if $FIX; then
        info "Starting Telegram bot..."
        nohup .venv/bin/python run_telegram.py > /dev/null 2>&1 &
        sleep 5
        NEW_PID=$(pgrep -f "python.*run_telegram" 2>/dev/null | head -1 || true)
        if [[ -n "$NEW_PID" ]]; then
            ok "Telegram bot started (PID $NEW_PID)"
        else
            fail "Telegram bot failed to start — check: tail -20 telegram_bot.log"
        fi
    else
        info "Fix: bash scripts/restart.sh"
    fi
fi

# ── 4. Log checks (since last startup) ───────────────────────────────────────
echo ""
echo "[ Log & Activity ]"

RECENT_LOGS=""
if [[ -f "$LOG_FILE" ]]; then
    STARTUP_LINE=$(grep -n "GSA Gateway ready" "$LOG_FILE" 2>/dev/null | tail -1 | cut -d: -f1)
    if [[ -n "$STARTUP_LINE" ]]; then
        RECENT_LOGS=$(tail -n "+${STARTUP_LINE}" "$LOG_FILE")
    else
        RECENT_LOGS=$(tail -300 "$LOG_FILE")
    fi
fi

# Discord gateway
if echo "$RECENT_LOGS" | grep -qE "connected to Gateway|RESUMED session|GSA Gateway ready"; then
    LAST_CONN=$(echo "$RECENT_LOGS" | grep -E "connected to Gateway|RESUMED session|GSA Gateway ready" | tail -1 | awk '{print $1, $2}')
    ok "Discord gateway connected  ($LAST_CONN)"
else
    fail "No Discord connection in recent logs"
fi

# Last activity
LAST_LINE=$(echo "$RECENT_LOGS" | grep -v "RESUMED\|connected to Gateway" | tail -1)
if [[ -n "$LAST_LINE" ]]; then
    LAST_TIME=$(echo "$LAST_LINE" | awk '{print $1, $2}')
    ok "Last activity: $LAST_TIME"
fi

# ChromaDB errors
if echo "$RECENT_LOGS" | grep -q "NotFoundError"; then
    warn "ChromaDB NotFoundError in logs (rebuild index or restart bot)"
else
    ok "No ChromaDB errors"
fi

# MathCafe last post
LAST_MATHCAFE=$(grep "MathCafe posted\|MathCafe daily post completed" "$LOG_FILE" 2>/dev/null | tail -1 || true)
if [[ -n "$LAST_MATHCAFE" ]]; then
    MC_TIME=$(echo "$LAST_MATHCAFE" | awk '{print $1, $2}')
    ok "MathCafe last posted: $MC_TIME"
else
    warn "MathCafe has never posted successfully — use /admin_mathcafe_post_now in Discord"
fi

# Errors since last startup
ERROR_COUNT=$(echo "$RECENT_LOGS" | grep -c "\[ERROR\]" || true)
if (( ERROR_COUNT > 0 )); then
    warn "$ERROR_COUNT error(s) since last startup"
    echo "$RECENT_LOGS" | grep "\[ERROR\]" | tail -3 | while IFS= read -r line; do
        info "  $line"
    done
else
    ok "No errors since last startup"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════"
if (( ISSUES == 0 )); then
    echo -e "  ${GREEN}All systems OK${NC}"
else
    echo -e "  ${YELLOW}${ISSUES} issue(s) found${NC}"
    if ! $FIX; then
        echo ""
        echo "  Auto-fix:  bash scripts/health_check.sh --fix"
        echo "  Full restart: bash scripts/restart.sh"
    fi
fi
echo "══════════════════════════════════════════════"
echo ""
