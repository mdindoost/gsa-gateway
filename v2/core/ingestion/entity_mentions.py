"""Offline entity-mentions tagger — resolve which Person node(s) a KB item is ABOUT.

Deterministic gate (no LLM by default): title fast-path -> both-names whole-word ->
anti-roster (reject list/roster pages) -> namesake abstain. Writes a many-to-many
`entity_mentions` table. Spec docs/superpowers/specs/2026-07-07-...-tagging-design.md
§5 / §15 R3/R5/R6/R10.
"""
from __future__ import annotations

import re
from collections import Counter, namedtuple

from v2.core.retrieval.entity import normalize_person_name

PersonName = namedtuple("PersonName", "node_id node_key last first")

# Tier-2 mention prose ONLY (R5/R6: no `award` — served id-linked; no `about` — already in card).
IN_SCOPE_TYPES = ("faq", "news", "event_info")


def _first_last(normalized: str) -> tuple[str, str]:
    parts = normalized.split()
    return (parts[-1], parts[0]) if len(parts) >= 2 else (normalized, "")


def load_person_index(conn) -> list[PersonName]:
    out = []
    for nid, key, raw in conn.execute(
            "SELECT id, key, name FROM nodes WHERE type='Person' AND is_active=1"):
        last, first = _first_last(normalize_person_name(raw))
        if last:
            out.append(PersonName(nid, key, last, first))
    return out


def _whole(word: str, text: str) -> list[int]:
    if not word:
        return []
    return [m.start() for m in re.finditer(r"\b" + re.escape(word) + r"\b", text, re.I)]


def _both_names_hits(p: PersonName, text: str) -> int:
    """Occurrences of the person requiring BOTH first and last present (whole-word).
    Returns the last-name hit count when the first name also appears, else 0."""
    if not (p.first and _whole(p.first, text)):
        return 0
    return len(_whole(p.last, text))


def resolve_item(title: str, content: str, people: list[PersonName], roster_n: int = 5):
    """Return [(person, basis, confidence), ...] for people this item is ABOUT; [] for
    roster/none. basis in {'title','both_names'}."""
    title = title or ""
    content = content or ""
    fullkeys = Counter((p.last.lower(), p.first.lower()) for p in people)
    # everyone present at all in the body (both-names) — for the anti-roster other-count
    body_present = [p for p in people if _both_names_hits(p, content) > 0]
    accepted = []
    for p in people:
        # TITLE fast-path
        if p.first and _whole(p.last, title) and _whole(p.first, title):
            accepted.append((p, "title", 1.0))
            continue
        hits = _both_names_hits(p, content)
        if hits == 0:
            continue
        # namesake abstain (no corroboration model in phase 1 -> silence, never a wrong tag)
        if fullkeys[(p.last.lower(), p.first.lower())] > 1:
            continue
        # anti-roster: target named once + many OTHER known people present -> list/roster page
        others = sum(1 for q in body_present if q.node_key != p.node_key)
        if hits == 1 and others >= roster_n:
            continue
        accepted.append((p, "both_names", 0.7))
    return accepted


def stable_key_of(item_id: int, natural_key) -> str:
    """R3: crawler rows key by natural_key (survive re-ingest); natural_key-less curated
    rows fall back to a stable id: key (manual rows are not re-ingested)."""
    return natural_key or f"id:{item_id}"


def build_mentions(conn, *, roster_n: int = 5, audit_writer=None) -> list[dict]:
    """Resolve all in-scope active KB items to the Person node(s) they are ABOUT.
    Returns accepted rows as dicts (does NOT write). Optional audit_writer (csv.writer)
    gets one row per accepted (item, person) pair."""
    people = load_person_index(conn)
    q = ("SELECT id, title, content, json_extract(metadata,'$.natural_key') "
         "FROM knowledge_items WHERE is_active=1 AND type IN (%s)"
         % ",".join("?" * len(IN_SCOPE_TYPES)))
    rows: list[dict] = []
    for item_id, title, content, nkey in conn.execute(q, IN_SCOPE_TYPES):
        for person, basis, conf in resolve_item(title or "", content or "", people, roster_n):
            rows.append({
                "stable_key": stable_key_of(item_id, nkey),
                "node_key": person.node_key, "item_id": item_id, "node_id": person.node_id,
                "match_basis": basis, "confidence": conf,
                "title": title or "", "person": f"{person.first} {person.last}".strip(),
            })
            if audit_writer:
                audit_writer.writerow([item_id, title, f"{person.first} {person.last}".strip(),
                                       basis, conf])
    return rows


def write_mentions(conn, rows: list[dict]) -> int:
    """Full rebuild in the tagger's OWN created_by scope (never touches foreign rows)."""
    conn.execute("DELETE FROM entity_mentions WHERE created_by='entity_mentions_tagger'")
    conn.executemany(
        "INSERT OR REPLACE INTO entity_mentions"
        "(stable_key,node_key,item_id,node_id,match_basis,confidence) VALUES(?,?,?,?,?,?)",
        [(r["stable_key"], r["node_key"], r["item_id"], r["node_id"],
          r["match_basis"], r["confidence"]) for r in rows])
    return len(rows)
