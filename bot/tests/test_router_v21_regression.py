"""Committed routing regression set for the Kavosh v2.1 UnifiedRouter (spec §11).

Runs THROUGH UnifiedRouter.decide on the real KG (read-only) — not ask.sh, which bypasses the
gate. Asserts each known case routes to the CORRECT family. Marked integration: needs Ollama (the
classifier encodes) + the live gsa_gateway.db. Skips cleanly when the DB is absent."""
import os
import sqlite3
import pytest


def _has_live_kg(path="gsa_gateway.db"):
    """True only for a POPULATED KG — guards against the 0-byte gsa_gateway.db placeholder a git
    worktree leaves behind (a bare os.path.exists would let these run against an empty DB and fail
    spuriously with 'no such table')."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            c.execute("SELECT 1 FROM organizations LIMIT 1")
            return True
        finally:
            c.close()
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _has_live_kg(), reason="needs the live KG (read-only, populated)")

CASES = [
    pytest.param("how do I become a dean", "RAG", id="become-dean"),       # no org+role match → None → RAG
    pytest.param("who is responsible for officer elections", "RAG", id="officer-elections"),  # _OFFICER_PROCESS
    pytest.param("departments in math", "RAG", id="leaf-dept"),            # leaf dept → not org_departments
    pytest.param("top 10 by citations in mechanical engineering", "KG", id="top-cited-me"),  # the motivating miss
    pytest.param("who are the gsa officers", "KG", id="gsa-officers"),
    # KNOWN GAP (xfail, flagged in the flip-gate go-live doc): the SHARED deterministic router.route()
    # returns faculty_in_department for "how do I become faculty in cs" (it has no process-phrasing
    # negative guard for "become/join faculty"; only role-process guards like _OFFICER_PROCESS exist).
    # The fast-path "faculty" cue therefore dispatches KG. This is PRE-EXISTING behavior the legacy
    # _try_structured path shares — NOT introduced by Phase 1b. A proper fix is a process-phrasing
    # negative guard in router.py (a change to the LIVE shared router, its own reviewed change) or
    # Phase-2 LLM slot-recovery. Deferred + documented, never silently dropped.
    pytest.param("how do I become faculty in cs", "RAG", id="become-faculty",
                 marks=pytest.mark.xfail(reason="shared router.route() has no become/join-faculty "
                                                "process guard; pre-existing, deferred to a reviewed "
                                                "router.py guard or Phase-2 slot-recovery", strict=True)),
    pytest.param("hi", "COMMAND", id="greeting"),
    pytest.param("clear", "COMMAND", id="clear"),
]


@pytest.mark.integration
@pytest.mark.parametrize("msg,expected_family", CASES)
def test_decide_family(msg, expected_family):
    from v2.core.retrieval.embedder import Embedder
    from v2.core.retrieval.route_exemplars import build_classifier
    from v2.core.retrieval.unified_router import UnifiedRouter
    from bot.services.intent_detector import IntentDetector
    conn = sqlite3.connect("file:gsa_gateway.db?mode=ro", uri=True)
    try:
        emb = Embedder()
        r = UnifiedRouter(db_path="file:gsa_gateway.db?mode=ro",
                          classifier=build_classifier(conn, emb), intent_detector=IntentDetector())
        assert r.decide(msg).family == expected_family
    finally:
        conn.close()
