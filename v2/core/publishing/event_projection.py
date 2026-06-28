"""EVENT → KB one-way projection (Phase 3, Build 3).

``derive_event_kb(ops_conn, kb_conn, *, org_slugs=("gsa",))`` is the single
idempotent, rebuildable function that copies a GSA event from the OPS DB into a
``knowledge_item`` (type ``event_info``) in the Knowledge DB.

``event_natural_key(name, date)`` computes the stable derive key for a given
event: normalized name + date. The natural key is stored in the KB item's
``metadata`` so re-runs match on it and never duplicate.

``resolve_org`` is **imported from** ``org_resolve.py`` — this module does NOT
redefine it (Phase 3 spec: REUSE the Build-2 helper).

MED-8 transition: existing ``event_info`` rows written before Phase 3 carry
``metadata.event_id`` (the OPS rowid) instead of ``metadata.natural_key``.
During derive, if no item matches on ``natural_key``, we fall back to matching
on ``event_id`` / ``ops_event_id``. The matched row is then back-filled with
the new ``natural_key`` so subsequent runs use the primary path.

Cross-DB write ordering (MED-9): callers commit OPS first, then call
``derive_event_kb``. A KB-write failure is caught and logged as a WARNING so
the OPS event is never lost; the gap is rebuildable by the standalone script
``scripts/derive_event_kb.py``.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3

from v2.core.publishing.org_resolve import resolve_org  # reuse Build-2 helper

__all__ = ["event_natural_key", "derive_event_kb", "resolve_org"]

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Stable derive key
# ─────────────────────────────────────────────────────────────────────────────

def event_natural_key(name: str, date: str) -> str:
    """Return a stable, normalized derive key for a GSA event.

    Normalization:
    - strip leading/trailing whitespace
    - collapse internal whitespace to a single space
    - lowercase

    The key is ``"{normalized_name}|{date}"``.  ``date`` is stored verbatim
    (YYYY-MM-DD); callers must not normalize it independently.
    """
    normalized = re.sub(r"\s+", " ", name.strip()).lower()
    return f"{normalized}|{date}"


# ─────────────────────────────────────────────────────────────────────────────
# Core derive function
# ─────────────────────────────────────────────────────────────────────────────

def derive_event_kb(
    ops_conn: sqlite3.Connection,
    kb_conn: sqlite3.Connection,
    *,
    org_slugs: tuple[str, ...] = ("gsa",),
) -> dict:
    """Derive ``event_info`` knowledge_items in KB from OPS events.

    For each ``org_slug`` in ``org_slugs``:
    1. Fetch active OPS events for that org.
    2. For each event, compute its ``natural_key``.
    3. Find an existing KB ``event_info`` item by ``natural_key`` (primary) or
       by ``event_id`` / ``ops_event_id`` (MED-8 transition fallback).
    4. If found: update metadata to back-fill ``natural_key``; activate the row.
       If not found: insert a new ``event_info`` item.
    5. Deactivate any derived ``event_info`` items whose ``natural_key`` is NOT
       in the current OPS event set (handles renamed / removed events).

    All KB writes are committed per org-slug iteration.  Returns a dict with
    ``{"created": int, "updated": int, "deactivated": int}`` counts.
    """
    totals = {"created": 0, "updated": 0, "deactivated": 0}

    for org_slug in org_slugs:
        try:
            org = resolve_org(kb_conn, org_slug)
        except ValueError:
            logger.warning("derive_event_kb: org slug %r not found in KB — skipped", org_slug)
            continue

        org_id = org["id"]

        # Fetch all events for this org from OPS
        ops_events = ops_conn.execute(
            "SELECT id, name, date, time, location FROM events WHERE org_slug=?",
            (org_slug,),
        ).fetchall()

        current_natural_keys: set[str] = set()

        for evt in ops_events:
            nk = event_natural_key(evt["name"], evt["date"])
            current_natural_keys.add(nk)

            # Build the KB item fields
            time_val = evt["time"] or "TBD"
            location_val = evt["location"] or "TBD"
            content = (
                f"{evt['name']} — {evt['date']} at {time_val}, {location_val}."
            )
            metadata = {
                "derived_from": "ops_event",
                "org_slug": org_slug,
                "ops_event_id": evt["id"],
                "date": evt["date"],
                "time": time_val,
                "natural_key": nk,
            }
            meta_json = json.dumps(metadata)

            # --- Primary match: by natural_key ---
            existing = kb_conn.execute(
                "SELECT id, metadata FROM knowledge_items "
                "WHERE type='event_info' AND org_id=? "
                "AND json_extract(metadata,'$.natural_key')=?",
                (org_id, nk),
            ).fetchone()

            # --- MED-8 fallback: match on legacy event_id / ops_event_id ---
            # ONLY for items that predate Phase 3 (no natural_key yet), so that
            # a renamed event is NOT matched here — it already has a natural_key
            # from a previous derive run, and the primary path above will not find
            # it (different natural_key), causing the old item to be deactivated
            # and a new item to be created with the new name.  Correct behavior.
            if existing is None:
                existing = kb_conn.execute(
                    "SELECT id, metadata FROM knowledge_items "
                    "WHERE type='event_info' AND org_id=? "
                    "AND json_extract(metadata,'$.natural_key') IS NULL "
                    "AND (json_extract(metadata,'$.event_id')=? OR "
                    "     json_extract(metadata,'$.ops_event_id')=?)",
                    (org_id, evt["id"], evt["id"]),
                ).fetchone()

            if existing is not None:
                kb_conn.execute(
                    "UPDATE knowledge_items "
                    "SET metadata=?, content=?, title=?, is_active=1, "
                    "    updated_at=datetime('now') "
                    "WHERE id=?",
                    (meta_json, content, evt["name"], existing["id"]),
                )
                totals["updated"] += 1
            else:
                kb_conn.execute(
                    "INSERT INTO knowledge_items"
                    "(org_id,type,title,content,metadata,created_by) "
                    "VALUES (?,?,?,?,?,?)",
                    (org_id, "event_info", evt["name"], content, meta_json,
                     "derive_event_kb"),
                )
                totals["created"] += 1

        # --- Deactivate stale derived items not in current OPS event set ---
        derived_rows = kb_conn.execute(
            "SELECT id, metadata FROM knowledge_items "
            "WHERE type='event_info' AND org_id=? AND is_active=1 "
            "AND json_extract(metadata,'$.derived_from')='ops_event'",
            (org_id,),
        ).fetchall()

        for row in derived_rows:
            try:
                m = json.loads(row["metadata"])
            except (json.JSONDecodeError, TypeError):
                continue
            if m.get("natural_key") not in current_natural_keys:
                kb_conn.execute(
                    "UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
                    "WHERE id=?",
                    (row["id"],),
                )
                totals["deactivated"] += 1

        kb_conn.commit()

    return totals
