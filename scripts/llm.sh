#!/usr/bin/env bash
# llm.sh — turn the local LLM (Ollama) on or off WITHOUT touching the bots.
#
# The bots keep running either way:
#   • OFF → structured/entity answers (dean, officers, faculty, "list the Michaels",
#           "X's research") still work at FULL quality — they're pure SQL, no LLM.
#           Open-ended RAG questions degrade to keyword search with no AI-written prose
#           (the bot returns the retrieved facts / a deflection). GPU is freed.
#   • ON  → full AI-composed answers + semantic (embedding) retrieval resume.
#
# Usage:
#   bash scripts/llm.sh off       # stop the LLM, free the GPU (bots keep serving)
#   bash scripts/llm.sh on        # start the LLM again
#   bash scripts/llm.sh status    # show current state + loaded models
#
# No bot restart needed — the running bot detects Ollama up/down per request and
# falls back on its own. (For a clean "LLM-off" boot instead, use: restart.sh --no-llm)

set -euo pipefail

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC}  $1"; }
fail() { echo -e "  ${RED}✗${NC}  $1"; }
info() { echo -e "  ${BLUE}→${NC}  $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  $1"; }

API="http://localhost:11434/api/tags"

llm_up() { curl -sf --max-time 5 "$API" > /dev/null 2>&1; }

show_status() {
    if llm_up; then
        ok "LLM is ON (Ollama responding on :11434)"
        local loaded
        loaded=$(curl -sf --max-time 5 http://localhost:11434/api/ps 2>/dev/null \
            | python3 -c "import sys,json;[print('     -',m['name']) for m in json.load(sys.stdin).get('models',[])]" 2>/dev/null || true)
        if [[ -n "$loaded" ]]; then
            info "models loaded in VRAM:"; echo "$loaded"
        else
            info "no model currently loaded in VRAM (loads on first request)"
        fi
    else
        warn "LLM is OFF (Ollama not responding) — bots run in structured-only / keyword mode"
    fi
}

case "${1:-status}" in
    off)
        echo "Stopping the LLM (bots keep running)…"
        if ! llm_up; then warn "Ollama already stopped."; exit 0; fi
        if sudo systemctl stop ollama 2>/dev/null; then
            sleep 2
            llm_up && fail "Ollama still responding — stop may have failed" || ok "LLM stopped, GPU freed"
        else
            warn "systemctl stop failed; trying to unload the model from VRAM instead…"
            ollama stop "$(grep -E '^OLLAMA_MODEL=' .env 2>/dev/null | cut -d= -f2 | tr -d '\"')" 2>/dev/null || true
        fi
        echo ""
        info "Bots still serve: structured answers FULL quality; RAG → keyword + raw facts (no AI prose)."
        ;;
    on)
        echo "Starting the LLM…"
        if llm_up; then ok "Ollama already running."; show_status; exit 0; fi
        if sudo systemctl start ollama 2>/dev/null; then
            for _ in $(seq 1 10); do llm_up && break; sleep 1; done
            llm_up && ok "LLM is back ON" || fail "Ollama started but API not responding — check: sudo journalctl -u ollama -n 20"
        else
            fail "Could not start Ollama via systemctl. Start it manually: ollama serve"
        fi
        ;;
    status)
        show_status
        ;;
    *)
        echo "Usage: bash scripts/llm.sh [on|off|status]"; exit 1
        ;;
esac
