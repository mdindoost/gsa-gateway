#!/usr/bin/env bash
# X-ray a question through the whole answer pipeline:
#   router -> fused RRF pool -> cross-encoder reranked (with CE scores) -> final top-5
#   -> heads-up -> (optional) LLM answer.
#
# Usage:
#   bash scripts/ask.sh "who do I contact about a billing hold"
#   bash scripts/ask.sh "how do I apply for OPT" --answer      # also generate the real answer
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python; [ -x "$PY" ] || PY=python3
exec "$PY" scripts/trace_query.py "$@"
