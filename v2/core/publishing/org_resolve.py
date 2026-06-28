"""Org resolution helpers for the two-connection split-ops model.

``resolve_org(kb_conn, slug)`` is the single source of truth for looking up an
org from the Knowledge DB by slug. It fails loudly on ambiguity (>1 match) and
on missing slugs so the caller never silently falls back to a wrong org.

``OrgCache`` is a per-scheduler-tick cache: call ``get(kb_conn, slug)`` to look
up (and memoize) an org row, then ``clear()`` at the start of each tick so the
next tick sees fresh data (e.g. if an org was renamed or deactivated).

Phase 3's ``event_projection.py`` **reuses** this module — it does NOT define
its own version.
"""
from __future__ import annotations

import sqlite3


def resolve_org(kb_conn: sqlite3.Connection, slug: str) -> sqlite3.Row:
    """Return the ``organizations`` row for ``slug`` from the Knowledge DB.

    Raises ``ValueError`` when:
    - no org has the given slug (unknown slug)
    - more than one org has the given slug (LOW-11 — global uniqueness required
      for the cross-DB contract; ``organizations.slug`` is only
      ``UNIQUE(parent_id, slug)``, not globally unique)
    """
    rows = kb_conn.execute(
        "SELECT * FROM organizations WHERE slug=?", (slug,)
    ).fetchall()
    if len(rows) == 0:
        raise ValueError(f"no org with slug '{slug}'")
    if len(rows) > 1:
        raise ValueError(
            f">1 org with slug '{slug}' ({len(rows)} matches) — "
            "slug must be globally unique for cross-DB references"
        )
    return rows[0]


class OrgCache:
    """Per-tick memoization of ``resolve_org`` results.

    Call ``get(kb_conn, slug)`` to look up (and cache) an org. Call ``clear()``
    at the start of each tick so stale rows are never served across ticks.

    Designed to avoid the N+1 settings read on the publish hot path where
    ``build_post`` performs ~5 settings reads per post (MED-7).
    """

    def __init__(self) -> None:
        self._cache: dict[str, sqlite3.Row] = {}

    def get(self, kb_conn: sqlite3.Connection, slug: str) -> sqlite3.Row:
        """Return the cached row for ``slug``, resolving via ``kb_conn`` on miss."""
        if slug not in self._cache:
            self._cache[slug] = resolve_org(kb_conn, slug)
        return self._cache[slug]

    def clear(self) -> None:
        """Flush the cache (call at the start of each scheduler tick)."""
        self._cache.clear()
