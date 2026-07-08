# v2/tests/processing_debt/test_adjudicate.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.types import FactRecord, PresenceResult
from eval.processing_debt.adjudicate import (
    fact_id, cohen_kappa, emit_csv, ingest_labels, machine_decisions, paired, nugget_quality)


def _pres(present=True):
    return PresenceResult(present, [], [])


def _rec(q, text, *, vital=True, in_answer=False, present=True, cls="OWNED_NOT_SURFACED",
         stage="COMPOSE", guard="supported"):
    return FactRecord(q, "", text, vital, guard, in_answer, _pres(present), cls, stage, q)


# --- cohen_kappa: unchanged math (original 3 tests still pass) ---
def test_kappa_perfect_agreement():
    assert round(cohen_kappa([True, False, True, False], [True, False, True, False]), 3) == 1.0

def test_kappa_chance_agreement_near_zero():
    assert abs(cohen_kappa([True, True, False, False], [True, False, True, False])) < 0.34

def test_kappa_handles_all_same_class():
    assert cohen_kappa([True, True], [True, True]) == 1.0


# --- fact_id: stable + distinguishes question and text ---
def test_fact_id_stable_and_distinct():
    a = fact_id("who is X", "X is a professor")
    assert a == fact_id("who is X", "X is a professor")          # stable
    assert a != fact_id("who is Y", "X is a professor")          # question matters
    assert a != fact_id("who is X", "X is a lecturer")           # text matters
    assert len(a) == 40 and all(c in "0123456789abcdef" for c in a)


# --- emit_csv: fact_id column + DROPPED_ORACLE included + guard/nugget human cols (B1 + R8) ---
def test_emit_csv_includes_key_dropped_and_human_cols(tmp_path):
    recs = [_rec("q1", "vital fact"),
            _rec("q1", "dropped fact", cls="DROPPED_ORACLE", stage=None, guard="unsupported")]
    p = tmp_path / "adj.csv"
    emit_csv(recs, str(p))
    import csv as _csv
    with open(p) as f:
        rows = list(_csv.DictReader(f))
    assert len(rows) == 2                                        # DROPPED_ORACLE row present (B1)
    assert rows[0]["fact_id"] == fact_id("q1", "vital fact")     # stable key emitted
    for col in ("human_guard_ok", "human_nugget_ok", "human_missing_nuggets",
                "human_in_answer", "human_presence"):
        assert col in rows[0] and rows[0][col] == ""             # blank for the human


def test_emit_csv_audit_columns_low_conf_and_judge(tmp_path):
    lc = FactRecord("q1", "", "low-conf fact", True, "supported", False,
                    PresenceResult(False, ["fts_probe"], [], low_conf=True, max_score=0.42),
                    "NOT_OWNED", None, "q1", judge_id="nli", max_score=0.42)
    p = tmp_path / "adj.csv"
    emit_csv([lc], str(p))
    import csv as _csv
    row = list(_csv.DictReader(open(p)))[0]
    assert row["machine_low_conf"] == "1" and row["judge_id"] == "nli"
    assert row["machine_max_score"] == "0.42" and row["machine_probes"] == "fts_probe"


# --- ingest_labels: keyed by fact_id, all decisions + missing ---
def test_machine_decisions_excludes_non_judgeable_classes():
    # NON_SELF_CONTAINED + DROPPED_ORACLE must NOT enter the presence/in_answer kappa (Fable ruling)
    recs = [_rec("q1", "owned", cls="OWNED_NOT_SURFACED"),
            _rec("q1", "dangling", cls="NON_SELF_CONTAINED"),
            _rec("q1", "dropped", cls="DROPPED_ORACLE", guard="unsupported")]
    md = machine_decisions(recs, "presence")
    assert fact_id("q1", "owned") in md
    assert fact_id("q1", "dangling") not in md
    assert fact_id("q1", "dropped") not in md


def test_ingest_labels_keyed(tmp_path):
    p = tmp_path / "labeled.csv"
    fid = fact_id("q1", "vital fact")
    header = ("fact_id,idx,question,fact_text,vital,guard_verdict,machine_in_answer,"
              "machine_presence,machine_class,machine_stage,human_in_answer,human_presence,"
              "human_stage_ok,human_guard_ok,human_nugget_ok,human_missing_nuggets")
    line = f"{fid},0,q1,vital fact,1,supported,0,1,OWNED_NOT_SURFACED,COMPOSE,0,1,,,1,extra fact; second miss"
    p.write_text(header + "\n" + line + "\n")
    lab = ingest_labels(str(p))
    assert lab["presence"][fid] is True
    assert lab["in_answer"][fid] is False
    assert lab["nugget_ok"][fid] is True
    assert lab["missing"]["q1"] == ["extra fact", "second miss"]


# --- paired: inner-join by key survives human reject/add (THE R8 fix) ---
def test_paired_inner_joins_by_key():
    machine = {"a": True, "b": False, "c": True}     # machine labeled a,b,c
    human = {"a": True, "c": False}                  # human dropped b, only labeled a,c
    m, h = paired(machine, human)
    assert len(m) == len(h) == 2                     # aligned to the 2 shared keys, no de-align
    assert (m, h) == ([True, True], [True, False])   # order stable, keyed not positional


def test_machine_decisions_builds_keymap():
    recs = [_rec("q1", "f1", present=True, in_answer=False),
            _rec("q1", "f2", present=False, in_answer=True)]
    dp = machine_decisions(recs, "presence")
    di = machine_decisions(recs, "in_answer")
    assert dp[fact_id("q1", "f1")] is True and dp[fact_id("q1", "f2")] is False
    assert di[fact_id("q1", "f2")] is True


# --- nugget_quality: precision/recall from accept-rejects + human-added ---
def test_nugget_quality_precision_recall():
    recs = [_rec("q1", "f1"), _rec("q1", "f2"), _rec("q1", "f3")]     # 3 machine nuggets
    human = {"nugget_ok": {fact_id("q1", "f2"): False},               # 1 rejected -> 2 accepted
             "missing": {"q1": ["human-only fact"]}}                  # 1 human-added
    nq = nugget_quality(recs, human)
    assert nq["total_machine"] == 3 and nq["accepted"] == 2 and nq["added"] == 1
    assert round(nq["precision"], 4) == round(2/3, 4)                 # accepted / total-machine
    assert round(nq["recall"], 4) == round(2/3, 4)                    # accepted / (accepted + added)

def test_nugget_quality_degenerate_empty():
    nq = nugget_quality([], {"nugget_ok": {}, "missing": {}})
    assert nq["precision"] == 1.0 and nq["recall"] == 1.0            # no nuggets -> vacuously perfect
