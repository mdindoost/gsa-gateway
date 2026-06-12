"""Item-level reconcile (R2).

One entity decomposes into MANY items that share a ``source_url``, so a re-crawl
cannot be a single upsert-by-url. Instead we diff the freshly-decomposed KItems
for one entity against the active rows in ``knowledge_items`` — matched by their
stable ``metadata.natural_key`` — and apply the minimal change set:

  * a natural_key that is new            -> INSERT a fresh original
  * a natural_key whose body changed     -> version-bump (deactivate old, insert
                                            new with parent_id/root_id wired)
  * a natural_key that vanished          -> deactivate (the entity dropped it)
  * an identical natural_key             -> leave untouched

Embeddings live in a separate table keyed by item id, so reconcile only touches
``knowledge_items`` rows and hands the caller the id sets it must (re)embed or
drop vectors for. See docs/superpowers/specs/2026-06-11-hybrid-knowledge-ingestion.md (§7b R2).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from v2.core.ingestion.entity import KItem


@dataclass
class ReconcileResult:
    inserted_ids: list[int] = field(default_factory=list)            # brand-new originals
    superseded: list[tuple[int, int]] = field(default_factory=list)  # (old_id, new_id)
    deactivated_ids: list[int] = field(default_factory=list)         # dropped, no replacement
    unchanged_ids: list[int] = field(default_factory=list)

    @property
    def to_embed(self) -> list[int]:
        """Active ids that need a (re)embedding: new originals + new versions."""
        return self.inserted_ids + [new for _, new in self.superseded]

    @property
    def vectors_to_drop(self) -> list[int]:
        """Ids whose vectors are now stale: dropped items + superseded versions."""
        return self.deactivated_ids + [old for old, _ in self.superseded]

    def summary(self) -> str:
        return (f"+{len(self.inserted_ids)} new, ~{len(self.superseded)} updated, "
                f"-{len(self.deactivated_ids)} removed, "
                f"={len(self.unchanged_ids)} unchanged")


def _store_meta(item: KItem) -> dict:
    """The metadata as persisted: the item's own metadata plus its natural_key
    (so a later reconcile can match this row back to a freshly-decomposed item)."""
    meta = dict(item.metadata)
    meta["natural_key"] = item.natural_key
    return meta


def _insert(conn, org_id: int, item: KItem, meta: dict, created_by: str,
            version: int = 1, root_id=None, parent_id=None) -> int:
    cur = conn.execute(
        "INSERT INTO knowledge_items"
        "(org_id,type,title,content,metadata,source_url,version,root_id,parent_id,"
        " is_active,created_by) VALUES(?,?,?,?,?,?,?,?,?,1,?)",
        (org_id, item.type, item.title, item.content, json.dumps(meta),
         item.source_url, version, root_id, parent_id, created_by),
    )
    return cur.lastrowid


def reconcile_entity(conn, org_id: int, entity_id: str, items: list[KItem],
                     created_by: str = "ingest") -> ReconcileResult:
    """Apply the freshly-decomposed ``items`` as the new active state of one entity.

    Caller is responsible for embedding ``result.to_embed`` and dropping vectors
    for ``result.vectors_to_drop`` afterwards.
    """
    result = ReconcileResult()
    # One transaction for the whole entity: a mid-loop failure rolls back so we
    # never leave an entity half-deactivated / half-inserted. `with conn` commits
    # on success and rolls back on any exception (it does not close the conn).
    with conn:
        rows = conn.execute(
            "SELECT id, title, content, metadata, root_id, version "
            "FROM knowledge_items "
            "WHERE org_id=? AND is_active=1 AND json_extract(metadata,'$.entity_id')=?",
            (org_id, entity_id),
        ).fetchall()

        existing: dict[str, tuple] = {}
        for r in rows:
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
            nk = meta.get("natural_key")
            if nk:
                existing[nk] = (r, meta)

        seen: set[str] = set()
        for item in items:
            nk = item.natural_key
            seen.add(nk)
            new_meta = _store_meta(item)

            if nk not in existing:
                result.inserted_ids.append(
                    _insert(conn, org_id, item, new_meta, created_by))
                continue

            row, old_meta = existing[nk]
            if (row["title"] == item.title and row["content"] == item.content
                    and old_meta == new_meta):
                result.unchanged_ids.append(row["id"])
                continue

            # body changed -> version-bump
            conn.execute(
                "UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') WHERE id=?",
                (row["id"],))
            new_id = _insert(conn, org_id, item, new_meta, created_by,
                             version=row["version"] + 1, root_id=row["root_id"],
                             parent_id=row["id"])
            result.superseded.append((row["id"], new_id))

        # anything present before but absent now -> deactivate
        for nk, (row, _meta) in existing.items():
            if nk not in seen:
                conn.execute(
                    "UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') WHERE id=?",
                    (row["id"],))
                result.deactivated_ids.append(row["id"])

    return result
