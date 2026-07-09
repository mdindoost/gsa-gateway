"""$0 pre-build probe for query-correction-salvage rev-4 must-fix #4 (Fable).

Question: does a typo/slang FIX actually lift a prose-debt query's top cross-encoder
relevance from BELOW LIVE_THRESHOLD (miss -> live) to ABOVE it (surfaced)? Or is the
miss corpus-side (chunking/coverage), which no rewrite converts?

Read-only. Loads the real serving retriever (Qwen embed + CE rerank) once, runs each
(original, corrected) pair, reports top reranked CE for each and the conversion verdict.
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# mirror bot startup: load .env so EMBEDDING_MODEL/thresholds match live
from eval.processing_debt.bootstrap import load_project_env  # type: ignore
load_project_env()

LIVE_THRESHOLD = float(os.getenv("LIVE_THRESHOLD", "0.15"))
DB = os.getenv("GATEWAY_DB", "gsa_gateway.db")

from v2.core.database.schema import get_connection
from v2.core.retrieval.embedder import Embedder
from v2.core.retrieval.reranker import CrossEncoderReranker
from v2.core.retrieval.retriever import V2Retriever

# (original typo/slang query, hand-corrected query). corrected==original => no typo to fix
# (tests whether the miss is corpus-side). All are prose/policy/forms DEBT/LIVE-owned from
# the 1000-Q report (the ⅔ PROSE-RAG arm the rewrite's RAG-rescue would target).
PAIRS = [
    ("how cpt apply", "how to apply for curricular practical training cpt"),
    ("how cpt applyy", "how to apply for curricular practical training cpt"),
    ("wht is njit policy fr opt?", "what is njit policy for optional practical training opt"),
    ("cn i take less credits at njit?", "can i take less credits at njit"),
    ("what happen if i miss add drop?", "what happens if i miss add drop"),
    ("wher submit degreeworks form?", "where do i submit a degreeworks form"),
    ("where submit tuishon refund form?", "where do i submit a tuition refund form"),
    ("wheree can i find info about academic probation?", "where can i find info about academic probation"),
    ("i have problem with health insurance waiver what do", "what do i do about a health insurance waiver problem"),
    ("where cn i find info about degreeworks?", "where can i find info about degreeworks"),
    ("wht happen if i miss tuition due date?", "what happens if i miss the tuition due date"),
    ("i have problem w grade appeal what do", "what do i do about a grade appeal"),
    ("i have problem with course registration what do", "what do i do about course registration"),
    ("what happen if i get academic warning?", "what happens if i get an academic warning"),
    ("wht is njit policy fr late payment?", "what is njit policy for late payment"),
    ("i20 sign wheree go", "where do i go for an i-20 signature"),
    ("where cn i find info about meal plan", "where can i find info about meal plan"),
    ("wher can i print on campus", "where can i print on campus"),
    ("how to apply fr room change?", "how to apply for a room change"),
    ("i have problem with academic probation what do", "what do i do about academic probation"),
    # already-clean debt (no typo) -> correction is a NO-OP -> proves miss is corpus/routing-side
    ("how do i transfer credits?", "how do i transfer credits?"),
    ("how to apply for health insurance waiver?", "how to apply for health insurance waiver?"),
    ("can i withdraw late at njit?", "can i withdraw late at njit?"),
    ("where can i find info about late payment?", "where can i find info about late payment?"),
]

def top_ce(chunks):
    for c in chunks:
        md = getattr(c, "metadata", None) or {}
        ce = md.get("ce_score")
        if ce is None:
            ce = getattr(c, "ce_score", None)
        if ce is not None:
            return float(ce)
    return None

def main():
    conn = get_connection(DB)
    emb = Embedder(); rer = CrossEncoderReranker()
    ret = V2Retriever(conn, emb, reranker=rer)
    print(f"LIVE_THRESHOLD={LIVE_THRESHOLD}  DB={DB}  n={len(PAIRS)}\n")
    conv = corpus = clean_noop = both_ok = 0
    rows = []
    for q1, q2 in PAIRS:
        r1 = top_ce(ret.retrieve(q1, limit=5)) or 0.0
        r2 = top_ce(ret.retrieve(q2, limit=5)) or 0.0
        miss1 = r1 < LIVE_THRESHOLD
        clears2 = r2 >= LIVE_THRESHOLD
        noop = (q1 == q2)
        if not miss1:
            verdict = "q1-already-clears"; both_ok += 1
        elif noop:
            verdict = "CORPUS (clean, still miss)" if not clears2 else "q1-already-clears";
            if not clears2: corpus += 1
        elif clears2:
            verdict = "CONVERT (fix lifts >thr)"; conv += 1
        else:
            verdict = "CORPUS (fix doesn't lift)"; corpus += 1
        rows.append((q1, r1, r2, verdict))
    w = max(len(q) for q, *_ in rows)
    print(f"{'original':<{w}}  q1_ce  q2_ce  verdict")
    print("-" * (w + 30))
    for q1, r1, r2, v in rows:
        print(f"{q1:<{w}}  {r1:5.3f}  {r2:5.3f}  {v}")
    print(f"\nCONVERSIONS (fix lifts miss->clear): {conv}")
    print(f"CORPUS debt (fix doesn't help / clean-but-miss): {corpus}")
    print(f"q1 already clears threshold (not a retrieval miss): {both_ok}")
    denom = conv + corpus
    if denom:
        print(f"\nConversion rate among genuine misses: {conv}/{denom} = {conv/denom:.0%}")

if __name__ == "__main__":
    main()
