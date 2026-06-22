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
PY=.venv/bin/python

# watermark so we can remove the eval's logged questions from analytics afterward
WM=$($PY -c "from bot.config import config; from bot.services.database import Database; d=Database(config.database_path); d.connect(); print(d.conn.execute('SELECT COALESCE(MAX(id),0) FROM questions').fetchone()[0]); d.close()")

echo ">> running questions through the real pipeline..."
$PY scripts/eval_run.py "$@"

echo ">> judging accuracy (local model)..."
$PY scripts/eval_judge.py

echo ">> removing eval questions from analytics (id > $WM)..."
$PY -c "from bot.config import config; from bot.services.database import Database; d=Database(config.database_path); d.connect(); d.conn.execute('DELETE FROM questions WHERE id > $WM'); d.conn.commit(); d.close()"

$PY scripts/eval_report.py "$@"
