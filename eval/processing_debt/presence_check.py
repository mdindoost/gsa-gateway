from __future__ import annotations
import re, sqlite3, sys
from eval.processing_debt.types import PresenceResult, PresenceEvidence

_STOP = {"the","a","an","is","are","of","in","to","and","for","by","with","on","at","as","who","what",
         "which","that","this","was","were","be","from","or","his","her","their","its","it"}

def _content_terms(fact: str) -> list[str]:
    toks = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-']+", fact)
    return [t for t in toks if t.lower() not in _STOP and len(t) > 2]

def _window(text: str, needle: str, pad: int = 150) -> str:
    i = text.lower().find(needle.lower())
    if i < 0:
        return text[:400]
    return text[max(0, i - pad): i + len(needle) + pad]

def _nli_windows(span: str, fact: str, max_chars: int = 1200) -> list[str]:
    """B3: DeBERTa-NLI truncates at 512 tokens; fts/embed spans are whole documents, so a genuine
    entailing sentence past that boundary would falsely read as NOT_OWNED. Split a long span into a
    few bounded windows around the fact's content-term matches (plus a head-of-doc safety window);
    the caller scores all windows and keeps the max P(entail). Short spans pass through unchanged."""
    if len(span) <= max_chars:
        return [span]
    low = span.lower()
    half = max_chars // 2
    # Localize around the RAREST fact terms first: a term that occurs once (e.g. "12,332", "Duolingo")
    # pins the entailing sentence; a common term ("university") windows the wrong region. Window around
    # the first occurrence of each of the top rare terms, skipping windows that overlap an earlier one.
    terms = [t for t in _content_terms(fact) if low.count(t.lower()) > 0]
    ranked = sorted(terms, key=lambda t: (low.count(t.lower()), -len(t)))
    wins: list[str] = []
    starts: list[int] = []
    for t in ranked:
        i = low.find(t.lower())
        if i < 0:
            continue
        s = max(0, i - half)
        if any(abs(s - u) < half for u in starts):
            continue                       # already covered by a nearby window
        starts.append(s)
        wins.append(span[s:s + max_chars])
        if len(wins) >= 4:
            break
    if not wins:  # no lexical anchor (paraphrase) -> cover the WHOLE span, bounded to N windows
        step = max_chars - 200
        n = min(12, max(1, (len(span) + step - 1) // step))
        if len(span) <= n * step:
            starts = list(range(0, len(span), step))
        else:  # span longer than N windows can tile -> distribute evenly across the whole length
            span_room = len(span) - max_chars
            starts = [int(i * span_room / (n - 1)) for i in range(n)] if n > 1 else [0]
        for s in starts:
            wins.append(span[s:s + max_chars])
    head = span[:max_chars]
    if head not in wins:
        wins.append(head)
    seen, out = set(), []
    for w in wins:
        if w not in seen:
            seen.add(w); out.append(w)
    return out[:12]

def _node_span(conn, node_id) -> str:
    """M1: span = the node's REAL structured content (name+type+attrs) + its edges (roles/titles/areas),
    NOT the bare name — else KG attribute facts (titles, office, h-index) wrongly fail entailment."""
    n = conn.execute("SELECT name, type, attrs FROM nodes WHERE id=?", (node_id,)).fetchone()
    if not n:
        return ""
    parts = [n["name"] or "", n["type"] or "", n["attrs"] or ""]
    for e in conn.execute("SELECT type, category, attrs FROM edges WHERE src_id=? AND is_active=1", (node_id,)):
        parts += [e["type"] or "", e["category"] or "", e["attrs"] or ""]
    return " | ".join(p for p in parts if p)

def kg_probe(conn, fact) -> list[PresenceEvidence]:
    names = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", fact)
    out, seen = [], set()
    for name in names:
        for r in conn.execute("SELECT id FROM nodes WHERE name LIKE ? OR attrs LIKE ?",
                              (f"%{name}%", f"%{name}%")):
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            out.append(PresenceEvidence("node", str(r["id"]), _node_span(conn, r["id"]), "kg_probe"))
    return out

def fts_probe(conn, fact) -> list[PresenceEvidence]:
    """Over ALL knowledge_items (bypasses production exclude_types). Span = FULL content (M3: not [:300])."""
    terms = _content_terms(fact)
    if not terms:
        return []
    query = " OR ".join(f'"{t}"' for t in terms[:12])
    try:
        rows = conn.execute(
            "SELECT ki.id AS id, ki.type AS type, ki.content AS content "
            "FROM knowledge_fts f JOIN knowledge_items ki ON ki.id = f.rowid "
            "WHERE knowledge_fts MATCH ? LIMIT 20", (query,)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [PresenceEvidence("knowledge_item", str(r["id"]), (r["content"] or ""), "fts_probe",
                             item_type=r["type"]) for r in rows]

def grep_probe(conn, fact) -> list[PresenceEvidence]:
    """M3: over knowledge_items.content AND .title AND nodes.attrs. Span = DB window around the match."""
    phrases = [p.strip() for p in re.findall(r"[A-Za-z0-9][A-Za-z0-9\- ]{12,}", fact)]
    out = []
    for ph in phrases:
        for r in conn.execute(
                "SELECT id, type, title, content FROM knowledge_items WHERE content LIKE ? OR title LIKE ? LIMIT 10",
                (f"%{ph}%", f"%{ph}%")):
            body = f"{r['content'] or ''} {r['title'] or ''}"
            out.append(PresenceEvidence("knowledge_item", str(r["id"]), _window(body, ph), "grep_probe",
                                        item_type=r["type"]))
        for r in conn.execute("SELECT id, attrs FROM nodes WHERE attrs LIKE ? LIMIT 10", (f"%{ph}%",)):
            out.append(PresenceEvidence("node", str(r["id"]), _window(r["attrs"] or "", ph), "grep_probe"))
    return out

def embed_probe(conn, fact, embed_query, knn) -> list[PresenceEvidence]:
    vec = embed_query(fact)
    if vec is None:
        return []
    return [PresenceEvidence("knowledge_item", str(i), (c or ""), "embed_probe", item_type=t)
            for (i, t, c) in knn(conn, vec, k=100)]

def _real_embed_and_knn():
    """M2: Embedder from the ACTIVE DESCRIPTOR (Qwen 1024-d in Build B); production KNN SQL verbatim."""
    from v2.core.retrieval.embedder import Embedder
    from v2.core.retrieval.model_descriptor import active_descriptor
    import sqlite_vec
    emb = Embedder(); dim = active_descriptor().dim
    def embed_query(text):
        v = emb.embed_query(text)
        if v is not None and len(v) != dim:
            raise RuntimeError(f"embed width {len(v)} != active dim {dim}")
        return v
    def knn(conn, vec, k=100):
        # Mirror the PROVEN production _semantic (retriever.py:376): the vec0 KNN must run ALONE with its
        # LIMIT (a JOIN inside the MATCH query breaks sqlite-vec's limit detection → "LIMIT or 'k = ?'
        # constraint is required"), THEN fetch item type/content in a second query.
        try:
            hits = conn.execute(
                "SELECT item_id FROM knowledge_vectors "
                "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                (sqlite_vec.serialize_float32(vec), k)).fetchall()
        except sqlite3.OperationalError as e:
            print(f"[presence_check] embed_probe KNN failed (vec unavailable?): {e}", file=sys.stderr)
            return []
        ids = [r[0] for r in hits]
        if not ids:
            return []
        ph = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT id, type, content FROM knowledge_items WHERE id IN ({ph})", ids).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]
    return embed_query, knn

_PSEUDO = {"yes": 1.0, "unsure": 0.5, "no": 0.0}

def _score_evidence(fact, evidence, *, verdict_fn=None, judge=None) -> list[tuple[object, str, float]]:
    """Return [(evidence, verdict, score)] per candidate span.
    - verdict_fn (per-span, hermetic tests / legacy): call it; pseudo-score from the verdict.
    - else BATCH path: window each span (B3), score ALL windows in ONE nli call (entailment.batch_verdicts),
      keep the max-scoring window's (verdict, score) per span."""
    if verdict_fn is not None:
        out = []
        for ev in evidence:
            v = verdict_fn(fact, ev.span)
            out.append((ev, v, _PSEUDO.get(v, 0.0)))
        return out
    from eval.processing_debt.entailment import batch_verdicts
    flat, groups = [], []            # groups[i] = (start, end) window-index range for evidence i
    for ev in evidence:
        wins = _nli_windows(ev.span, fact)
        groups.append((len(flat), len(flat) + len(wins)))
        flat.extend(wins)
    scored = batch_verdicts(fact, flat, judge=judge)   # [(verdict, score)] per window, in order
    out = []
    for ev, (s, e) in zip(evidence, groups):
        best_v, best_s = "no", 0.0
        for v, sc in scored[s:e]:
            if sc > best_s:
                best_v, best_s = v, sc
        out.append((ev, best_v, best_s))
    return out

def presence(conn, fact, *, embedder=None, verdict_fn=None, judge=None) -> PresenceResult:
    """Union of 4 probes; confirm candidate spans with the calibrated NLI judge (batched).
    NEW lean (Fable B2): present iff some span is a confident 'yes' (P(entail) >= HI). A span in the
    [LO,HI) band does NOT make the fact present, but sets low_conf=True and is RETAINED as evidence so
    the human still adjudicates it (never silently dropped)."""
    evidence = []
    evidence += kg_probe(conn, fact)
    evidence += fts_probe(conn, fact)
    evidence += grep_probe(conn, fact)
    if embedder != "SKIP":
        eq, knn = _real_embed_and_knn()
        evidence += embed_probe(conn, fact, eq, knn)
    scored = _score_evidence(fact, evidence, verdict_fn=verdict_fn, judge=judge)
    yes_ev = [ev for ev, v, _ in scored if v == "yes"]
    unsure_ev = [ev for ev, v, _ in scored if v == "unsure"]
    max_score = max((s for _, _, s in scored), default=0.0)
    present = bool(yes_ev)
    low_conf = (not present) and bool(unsure_ev)
    kept = yes_ev if present else (unsure_ev if low_conf else [])
    return PresenceResult(present=present, probes_hit=sorted({e.probe for e in kept}),
                          evidence=kept, unsure_only=low_conf, low_conf=low_conf,
                          max_score=round(max_score, 4))
