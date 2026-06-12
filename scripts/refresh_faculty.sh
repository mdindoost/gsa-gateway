#!/usr/bin/env bash
# refresh_faculty.sh — THE BUTTON: one-command faculty knowledge refresh.
#
# Run this when faculty pages may have changed (≈once a semester). It does the
# whole pipeline in one shot, no manual steps:
#   1. auto-backup the DB (verified, un-skippable — built into --commit)
#   2. crawl every CS faculty profile on people.njit.edu
#   3. extract everything (publications, awards, research areas, experience, …)
#   4. generate each professor's grounded overview (local LLM)
#   5. reconcile (only what changed is updated/versioned) + embed
#   6. write the exact diff to logs/ingest_changes.log
#
# Nothing to manipulate or check by hand — read logs/ingest_changes.log afterwards
# to see exactly what changed. The live bot picks up the new data immediately
# (it reads the DB per query); no restart needed.
#
# Usage:  bash scripts/refresh_faculty.sh [N]      # N = how many profiles (default 80 = all)
set -euo pipefail
cd "$(dirname "$0")/.."

N="${1:-80}"                       # default covers the whole CS faculty list
DEFAULT_ORG=5                      # CS — fallback only for pages with no dept label

echo "════════════════════════════════════════════════════"
echo "  Faculty KB refresh — $(date '+%Y-%m-%d %H:%M:%S')"
echo "  (backup → crawl → extract → overview → embed)"
echo "════════════════════════════════════════════════════"

# Ollama must be up for overviews + embeddings.
if ! curl -sf --max-time 5 http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "  ✗ Ollama not responding on :11434 — start it first (overviews/embeddings need it)." >&2
    exit 1
fi

.venv/bin/python scripts/ingest_faculty.py \
    --limit "$N" --overview --commit --default-org-id "$DEFAULT_ORG"

echo
echo "  Done. What changed → logs/ingest_changes.log"
echo "  Backups → .backups/  (last 10 auto-backups kept)"
