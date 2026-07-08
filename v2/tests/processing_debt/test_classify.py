# v2/tests/processing_debt/test_classify.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.types import (OracleAnswer, Nugget, GuardVerdict, PresenceResult,
                                        PresenceEvidence, XRay, Attribution)
from eval.processing_debt.classify import classify_fact, Deps

def _xr(): return XRay("q", "rag", None, [10], [10], {10: 0.5}, False, "our answer")

def _deps(**over):
    base = dict(
        guard=lambda n, o: GuardVerdict("supported"),
        entails=lambda fact, text: False,          # not in our answer
        presence=lambda conn, fact: PresenceResult(True, ["fts_probe"],
            [PresenceEvidence("knowledge_item", "10", "s", "fts_probe", item_type="policy")]),
        attribute=lambda conn, fact, pres, xray: Attribution("COMPOSE", "r"))
    base.update(over); return Deps(**base)

def test_dropped_when_guard_unsupported():
    d = _deps(guard=lambda n, o: GuardVerdict("unsupported"))
    fr = classify_fact(None, Nugget("f", True), OracleAnswer("q","a"), "our answer", _xr(), deps=d)
    assert fr.fact_class == "DROPPED_ORACLE"

def test_in_answer_when_entailed():
    d = _deps(entails=lambda fact, text: True)
    fr = classify_fact(None, Nugget("f", True), OracleAnswer("q","a"), "our answer", _xr(), deps=d)
    assert fr.fact_class == "IN_ANSWER" and fr.stage is None

def test_owned_not_surfaced_with_stage():
    fr = classify_fact(None, Nugget("f", True), OracleAnswer("q","a"), "our answer", _xr(), deps=_deps())
    assert fr.fact_class == "OWNED_NOT_SURFACED" and fr.stage == "COMPOSE"

def test_not_owned_when_absent():
    d = _deps(presence=lambda conn, fact: PresenceResult(False, [], []))
    fr = classify_fact(None, Nugget("f", True), OracleAnswer("q","a"), "our answer", _xr(), deps=d)
    assert fr.fact_class == "NOT_OWNED" and fr.stage is None

def test_non_self_contained_excluded_before_any_scoring():
    # dangling-pronoun nugget: excluded up front, guard/presence never consulted
    called = {"guard": False}
    def guard(n, o):
        called["guard"] = True; return GuardVerdict("supported")
    d = _deps(guard=guard)
    fr = classify_fact(None, Nugget("He joined in September 2022.", True),
                       OracleAnswer("q","a"), "our answer", _xr(), deps=d)
    assert fr.fact_class == "NON_SELF_CONTAINED" and fr.stage is None
    assert called["guard"] is False

def test_low_conf_presence_is_not_owned_but_flagged():
    pres = PresenceResult(False, [], [], low_conf=True, max_score=0.42)
    d = _deps(presence=lambda conn, fact: pres)
    fr = classify_fact(None, Nugget("f", True), OracleAnswer("q","a"), "our answer", _xr(), deps=d)
    assert fr.fact_class == "NOT_OWNED"
    assert fr.presence.low_conf is True          # rides through for report/adjudication bucket

def test_audit_fields_recorded():
    pres = PresenceResult(True, ["fts_probe"],
        [PresenceEvidence("knowledge_item", "10", "s", "fts_probe")], max_score=0.97)
    d = _deps(presence=lambda conn, fact: pres)
    fr = classify_fact(None, Nugget("f", True), OracleAnswer("q","a"), "our answer", _xr(), deps=d)
    assert fr.judge_id and fr.max_score == 0.97
