#!/usr/bin/env bash
# Reusable eval: run eval/questions.txt through the REAL bot pipeline (KB + live fallback),
# auto-judge accuracy, and print a coverage + accuracy + gaps report. Edit eval/questions.txt
# to add questions. Args pass through to BOTH eval_run (e.g. --limit 20) and eval_report (the gate).
#
#   bash scripts/eval.sh                              # full run, report only
#   bash scripts/eval.sh --limit 20                  # quick subset
#   bash scripts/eval.sh --min-answered 90 --min-correct 80   # GATE: exit non-zero on regression
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python; [ -x "$PY" ] || PY=python3

echo ">> running questions through the real pipeline..."
# eval_run.py cleans up its OWN analytics rows (hash-scoped, in a finally) — no id-watermark delete
# here, which would wipe real students' questions logged during the run.
$PY scripts/eval_run.py "$@"

echo ">> judging accuracy (local model)..."
$PY scripts/eval_judge.py

$PY scripts/eval_report.py "$@"
