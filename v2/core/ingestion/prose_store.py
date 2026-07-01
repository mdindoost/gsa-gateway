"""The ONE canonical prose write path (day-1 rebuild Task 4).

`upsert_prose` keys a prose row on its `canonical_prose_url` GLOBALLY — across every org and every
source — so the same page can never land as two rows (the org-scoped `(org_id,natural_key,created_by)`
key was what let `policy@bursar` + `webpage@njit` co-exist; spec §4.3, SE#2/RAG#3). On a content change
it keeps the FULLER/substantive capture (`keep_better`), never blindly adopting the latest — so a
truncated re-fetch can't overwrite a denser row (spec §4.4, RAG#1). Does NOT commit (caller owns txn).

Spec: docs/superpowers/specs/2026-06-30-day1-prose-rebuild-design.md §4.3/§4.4
"""
from __future__ import annotations

import hashlib
import json

from v2.core.ingestion.prose_quality import keep_better


# Prose sources whose rows must be one-active-per-canonical-URL. `crawler` is EXCLUDED: after the
# day-1 wipe there is no `crawler` prose (it re-crawls as njit_www_crawl), and `crawler` PERSON rows
# carry an entity-scoped natural_key that must never be constrained by this URL index (SE#1/Codex#5).
PROSE_INDEX_SOURCES = ("njit_www_crawl", "college_crawl", "catalog_crawl")

PROSE_UNIQUE_INDEX_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_prose_canonical "
    "ON knowledge_items(json_extract(metadata,'$.natural_key')) "
    "WHERE is_active=1 AND created_by IN ('njit_www_crawl','college_crawl','catalog_crawl')"
)


def ensure_prose_unique_index(conn) -> None:
    """DB-enforce one active row per canonical prose URL (backstop to upsert_prose; spec §4.1).
    Prose-scoped partial index — applied to the CLEAN rebuilt DB by the rebuild runner, NOT to the
    always-run create_all (the live DB still holds dup active prose until the swap, which would make
    a global create-index fail on every bot restart)."""
    conn.execute(PROSE_UNIQUE_INDEX_SQL)


def _hash(content: str) -> str:
    return hashlib.sha1((content or "").encode("utf-8")).hexdigest()


def upsert_prose(conn, *, org_id: int, ptype: str, title: str, content: str, meta: dict,
                 canonical: str, created_by: str) -> str:
    """Upsert one prose page keyed on `canonical` across ALL active prose rows.
    Returns 'inserted' | 'updated' | 'unchanged' | 'skipped_worse'."""
    ch = _hash(content)
    row = conn.execute(
        "SELECT id, content, type, json_extract(metadata,'$.content_hash') "
        "FROM knowledge_items WHERE is_active=1 AND json_extract(metadata,'$.natural_key')=? "
        "LIMIT 1", (canonical,)).fetchone()

    full_meta = dict(meta or {})
    full_meta["natural_key"] = canonical
    full_meta["content_hash"] = ch
    full_meta.setdefault("source", created_by)

    def _insert():
        conn.execute(
            "INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,"
            "version,is_active,created_by) VALUES(?,?,?,?,?,?,1,1,?)",
            (org_id, ptype, title, content, json.dumps(full_meta), canonical, created_by))

    if row is None:
        _insert()
        return "inserted"

    rid, ex_content, ex_type, ex_hash = row[0], row[1], row[2], row[3]
    if ex_hash == ch:
        return "unchanged"
    # content differs — keep the better capture (substantive type wins; else more real content)
    if not keep_better(content, ptype, ex_content, ex_type):
        return "skipped_worse"
    conn.execute("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') WHERE id=?",
                 (rid,))
    _insert()
    return "updated"
