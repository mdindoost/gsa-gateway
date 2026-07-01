"""Pure-helper tests for the Qwen prefix-wiring dry-run (scripts/qwen_dryrun.py)."""
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.qwen_dryrun import rank_of, summarize


def test_rank_of_target_is_one_when_identical():
    docs = [[1.0, 0.0], [0.0, 1.0], [0.7, 0.7]]
    # query identical to doc 1 -> doc 1 is the top hit (rank 1)
    assert rank_of([0.0, 1.0], docs, target_idx=1) == 1


def test_rank_of_counts_better_scoring_docs():
    docs = [[1.0, 0.0], [0.6, 0.8], [0.0, 1.0]]
    # query [0,1]: dots = 0.0, 0.8, 1.0 -> target 1 (0.8) is 2nd best
    assert rank_of([0.0, 1.0], docs, target_idx=1) == 2


def test_summarize_reports_prefixed_vs_raw():
    rows = [
        {"target": "A", "rank_with": 1, "rank_without": 3},
        {"target": "B", "rank_with": 1, "rank_without": 1},
        {"target": "C", "rank_with": 2, "rank_without": 5},
    ]
    s = summarize(rows)
    assert s["n"] == 3
    assert s["top1_with"] == 2          # A, B rank 1 with prefix
    assert s["top1_without"] == 1       # only B rank 1 without prefix
    assert s["mean_rank_with"] < s["mean_rank_without"]   # prefix helps on average
