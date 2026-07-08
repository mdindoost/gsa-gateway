from __future__ import annotations
from eval.processing_debt.types import Attribution, PresenceResult, XRay

def _excluded_types(conn) -> set[str]:
    """M4: read the LIVE retriever.exclude_types setting; do NOT hardcode. Real default = {publication,
    syllabus} (retriever.py DEFAULT_EXCLUDE_TYPES), and it is admin-tunable via the settings table."""
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='retriever.exclude_types'").fetchone()
            if row and row[0] is not None:
                return {t.strip() for t in str(row[0]).split(",") if t.strip()}
        except Exception:
            pass
    return {"publication", "syllabus"}

def attribute(conn, fact: str, presence: PresenceResult, xray: XRay, *, erag=None) -> Attribution:
    from eval.processing_debt.erag_attrib import chunk_yields_fact
    erag = erag or chunk_yields_fact
    ev = presence.evidence
    excluded = _excluded_types(conn)
    ki = [e for e in ev if e.source_type == "knowledge_item"]
    servable = [int(e.row_or_node_id) for e in ki if e.item_type not in excluded]

    # CONFIG: fact found ONLY in excluded knowledge_items types (owned, but deliberately not served)
    if ki and all(e.item_type in excluded for e in ki) and not any(e.source_type == "node" for e in ev):
        return Attribution("CONFIG", "fact lives only in an excluded item type")

    # ROUTER: a KG-owned fact whose structured skill wasn't routed — but ONLY when there is no servable
    # knowledge_item chunk that could have carried it (else prefer the POOL/RANK/COMPOSE branch below).
    if any(e.source_type == "node" for e in ev) and xray.router_skill is None and not servable:
        return Attribution("ROUTER", "kg-owned fact but router did not hit a structured skill")

    # servable (non-excluded) chunk → locate it in the retrieval pipeline
    if servable:
        cid = servable[0]
        if cid not in xray.fused_pool_ids:
            return Attribution("POOL", "evidence chunk absent from the fused candidate pool")
        if cid not in xray.top5_ids:
            if erag(conn, cid, xray.question, fact):
                return Attribution("RANK", "chunk in pool, below top-5, but alone yields the fact")
            return Attribution("POOL", "chunk in pool but not utile for the fact")
        return Attribution("COMPOSE", "chunk in top-5 context but fact absent from the answer")

    return Attribution("UNRESOLVED", "no servable evidence chunk mapped to a stage")
