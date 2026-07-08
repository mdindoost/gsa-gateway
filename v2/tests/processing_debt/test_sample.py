# v2/tests/processing_debt/test_sample.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.sample import (
    STRATA_A, STRATA_B, STRATA_C, allocate, junk_filter, pii_filter,
    stratum_of_row, sample_set_a)
from eval.processing_debt.run_pilot import _extract_answer


def test_each_set_strata_sum_to_50():
    for strata in (STRATA_A, STRATA_B, STRATA_C):
        assert sum(n for _, n in strata) == 50


def test_allocate_respects_quota_and_dedup():
    pools = {name: [f"{name} q{i}" for i in range(50)] for name, _ in STRATA_B}
    picked = allocate(pools, STRATA_B, seed=0)
    from collections import Counter
    c = Counter(stratum for _, stratum in picked)
    for name, n in STRATA_B:
        assert c[name] == n
    assert len(set(q for q, _ in picked)) == len(picked)          # no dup questions


def test_allocate_degrades_when_pool_short():
    # a pool smaller than its quota yields only what it has, never raises, never dups
    pools = {name: [] for name, _ in STRATA_B}
    pools["db_router_hit"] = ["only one"]
    picked = allocate(pools, STRATA_B, seed=1)
    assert picked == [("only one", "db_router_hit")]


def test_junk_filter_drops_short_fragments():
    qs = ["hi", "who is dean", "where is the gitc building located exactly"]
    assert junk_filter(qs) == ["who is dean", "where is the gitc building located exactly"]


def test_pii_filter_drops_email_keeps_faculty_name():
    qs = ["contact me at john.doe@njit.edu please", "who is Professor Shantanu Sharma"]
    assert pii_filter(qs) == ["who is Professor Shantanu Sharma"]


def test_stratum_of_row_confidence_bands():
    assert stratum_of_row(95.0, 1) == "answered_hi_conf"
    assert stratum_of_row(40.0, 1) == "answered_lo_conf"
    assert stratum_of_row(0.0, 0) == "deflected"
    assert stratum_of_row(None, 0) == "deflected"


def test_sample_set_a_quotas_with_injected_fetch():
    # fake rows: plenty in each confidence band; identity dedup; no control files → controls empty
    rows = ([(f"hi conf question number {i} about faculty", 90.0, 1) for i in range(40)]
            + [(f"lo conf question number {i} about program", 30.0, 1) for i in range(40)]
            + [(f"deflected question number {i} about xyz", 0.0, 0) for i in range(40)])
    picked = sample_set_a(conn=object(), seed=0,
                          fetch=lambda conn: rows, dedup=lambda qs: qs)
    from collections import Counter
    c = Counter(s for _, s in picked)
    assert c["answered_hi_conf"] == 16 and c["answered_lo_conf"] == 14 and c["deflected"] == 12
    assert len(set(q for q, _ in picked)) == len(picked)          # deduped, unique


def test_extract_answer_strips_header_and_rule():
    raw = ("1. ROUTER ...\n2. POOL ...\n5. FINAL LLM ANSWER\n"
           "────────\n"
           "Hi there! Shantanu Sharma is an Associate Professor.\n")
    assert _extract_answer(raw) == "Hi there! Shantanu Sharma is an Associate Professor."


def test_extract_answer_absent_header_returns_empty():
    assert _extract_answer("no header here at all") == ""


def test_extract_answer_survives_real_trace_format():
    B, X, DIM = "\x1b[1m", "\x1b[0m", "\x1b[2m"
    rule = "─" * 72
    raw = (
        f"{B}QUERY:{X} who is the gsa president\n"
        "  ... other stages ...\n"
        f"\n{B}{rule}\n"
        "5. FINAL LLM ANSWER  (full real pipeline — incl. the WS4 gate)\n"
        f"{rule}{X}\n"
        "  Hi there! The GSA president is Jane Doe.\n"
        "  Reach out at the GSA office for details.\n"
        f"\n  {DIM}[source_note=kb · used_ai=True]{X}\n"
    )
    assert _extract_answer(raw) == (
        "Hi there! The GSA president is Jane Doe.\n"
        "Reach out at the GSA office for details."
    )
