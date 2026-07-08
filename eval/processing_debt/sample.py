# eval/processing_debt/sample.py
"""Stratified question samplers for the 3 pilot sets (R0). Read-only.

Set A = real student logs (live `questions` table), stratified by CONFIDENCE BANDS (the live
`was_answered` column is uniformly 0 → dead; confidence 0–100 is the discriminating signal).
Set B = SampleQuestions DB-answerable, stratified by pipeline path.
Set C = SampleQuestions web-needing (mostly NOT_OWNED; no debt denominator).
Controls (positive_control / oracle_blind) load from curated files under out/ if present, else empty
(the sampler degrades gracefully; the files are authored in Task 14 setup).
"""
from __future__ import annotations
import random
import re
from pathlib import Path

_PKG = Path(__file__).resolve().parent
_REPO = _PKG.parents[1]
_OUT = _PKG / "out"
_SAMPLEQ = _REPO / "docs" / "SampleQuestions"

# Each set's strata MUST sum to 50 (R0).
STRATA_A = [                       # real logs, confidence-band strata + controls
    ("answered_hi_conf", 16), ("answered_lo_conf", 14), ("deflected", 12),
    ("positive_control", 5), ("oracle_blind", 3),
]
STRATA_B = [                       # SampleQuestions DB-answerable, pipeline-path strata + controls
    ("db_router_hit", 12), ("db_rag", 14), ("db_live_fallback", 8),
    ("db_abstain", 8), ("positive_control", 5), ("oracle_blind", 3),
]
STRATA_C = [                       # web-needing (knowledge-gap track; no debt denominator)
    ("web_needing", 50),
]

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def allocate(pools: dict, strata: list, seed: int = 0) -> list:
    """Draw each stratum's quota from its pool (shuffled by `seed`), dedup questions across strata.
    A short pool yields only what it has — never raises, never duplicates."""
    rng = random.Random(seed)
    picked, used = [], set()
    for name, n in strata:
        cand = [q for q in pools.get(name, []) if q not in used]
        rng.shuffle(cand)
        for q in cand[:n]:
            used.add(q)
            picked.append((q, name))
    return picked


def junk_filter(questions: list, min_tokens: int = 3) -> list:
    return [q for q in questions if len(q.split()) >= min_tokens]


def pii_filter(questions: list) -> list:
    """Drop any row carrying an email (student self-identifier) before it goes outbound to Brave.
    Faculty names are public → kept."""
    return [q for q in questions if not _EMAIL_RE.search(q)]


def stratum_of_row(confidence, has_topic, *, hi: float = 70.0) -> str:
    """Map a log row's stored signals to a Set-A stratum. `was_answered` is dead in the live DB, so
    we use confidence bands: >=hi -> answered_hi_conf; 0<c<hi -> answered_lo_conf; else deflected."""
    if confidence is None or confidence <= 0 or not has_topic:
        return "deflected"
    return "answered_hi_conf" if confidence >= hi else "answered_lo_conf"


def _fetch_set_a_rows(conn) -> list:
    """(question_text, max-confidence, has_topic) per distinct usable question from the live table."""
    q = ("SELECT question_text, MAX(confidence) AS conf, "
         "MAX(CASE WHEN matched_topic IS NOT NULL THEN 1 ELSE 0 END) AS has_topic "
         "FROM questions WHERE question_text IS NOT NULL AND length(question_text) >= 12 "
         "GROUP BY question_text")
    return [(r[0], r[1], r[2]) for r in conn.execute(q).fetchall()]


def _default_dedup(questions: list, threshold: float = 0.92) -> list:
    """Near-paraphrase dedup (R0 step 3). Embeds with the active descriptor and greedily keeps one
    exemplar per cluster (cosine >= threshold). Falls back to normalized-exact dedup if embeddings
    are unavailable (keeps the sampler runnable without the model in dev)."""
    try:
        from v2.core.retrieval.embedder import Embedder   # same pattern as presence_check._real_embed_and_knn
        import numpy as np
        emb = Embedder()                                    # resolves the active descriptor internally
        kept, kept_vecs = [], []
        for qq in questions:
            raw = emb.embed_query(qq)
            if raw is None:                                 # embed_query returns None on failure
                kept.append(qq)                             # never drop a question we couldn't embed
                continue
            v = np.asarray(raw, dtype="float32")
            n = float((v * v).sum()) ** 0.5 or 1.0
            vn = v / n
            if any(float((vn * kv).sum()) >= threshold for kv in kept_vecs):
                continue
            kept.append(qq)
            kept_vecs.append(vn)
        return kept
    except Exception:
        seen, kept = set(), []
        for qq in questions:
            key = " ".join(qq.lower().split())
            if key not in seen:
                seen.add(key)
                kept.append(qq)
        return kept


def _load_control_pool(name: str) -> list:
    fn = {"positive_control": "controls_positive.txt", "oracle_blind": "controls_internal.txt"}.get(name)
    if not fn:
        return []
    f = _OUT / fn
    if not f.exists():
        return []
    return [l.strip() for l in f.read_text().splitlines() if l.strip()]


def sample_set_a(conn=None, seed: int = 0, *, fetch=None, dedup=None) -> list:
    """Set A — real student logs. fetch(conn)->rows and dedup(questions)->questions are injectable
    for unit tests (default hits the live `questions` table + embed-dedup)."""
    if conn is None:
        from eval.processing_debt.dbconn import get_ro_connection
        conn = get_ro_connection()
    fetch = fetch or _fetch_set_a_rows
    dedup = dedup or _default_dedup
    rows = fetch(conn)
    # junk + PII on the text, preserving each row's signals
    kept_text = set(pii_filter(junk_filter([r[0] for r in rows])))
    rows = [r for r in rows if r[0] in kept_text]
    # dedup near-paraphrases, then re-attach signals for the survivors
    survivors = set(dedup([r[0] for r in rows]))
    pools = {name: [] for name, _ in STRATA_A}
    for text, conf, has_topic in rows:
        if text not in survivors:
            continue
        survivors.discard(text)                        # each text lands in exactly one pool once
        s = stratum_of_row(conf, has_topic)
        pools[s].append(text)
    pools["positive_control"] = _load_control_pool("positive_control")
    pools["oracle_blind"] = _load_control_pool("oracle_blind")
    return allocate(pools, STRATA_A, seed=seed)


def _load_lines(path: Path) -> list:
    return [l.strip() for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def sample_set_b(seed: int = 0, *, path_label=None) -> list:
    """Set B — SampleQuestions DB-answerable, stratified by live pipeline path."""
    if path_label is None:
        from eval.processing_debt.pathlabel import label_path
        path_label = label_path
    db = junk_filter(pii_filter(_load_lines(_SAMPLEQ / "Question based on DB.txt")))
    pools = {name: [] for name, _ in STRATA_B}
    keymap = {"router_hit": "db_router_hit", "rag": "db_rag",
              "live_fallback": "db_live_fallback", "abstain": "db_abstain"}
    for qq in db:
        key = keymap.get(path_label(qq))
        if key:
            pools[key].append(qq)
    pools["positive_control"] = _load_control_pool("positive_control")
    pools["oracle_blind"] = _load_control_pool("oracle_blind")
    return allocate(pools, STRATA_B, seed=seed)


def sample_set_c(seed: int = 0) -> list:
    """Set C — SampleQuestions web-needing. Single stratum; mostly NOT_OWNED by construction."""
    web = junk_filter(pii_filter(_load_lines(_SAMPLEQ / "Questions based on internet.txt")))
    return allocate({"web_needing": web}, STRATA_C, seed=seed)
