from __future__ import annotations
from dataclasses import dataclass
from eval.processing_debt.types import OracleAnswer, Nugget, XRay, FactRecord

@dataclass
class Deps:
    guard: object
    entails: object
    presence: object
    attribute: object

def _default_deps() -> Deps:
    from eval.processing_debt.oracle_guard import guard
    from eval.processing_debt.entailment import text_entails_fact
    from eval.processing_debt.presence_check import presence
    from eval.processing_debt.attribute import attribute
    # IN_ANSWER uses the WINDOWED strict-yes check (bot answers can exceed the 512-token NLI cap).
    return Deps(guard=guard, entails=text_entails_fact, presence=presence, attribute=attribute)

def classify_fact(conn, nugget: Nugget, oracle: OracleAnswer, our_answer: str, xray: XRay,
                  *, stratum: str = "", deps: Deps | None = None) -> FactRecord:
    deps = deps or _default_deps()
    from eval.processing_debt.self_contained import is_self_contained
    from eval.processing_debt.entailment import active_judge_id
    jid = active_judge_id()
    # Nugget-quality gate (Fable pronoun ruling): dangling-anaphor nuggets are unjudgeable in
    # isolation -> excluded up front from the kappa denominator + headline, before any scoring.
    if not is_self_contained(nugget.text):
        return FactRecord(oracle.question, stratum, nugget.text, nugget.vital, "",
                          in_answer=False, presence=_empty(), fact_class="NON_SELF_CONTAINED",
                          stage=None, xray_ref=oracle.question, judge_id=jid)
    gv = deps.guard(nugget, oracle)
    if gv.verdict != "supported":
        return FactRecord(oracle.question, stratum, nugget.text, nugget.vital, gv.verdict,
                          in_answer=False, presence=_empty(), fact_class="DROPPED_ORACLE",
                          stage=None, xray_ref=oracle.question, judge_id=jid)
    in_ans = deps.entails(nugget.text, our_answer or "")
    if in_ans:
        return FactRecord(oracle.question, stratum, nugget.text, nugget.vital, gv.verdict,
                          in_answer=True, presence=_empty(), fact_class="IN_ANSWER",
                          stage=None, xray_ref=oracle.question, judge_id=jid)
    pres = deps.presence(conn, nugget.text)
    if not pres.present:
        return FactRecord(oracle.question, stratum, nugget.text, nugget.vital, gv.verdict,
                          in_answer=False, presence=pres, fact_class="NOT_OWNED",
                          stage=None, xray_ref=oracle.question, judge_id=jid, max_score=pres.max_score)
    attr = deps.attribute(conn, nugget.text, pres, xray)
    return FactRecord(oracle.question, stratum, nugget.text, nugget.vital, gv.verdict,
                      in_answer=False, presence=pres, fact_class="OWNED_NOT_SURFACED",
                      stage=attr.stage, xray_ref=oracle.question, judge_id=jid, max_score=pres.max_score)

def _empty():
    from eval.processing_debt.types import PresenceResult
    return PresenceResult(False, [], [])
