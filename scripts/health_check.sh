#!/usr/bin/env bash
# health_check.sh — Check GSA Gateway services and auto-fix common issues
# Usage:
#   bash scripts/health_check.sh          # check only, report issues
#   bash scripts/health_check.sh --fix    # check + auto-restart on issues

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

FIX=false
[[ "${1:-}" == "--fix" ]] && FIX=true

ISSUES=0

# ── Helpers ───────────────────────────────────────────────────────────────────
ok()    { echo -e "  ${GREEN}✓${NC}  $1"; }
warn()  { echo -e "  ${YELLOW}⚠${NC}  $1"; ISSUES=$((ISSUES + 1)); }
fail()  { echo -e "  ${RED}✗${NC}  $1"; ISSUES=$((ISSUES + 1)); }
info()  { echo -e "  ${BLUE}→${NC}  $1"; }

restart_svc() {
  local svc="$1"
  if $FIX; then
    info "Restarting $svc..."
    sudo systemctl restart "$svc"
    sleep 4
    if systemctl is-active --quiet "$svc"; then
      ok "$svc restarted successfully"
    else
      fail "$svc failed to restart — check: sudo journalctl -u $svc -n 30"
    fi
  else
    info "Run with --fix to auto-restart"
  fi
}

echo ""
echo "══════════════════════════════════════════════"
echo "  GSA Gateway Health Check — $(date '+%Y-%m-%d %H:%M:%S')"
echo "══════════════════════════════════════════════"

# ── 1. Ollama service ─────────────────────────────────────────────────────────
echo ""
echo "[ Ollama ]"

if systemctl is-active --quiet ollama; then
  ok "ollama.service is running"
else
  fail "ollama.service is NOT running"
  restart_svc ollama
fi

# ── 2. Ollama API reachable ───────────────────────────────────────────────────
if curl -sf --max-time 5 http://localhost:11434/api/tags > /dev/null 2>&1; then
  ok "Ollama API responding on :11434"
else
  warn "Ollama API not responding on :11434"
  restart_svc ollama
fi

# ── 3. LLM model available ────────────────────────────────────────────────────
ENV_FILE="$(dirname "$0")/../.env"
MODEL=$(grep -E '^OLLAMA_MODEL=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "llama3.1:8b")
MODEL="${MODEL:-llama3.1:8b}"

if curl -sf --max-time 5 http://localhost:11434/api/tags 2>/dev/null | grep -q "${MODEL%%:*}"; then
  ok "Model '$MODEL' is available"
else
  warn "Model '$MODEL' not found in Ollama"
  info "Fix: ollama pull $MODEL"
fi

# ── 4. Bot service ────────────────────────────────────────────────────────────
echo ""
echo "[ GSA Gateway Bot ]"

if systemctl is-active --quiet gsa-gateway; then
  ok "gsa-gateway.service is running"
  SINCE=$(systemctl show gsa-gateway --property=ActiveEnterTimestamp | cut -d= -f2)
  info "Running since: $SINCE"
else
  fail "gsa-gateway.service is NOT running"
  restart_svc gsa-gateway
fi

# ── Log file — only lines since the last bot startup ─────────────────────────
LOG_FILE="$(cd "$(dirname "$0")/.." && pwd)/gsa_gateway.log"
RECENT_LOGS=""
if [[ -f "$LOG_FILE" ]]; then
  # Find the line number of the last startup marker so we ignore old errors
  STARTUP_LINE=$(grep -n "GSA Gateway ready" "$LOG_FILE" 2>/dev/null | tail -1 | cut -d: -f1)
  if [[ -n "$STARTUP_LINE" ]]; then
    RECENT_LOGS=$(tail -n "+${STARTUP_LINE}" "$LOG_FILE" 2>/dev/null)
  else
    RECENT_LOGS=$(tail -300 "$LOG_FILE" 2>/dev/null)
  fi
fi

# ── 5. ChromaDB stale collection error ───────────────────────────────────────
if echo "$RECENT_LOGS" | grep -q "NotFoundError"; then
  warn "ChromaDB NotFoundError in recent logs (stale collection — index rebuilt while bot was running)"
  restart_svc gsa-gateway
else
  ok "No ChromaDB errors in recent logs"
fi

# ── 6. Discord gateway connected ──────────────────────────────────────────────
if echo "$RECENT_LOGS" | grep -qE "connected to Gateway|RESUMED session|GSA Gateway ready"; then
  ok "Discord gateway connected"
else
  warn "No Discord connection event found in recent logs"
  restart_svc gsa-gateway
fi

# ── 7. Memory usage ───────────────────────────────────────────────────────────
MEM_RAW=$(systemctl show gsa-gateway --property=MemoryCurrent 2>/dev/null | cut -d= -f2)
if [[ "$MEM_RAW" =~ ^[0-9]+$ ]] && [[ "$MEM_RAW" != "18446744073709551615" ]]; then
  MEM_MB=$((MEM_RAW / 1024 / 1024))
  if [ "$MEM_MB" -gt 500 ]; then
    warn "Bot memory usage high: ${MEM_MB} MB — consider restarting"
  else
    ok "Bot memory: ${MEM_MB} MB"
  fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════"
if [ "$ISSUES" -eq 0 ]; then
  echo -e "  ${GREEN}All systems OK${NC}"
else
  echo -e "  ${YELLOW}${ISSUES} issue(s) found${NC}"
  if ! $FIX; then
    echo ""
    echo "  Auto-fix: bash scripts/health_check.sh --fix"
  fi
fi
echo "══════════════════════════════════════════════"
echo ""
