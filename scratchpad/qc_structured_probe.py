"""Structured-arm companion to qc_prose_probe.py (query-correction rev-4 must-fix, symmetric evidence).

Question: for the STRUCTURED ⅓ debt (role/metric/club), does CORRECTING the slang/typo query make it
ROUTE to a KG skill (route() != None), where the raw query stays router-None? That is what the KG-rescue
arm (`_try_structured(q2)`) would buy. route() is deterministic (no embed/rerank) → fast, $0.
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from eval.processing_debt.bootstrap import load_project_env  # type: ignore
load_project_env()
from v2.core.database.schema import get_connection
from v2.core.retrieval.router import route

DB = os.getenv("GATEWAY_DB", "gsa_gateway.db")

# (original slang/typo debt query, hand-corrected). From the 1000-Q report structured-debt clusters.
PAIRS = [
    # colloquial role lookups ("run"/"boss"/"president" of a dept = chair/dean)
    ("who run math", "who is the chair of mathematics"),
    ("boss of cs?", "who is the chair of computer science"),
    ("who president cybersecurity dept?", "who is the chair of the cybersecurity department"),
    ("boss of data science dept?", "who is the chair of the data science department"),
    ("who president cs?", "who is the chair of computer science"),
    ("who run cs?", "who is the chair of computer science"),
    ("who run computer sci?", "who is the chair of computer science"),
    ("who run computer science?", "who is the chair of computer science"),
    ("ece president name pls", "who is the chair of electrical and computer engineering"),
    ("boss of ywcc?", "who is the dean of the ying wu college of computing"),
    # metric / ranking
    ("top cited prof in computer sci", "most cited professor in computer science"),
    ("top cited prof in data sci dept", "most cited professor in the data science department"),
    ("machine learning prof h index?", "h-index of machine learning professors"),
    ("highest hindex in computer vision", "highest h-index in computer vision"),
    ("rank prof by citatns in biomedical engineering", "rank professors by citations in biomedical engineering"),
    ("most published prof in mathematics", "most published professor in mathematics"),
    # club / org officers
    ("women in cs officers who", "who are the officers of women in computing"),
    ("graduate stdent association officers who", "who are the graduate student association officers"),
    ("intl student club officers who", "who are the international student club officers"),
    ("data science club officers who", "who are the data science club officers"),
    # clean control (no correction) — proves whether routing was the miss
    ("who is the chair of ying wu college of computing?", "who is the chair of ying wu college of computing?"),
]

def rr(conn, q):
    r = route(conn, q)
    if r is None:
        return None
    return getattr(r, "skill", None) or getattr(r, "name", None) or str(r)

def main():
    conn = get_connection(DB)
    print(f"DB={DB}  n={len(PAIRS)}\n")
    routed = stay_none = clean_none = both_routed = 0
    w = max(len(q) for q, _ in PAIRS)
    print(f"{'original':<{w}}  route(q1)      route(q2)      verdict")
    print("-" * (w + 45))
    for q1, q2 in PAIRS:
        s1, s2 = rr(conn, q1), rr(conn, q2)
        noop = q1 == q2
        if s1 is not None:
            v = "q1-already-routes"; both_routed += 1
        elif noop:
            v = "CLEAN but router-None (routing gap)"; clean_none += 1
        elif s2 is not None:
            v = "ROUTES after fix (KG-rescue win)"; routed += 1
        else:
            v = "still None (correction doesn't route)"; stay_none += 1
        print(f"{q1:<{w}}  {str(s1):<13}  {str(s2):<13}  {v}")
    print(f"\nROUTES-after-fix (correction turns None->KG skill): {routed}")
    print(f"still-None after fix (correction doesn't route):    {stay_none}")
    print(f"clean-but-None (routing gap, no typo to fix):        {clean_none}")
    print(f"q1 already routes (not a routing miss):              {both_routed}")
    denom = routed + stay_none
    if denom:
        print(f"\nRoute-conversion among raw-None slang/typo queries: {routed}/{denom} = {routed/denom:.0%}")

if __name__ == "__main__":
    main()
