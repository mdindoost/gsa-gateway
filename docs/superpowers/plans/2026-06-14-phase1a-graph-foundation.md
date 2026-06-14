# Phase 1a — Graph Foundation & Reconcile-Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the knowledge-graph layer (`nodes`/`edges`/`raw_pages`) and wire per-entity graph projection into `reconcile_entity` so the graph and text layers can never diverge — proven on existing structured extraction, with no new crawl coverage.

**Architecture:** A new `v2/core/graph/` package holds node/edge CRUD, an `organizations`-bridge (Org nodes reference `organizations`, not a parallel tree), and an entity→graph projection. `reconcile_entity` calls the projection *inside its existing transaction* (blocker B1), so a text-row change and its graph edges commit or roll back together. Deterministic extraction (`njit_adapter` + `EntityRecord`) is the authoritative source; the LLM is out of scope until Phase 2.

**Tech Stack:** Python 3.12, sqlite3 (STRICT tables, sqlite-vec already loaded by `get_connection`), pytest, BeautifulSoup4 (already used by the adapter).

**Spec:** `docs/superpowers/specs/2026-06-14-people-roles-kg-ingestion-design.md` (Phase 1a in §10).

---

## File structure

- Create `v2/core/graph/__init__.py` — package marker.
- Create `v2/core/graph/store.py` — node/edge CRUD (`upsert_node`, `upsert_edge`, `active_edge_ids_from`, `deactivate_edges`).
- Create `v2/core/graph/orgs.py` — the `organizations` bridge (`org_node_id`, `sync_org_nodes`).
- Create `v2/core/graph/project.py` — `category_from_titles`, `area_key`, `project_entity`.
- Create `v2/core/graph/raw.py` — `struct_hash`, `save_raw_page`.
- Modify `v2/core/database/schema.py` — add `RAW_PAGES`, `NODES`, `EDGES` DDL + indexes.
- Modify `v2/core/ingestion/reconcile.py` — `reconcile_entity` gains an optional `rec` and projects inside its transaction.
- Modify `scripts/ingest_faculty.py` — pass `rec=rec` to `reconcile_entity`.
- Tests: `v2/tests/test_graph_schema.py`, `test_graph_store.py`, `test_graph_orgs.py`, `test_graph_project.py`, `test_reconcile_graph.py`, `test_graph_raw.py`, `test_graph_fixture_koutis.py`.
- Fixture: `v2/tests/fixtures/koutis_profile.html`.

---

### Task 1: Graph schema (`raw_pages`, `nodes`, `edges`)

**Files:**
- Modify: `v2/core/database/schema.py` (add DDL constants near `KNOWLEDGE_FTS` ~line 216; register in `_TABLE_DDL` ~line 292 and `INDEXES` ~line 222)
- Test: `v2/tests/test_graph_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_graph_schema.py
from __future__ import annotations
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    yield c
    c.close()


def test_graph_tables_exist(conn):
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"raw_pages", "nodes", "edges"} <= names


def test_edges_category_check_rejects_bad_value(conn):
    conn.execute("INSERT INTO nodes(type,key,name,source) VALUES('Person','p/a','A','crawler')")
    conn.execute("INSERT INTO nodes(type,key,name,source) VALUES('Org','cs','CS','crawler')")
    a = conn.execute("SELECT id FROM nodes WHERE key='p/a'").fetchone()[0]
    o = conn.execute("SELECT id FROM nodes WHERE key='cs'").fetchone()[0]
    with pytest.raises(Exception):
        conn.execute("INSERT INTO edges(src_id,type,dst_id,category,source) "
                     "VALUES(?,?,?,?,?)", (a, "has_role", o, "president", "crawler"))


def test_node_key_is_unique_per_type(conn):
    conn.execute("INSERT INTO nodes(type,key,name,source) VALUES('Person','p/a','A','crawler')")
    with pytest.raises(Exception):
        conn.execute("INSERT INTO nodes(type,key,name,source) VALUES('Person','p/a','A2','crawler')")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/test_graph_schema.py -v`
Expected: FAIL — `test_graph_tables_exist` asserts tables that don't exist yet.

- [ ] **Step 3: Write minimal implementation**

In `v2/core/database/schema.py`, add these constants immediately after `KNOWLEDGE_FTS` (after ~line 216):

```python
# ── Group C — knowledge graph (STRICT) ───────────────────────────────────────

RAW_PAGES = """
CREATE TABLE IF NOT EXISTS raw_pages (
    url          TEXT PRIMARY KEY,
    content      TEXT NOT NULL,
    struct_hash  TEXT NOT NULL,
    status       TEXT NOT NULL,
    fetched_at   TEXT NOT NULL DEFAULT (datetime('now'))
) STRICT;
"""

NODES = """
CREATE TABLE IF NOT EXISTS nodes (
    id               INTEGER PRIMARY KEY,
    type             TEXT NOT NULL,
    key              TEXT NOT NULL,
    name             TEXT NOT NULL,
    attrs            TEXT NOT NULL DEFAULT '{}',
    source           TEXT NOT NULL,
    source_doc_id    INTEGER,
    ontology_version INTEGER NOT NULL DEFAULT 1,
    is_active        INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
) STRICT;
"""

EDGES = """
CREATE TABLE IF NOT EXISTS edges (
    id               INTEGER PRIMARY KEY,
    src_id           INTEGER NOT NULL REFERENCES nodes(id),
    type             TEXT NOT NULL,
    dst_id           INTEGER NOT NULL REFERENCES nodes(id),
    category         TEXT,
    area_source      TEXT,
    source_section   TEXT,
    attrs            TEXT NOT NULL DEFAULT '{}',
    source           TEXT NOT NULL,
    source_doc_id    INTEGER,
    ontology_version INTEGER NOT NULL DEFAULT 1,
    is_active        INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (category IS NULL OR category IN
           ('faculty','staff','admin','advisor','joint','emeritus'))
) STRICT;
"""
```

Register them. In `_TABLE_DDL` (the list at ~line 292), add `RAW_PAGES, NODES, EDGES` at the END (nodes before edges; edges FK nodes):

```python
_TABLE_DDL = [
    SCHEMA_MIGRATIONS,
    ORGANIZATIONS,
    KNOWLEDGE_ITEMS,
    KNOWLEDGE_VECTORS,
    KNOWLEDGE_FTS,
    POSTS,
    POST_TEMPLATES,
    POST_DELIVERIES,
    EVENTS,
    EVENT_REMINDERS,
    SETTINGS,
    RAW_PAGES,
    NODES,
    EDGES,
]
```

Append to `INDEXES` (the list at ~line 222), before its closing `]`:

```python
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_nodes_key   ON nodes(type, key);",
    "CREATE INDEX        IF NOT EXISTS idx_nodes_type  ON nodes(type, is_active);",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_triple ON edges(src_id, type, dst_id);",
    "CREATE INDEX        IF NOT EXISTS idx_edges_src   ON edges(src_id, is_active);",
    "CREATE INDEX        IF NOT EXISTS idx_edges_dst   ON edges(dst_id, type, is_active);",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest v2/tests/test_graph_schema.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add v2/core/database/schema.py v2/tests/test_graph_schema.py
git commit -m "feat(graph): nodes/edges/raw_pages schema (Phase 1a)"
```

---

### Task 2: Graph store CRUD

**Files:**
- Create: `v2/core/graph/__init__.py`
- Create: `v2/core/graph/store.py`
- Test: `v2/tests/test_graph_store.py`

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_graph_store.py
from __future__ import annotations
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.graph.store import (
    active_edge_ids_from, deactivate_edges, upsert_edge, upsert_node)


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    yield c
    c.close()


def test_upsert_node_is_idempotent_and_updates(conn):
    n1 = upsert_node(conn, type="Person", key="p/a", name="Ann", attrs={"email": "a@x"})
    n2 = upsert_node(conn, type="Person", key="p/a", name="Ann B", attrs={"email": "b@x"})
    assert n1 == n2  # same row
    row = conn.execute("SELECT name, attrs FROM nodes WHERE id=?", (n1,)).fetchone()
    assert row[0] == "Ann B" and '"email": "b@x"' in row[1]


def test_upsert_edge_idempotent_and_active_set(conn):
    p = upsert_node(conn, type="Person", key="p/a", name="Ann")
    o = upsert_node(conn, type="Org", key="cs", name="CS", attrs={"org_id": 5})
    e1 = upsert_edge(conn, src_id=p, type="has_role", dst_id=o, category="faculty")
    e2 = upsert_edge(conn, src_id=p, type="has_role", dst_id=o, category="faculty")
    assert e1 == e2
    assert active_edge_ids_from(conn, p) == {e1}


def test_deactivate_edges(conn):
    p = upsert_node(conn, type="Person", key="p/a", name="Ann")
    o = upsert_node(conn, type="Org", key="cs", name="CS")
    e = upsert_edge(conn, src_id=p, type="has_role", dst_id=o, category="faculty")
    deactivate_edges(conn, {e})
    assert active_edge_ids_from(conn, p) == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/test_graph_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'v2.core.graph'`.

- [ ] **Step 3: Write minimal implementation**

```python
# v2/core/graph/__init__.py
```

```python
# v2/core/graph/store.py
"""Node/edge CRUD for the knowledge graph. All writes assume the caller manages
the transaction (the reconcile step runs these inside its own `with conn:`)."""
from __future__ import annotations

import json
import sqlite3


def upsert_node(conn: sqlite3.Connection, *, type: str, key: str, name: str,
                attrs: dict | None = None, source: str = "crawler",
                source_doc_id: int | None = None, ontology_version: int = 1) -> int:
    """Insert or update a node by its (type, key) identity; returns the node id and
    (re)activates it."""
    a = json.dumps(attrs or {})
    row = conn.execute("SELECT id FROM nodes WHERE type=? AND key=?", (type, key)).fetchone()
    if row:
        nid = row[0]
        conn.execute(
            "UPDATE nodes SET name=?, attrs=?, source=?, source_doc_id=?, "
            "ontology_version=?, is_active=1, updated_at=datetime('now') WHERE id=?",
            (name, a, source, source_doc_id, ontology_version, nid))
        return nid
    cur = conn.execute(
        "INSERT INTO nodes(type,key,name,attrs,source,source_doc_id,ontology_version) "
        "VALUES(?,?,?,?,?,?,?)", (type, key, name, a, source, source_doc_id, ontology_version))
    return cur.lastrowid


def upsert_edge(conn: sqlite3.Connection, *, src_id: int, type: str, dst_id: int,
                category: str | None = None, area_source: str | None = None,
                source_section: str | None = None, attrs: dict | None = None,
                source: str = "crawler", source_doc_id: int | None = None,
                ontology_version: int = 1) -> int:
    """Insert or update an edge by its (src_id, type, dst_id) identity; returns the id."""
    a = json.dumps(attrs or {})
    row = conn.execute("SELECT id FROM edges WHERE src_id=? AND type=? AND dst_id=?",
                       (src_id, type, dst_id)).fetchone()
    if row:
        eid = row[0]
        conn.execute(
            "UPDATE edges SET category=?, area_source=?, source_section=?, attrs=?, "
            "source=?, source_doc_id=?, ontology_version=?, is_active=1, "
            "updated_at=datetime('now') WHERE id=?",
            (category, area_source, source_section, a, source, source_doc_id,
             ontology_version, eid))
        return eid
    cur = conn.execute(
        "INSERT INTO edges(src_id,type,dst_id,category,area_source,source_section,"
        "attrs,source,source_doc_id,ontology_version) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (src_id, type, dst_id, category, area_source, source_section, a, source,
         source_doc_id, ontology_version))
    return cur.lastrowid


def active_edge_ids_from(conn: sqlite3.Connection, src_id: int,
                         type: str | None = None, source: str = "crawler") -> set[int]:
    """Active edge ids leaving ``src_id`` (optionally one type), scoped to a source."""
    q = "SELECT id FROM edges WHERE src_id=? AND is_active=1 AND source=?"
    p: list = [src_id, source]
    if type:
        q += " AND type=?"
        p.append(type)
    return {r[0] for r in conn.execute(q, p)}


def deactivate_edges(conn: sqlite3.Connection, edge_ids) -> None:
    conn.executemany(
        "UPDATE edges SET is_active=0, updated_at=datetime('now') WHERE id=?",
        [(e,) for e in edge_ids])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest v2/tests/test_graph_store.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add v2/core/graph/__init__.py v2/core/graph/store.py v2/tests/test_graph_store.py
git commit -m "feat(graph): node/edge upsert + deactivate CRUD"
```

---

### Task 3: Organizations bridge (B2 — reference, don't mirror)

**Files:**
- Create: `v2/core/graph/orgs.py`
- Test: `v2/tests/test_graph_orgs.py`

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_graph_orgs.py
from __future__ import annotations
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.graph.orgs import org_node_id, sync_org_nodes


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(4,'YWCC','ywcc','college')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(5,4,'Computer Science','computer-science','department')")
    c.commit()
    yield c
    c.close()


def test_org_node_bridges_organizations_id(conn):
    nid = org_node_id(conn, 5)
    row = conn.execute("SELECT type,key,name,attrs FROM nodes WHERE id=?", (nid,)).fetchone()
    assert row[0] == "Org" and row[1] == "computer-science" and row[2] == "Computer Science"
    assert json.loads(row[3])["org_id"] == 5


def test_sync_builds_part_of_from_parent_id(conn):
    sync_org_nodes(conn)
    cs = org_node_id(conn, 5)
    ywcc = org_node_id(conn, 4)
    e = conn.execute("SELECT id FROM edges WHERE src_id=? AND type='part_of' AND dst_id=?",
                     (cs, ywcc)).fetchone()
    assert e is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/test_graph_orgs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'v2.core.graph.orgs'`.

- [ ] **Step 3: Write minimal implementation**

```python
# v2/core/graph/orgs.py
"""The org bridge: `organizations` is the one authoritative tree; Org nodes only
reference it (key=slug, attrs.org_id) and `part_of` is derived from parent_id."""
from __future__ import annotations

import sqlite3

from v2.core.graph.store import upsert_edge, upsert_node


def org_node_id(conn: sqlite3.Connection, org_id: int) -> int:
    """Get/create the Org node that bridges ``organizations.id``."""
    o = conn.execute("SELECT id,name,slug FROM organizations WHERE id=?", (org_id,)).fetchone()
    if not o:
        raise ValueError(f"no organization id={org_id}")
    return upsert_node(conn, type="Org", key=o["slug"], name=o["name"],
                       attrs={"org_id": o["id"]})


def sync_org_nodes(conn: sqlite3.Connection) -> None:
    """Project every active organization to an Org node and a `part_of` edge to its parent."""
    rows = conn.execute(
        "SELECT id, parent_id FROM organizations WHERE is_active=1").fetchall()
    for o in rows:
        org_node_id(conn, o["id"])
    for o in rows:
        if o["parent_id"]:
            upsert_edge(conn, src_id=org_node_id(conn, o["id"]), type="part_of",
                        dst_id=org_node_id(conn, o["parent_id"]))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest v2/tests/test_graph_orgs.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add v2/core/graph/orgs.py v2/tests/test_graph_orgs.py
git commit -m "feat(graph): organizations bridge (Org nodes reference, not mirror)"
```

---

### Task 4: Entity → graph projection

**Files:**
- Create: `v2/core/graph/project.py`
- Test: `v2/tests/test_graph_project.py`

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_graph_project.py
from __future__ import annotations
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.graph.project import category_from_titles, project_entity
from v2.core.graph.store import active_edge_ids_from
from v2.core.ingestion.entity import EntityRecord


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(5,'Computer Science','computer-science','department')")
    c.commit()
    yield c
    c.close()


def test_category_from_titles():
    assert category_from_titles(["Associate Professor, Computer Science"]) == "faculty"
    assert category_from_titles(["Professor", "Associate Dean for Academic Affairs"]) == "faculty"
    assert category_from_titles(["Dean, Ying Wu College of Computing"]) == "admin"
    assert category_from_titles(["Director of Marketing and Communications"]) == "staff"


def _rec(areas):
    return EntityRecord(entity_id="p/ikoutis", name="Ioannis Koutis", org="Computer Science",
                        titles=["Associate Professor, Computer Science"],
                        research_areas=areas,
                        contact={"email": "ioannis.koutis@njit.edu", "office": "4105 GITC"})


def test_project_creates_person_role_and_research_edges(conn):
    pid = project_entity(conn, _rec(["Spectral graph theory", "Graph sparsification"]), 5)
    # has_role edge to CS, category faculty
    hr = conn.execute("SELECT category FROM edges WHERE src_id=? AND type='has_role'", (pid,)).fetchall()
    assert [r[0] for r in hr] == ["faculty"]
    # two structured researches edges
    rs = conn.execute("SELECT area_source FROM edges WHERE src_id=? AND type='researches' "
                      "AND is_active=1", (pid,)).fetchall()
    assert len(rs) == 2 and all(r[0] == "structured" for r in rs)
    # person carries contact attrs
    attrs = conn.execute("SELECT attrs FROM nodes WHERE id=?", (pid,)).fetchone()[0]
    assert "ioannis.koutis@njit.edu" in attrs and "4105 GITC" in attrs


def test_reproject_with_dropped_area_deactivates_stale_edge(conn):
    pid = project_entity(conn, _rec(["Spectral graph theory", "Graph sparsification"]), 5)
    before = active_edge_ids_from(conn, pid, type="researches")
    assert len(before) == 2
    project_entity(conn, _rec(["Spectral graph theory"]), 5)  # dropped one area
    after = active_edge_ids_from(conn, pid, type="researches")
    assert len(after) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/test_graph_project.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'v2.core.graph.project'`.

- [ ] **Step 3: Write minimal implementation**

```python
# v2/core/graph/project.py
"""Project one EntityRecord into the graph: a Person node, its home has_role edge,
and structured `researches` edges. Deterministic only (the LLM is Phase 2). Runs
inside the reconcile transaction so it is atomic with the text rows (B1)."""
from __future__ import annotations

import re
import sqlite3

from v2.core.graph.orgs import org_node_id
from v2.core.graph.store import (
    active_edge_ids_from, deactivate_edges, upsert_edge, upsert_node)
from v2.core.ingestion.entity import EntityRecord

# Order matters: a faculty title wins even when an admin title is also present
# (e.g. "Professor" + "Associate Dean" -> faculty home appointment). 1b refines
# this with the listing section.
_CATEGORY_RULES = [
    (re.compile(r"\b(professor|lecturer)\b", re.I), "faculty"),
    (re.compile(r"\bemerit", re.I), "emeritus"),
    (re.compile(r"\bdean\b", re.I), "admin"),
    (re.compile(r"\badvis", re.I), "advisor"),
    (re.compile(r"\b(director|coordinator|designer|administrat|manager|assistant to)\b", re.I), "staff"),
]


def category_from_titles(titles: list[str]) -> str:
    hay = " ; ".join(titles)
    for rx, cat in _CATEGORY_RULES:
        if rx.search(hay):
            return cat
    return "staff"


def area_key(area: str) -> str:
    """Case-folded grouping key for a ResearchArea node (display canonicalization for
    the facets is Phase 3, reusing skills._canonical)."""
    return area.strip().casefold()


def project_entity(conn: sqlite3.Connection, rec: EntityRecord, org_id: int,
                   source: str = "crawler") -> int:
    """Rebuild this entity's graph to match ``rec``; deactivate its crawler edges that
    are no longer present. Returns the Person node id."""
    attrs = {k: v for k, v in {
        "email": rec.contact.get("email"),
        "phone": rec.contact.get("phone"),
        "office": rec.contact.get("office"),
        "website": rec.links.get("website"),
    }.items() if v}
    pid = upsert_node(conn, type="Person", key=rec.entity_id, name=rec.name,
                      attrs=attrs, source=source)

    keep: set[int] = set()
    keep.add(upsert_edge(
        conn, src_id=pid, type="has_role", dst_id=org_node_id(conn, org_id),
        category=category_from_titles(rec.titles),
        attrs={"titles": rec.titles, "is_primary": True}, source=source))

    seen: set[str] = set()
    for area in rec.research_areas:
        a = area.strip()
        if not a:
            continue
        k = area_key(a)
        if k in seen:
            continue
        seen.add(k)
        anode = upsert_node(conn, type="ResearchArea", key=k, name=a, source=source)
        keep.add(upsert_edge(conn, src_id=pid, type="researches", dst_id=anode,
                             area_source="structured", source=source))

    deactivate_edges(conn, active_edge_ids_from(conn, pid, source=source) - keep)
    return pid
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest v2/tests/test_graph_project.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add v2/core/graph/project.py v2/tests/test_graph_project.py
git commit -m "feat(graph): entity->graph projection (person/role/structured areas)"
```

---

### Task 5: Wire projection into `reconcile_entity` (Blocker B1)

**Files:**
- Modify: `v2/core/ingestion/reconcile.py:79-139`
- Test: `v2/tests/test_reconcile_graph.py`

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_reconcile_graph.py
from __future__ import annotations
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.graph.store import active_edge_ids_from
from v2.core.ingestion.decompose import decompose
from v2.core.ingestion.entity import EntityRecord
from v2.core.ingestion.reconcile import reconcile_entity


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(5,'Computer Science','computer-science','department')")
    c.commit()
    yield c
    c.close()


def _rec(areas):
    return EntityRecord(entity_id="p/ikoutis", name="Ioannis Koutis", org="Computer Science",
                        source_url="https://people.njit.edu/profile/ikoutis",
                        titles=["Associate Professor, Computer Science"],
                        research_areas=areas)


def test_reconcile_populates_graph_in_same_call(conn):
    rec = _rec(["Spectral graph theory", "Graph sparsification"])
    reconcile_entity(conn, 5, rec.entity_id, decompose(rec), rec=rec)
    pid = conn.execute("SELECT id FROM nodes WHERE type='Person' AND key='p/ikoutis'").fetchone()
    assert pid is not None
    n = conn.execute("SELECT COUNT(*) FROM edges WHERE type='researches' AND is_active=1").fetchone()[0]
    assert n == 2


def test_text_and_graph_stay_consistent_on_dropped_area(conn):
    rec2 = _rec(["Spectral graph theory", "Graph sparsification"])
    reconcile_entity(conn, 5, rec2.entity_id, decompose(rec2), rec=rec2)
    rec1 = _rec(["Spectral graph theory"])             # one area dropped
    reconcile_entity(conn, 5, rec1.entity_id, decompose(rec1), rec=rec1)
    pid = conn.execute("SELECT id FROM nodes WHERE key='p/ikoutis'").fetchone()[0]
    # graph dropped the stale researches edge ...
    assert len(active_edge_ids_from(conn, pid, type="researches")) == 1
    # ... in lockstep with the text layer's active research_areas item content
    ra = conn.execute("SELECT content FROM knowledge_items WHERE type='research_areas' "
                      "AND is_active=1 AND json_extract(metadata,'$.entity_id')='p/ikoutis'").fetchone()
    assert "Graph sparsification" not in ra[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/test_reconcile_graph.py -v`
Expected: FAIL — `reconcile_entity()` has no `rec` argument (TypeError).

- [ ] **Step 3: Write minimal implementation**

In `v2/core/ingestion/reconcile.py`, change the `reconcile_entity` signature and add the projection call at the END of the `with conn:` block (so it commits in the same transaction). Replace the function definition line (line 79-80) and add the projection just before the function returns.

Change the signature:

```python
def reconcile_entity(conn, org_id: int, entity_id: str, items: list[KItem],
                     created_by: str = "ingest", rec=None) -> ReconcileResult:
```

Then, inside the `with conn:` block, immediately AFTER the "absent now -> deactivate" loop (after line 137, still inside `with conn:`), add:

```python
        # B1: project the entity into the graph in the SAME transaction, so the
        # graph layer can never diverge from the text layer. Deterministic only.
        if rec is not None:
            from v2.core.graph.project import project_entity
            project_entity(conn, rec, org_id, source="crawler")
```

(The lazy import avoids a module-load cycle and keeps `reconcile` importable without the graph package.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest v2/tests/test_reconcile_graph.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/reconcile.py v2/tests/test_reconcile_graph.py
git commit -m "feat(ingestion): project entity into graph inside reconcile txn (B1)"
```

---

### Task 6: `raw_pages` capture + structural hash

**Files:**
- Create: `v2/core/graph/raw.py`
- Test: `v2/tests/test_graph_raw.py`

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_graph_raw.py
from __future__ import annotations
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.graph.raw import save_raw_page, struct_hash


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    yield c
    c.close()


def test_struct_hash_ignores_byte_noise_outside_structure():
    a = "<html><body><div>Areas: graph</div><!-- nonce 111 --></body></html>"
    b = "<html><body><div>Areas: graph</div><!-- nonce 999 --></body></html>"
    assert struct_hash(a) == struct_hash(b)


def test_struct_hash_changes_when_content_changes():
    a = "<html><body><div>Areas: graph</div></body></html>"
    b = "<html><body><div>Areas: graphs and trees</div></body></html>"
    assert struct_hash(a) != struct_hash(b)


def test_save_raw_page_upserts(conn):
    save_raw_page(conn, "http://x/p", "<html><body>one</body></html>")
    save_raw_page(conn, "http://x/p", "<html><body>two</body></html>")
    rows = conn.execute("SELECT content FROM raw_pages WHERE url='http://x/p'").fetchall()
    assert len(rows) == 1 and "two" in rows[0][0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/test_graph_raw.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'v2.core.graph.raw'`.

- [ ] **Step 3: Write minimal implementation**

```python
# v2/core/graph/raw.py
"""Verbatim page snapshots for change-detection (M2: hash the normalized structure,
not raw bytes). Phase 1a stores snapshots; the skip-re-extract-on-unchanged-hash
logic is wired in Phase 1b."""
from __future__ import annotations

import hashlib
import sqlite3

from bs4 import BeautifulSoup


def struct_hash(html: str) -> str:
    """SHA-256 of the normalized text structure — comments / scripts / whitespace
    noise don't change it, real content does."""
    soup = BeautifulSoup(html or "", "html.parser")
    for t in soup(["script", "style", "nav", "footer", "header"]):
        t.decompose()
    norm = " ".join(soup.get_text(" ").split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def save_raw_page(conn: sqlite3.Connection, url: str, content: str,
                  status: str = "ok") -> str:
    """Upsert a page snapshot; returns its structural hash (empty for non-ok status)."""
    h = struct_hash(content) if status == "ok" else ""
    conn.execute(
        "INSERT INTO raw_pages(url,content,struct_hash,status,fetched_at) "
        "VALUES(?,?,?,?,datetime('now')) "
        "ON CONFLICT(url) DO UPDATE SET content=excluded.content, "
        "struct_hash=excluded.struct_hash, status=excluded.status, "
        "fetched_at=datetime('now')",
        (url, content, h, status))
    return h
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest v2/tests/test_graph_raw.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add v2/core/graph/raw.py v2/tests/test_graph_raw.py
git commit -m "feat(graph): raw_pages snapshot + structural hash (M2)"
```

---

### Task 7: End-to-end fixture test (saved CS profile → graph)

**Files:**
- Create: `v2/tests/fixtures/koutis_profile.html` (captured once, offline thereafter — m7)
- Test: `v2/tests/test_graph_fixture_koutis.py`

- [ ] **Step 1: Capture the fixture (one-time)**

Run this once to save the real profile HTML as an offline fixture:

```bash
.venv/bin/python - <<'PY'
import urllib.request, pathlib
UA="GSA-Gateway-Bot/1.0 (+https://github.com/mdindoost/gsa-gateway)"
req=urllib.request.Request("https://people.njit.edu/profile/ikoutis", headers={"User-Agent":UA})
html=urllib.request.urlopen(req,timeout=30).read().decode("utf-8","ignore")
p=pathlib.Path("v2/tests/fixtures"); p.mkdir(parents=True, exist_ok=True)
(p/"koutis_profile.html").write_text(html, encoding="utf-8")
print("saved", len(html), "chars")
PY
```

Expected: `saved ~10000 chars`.

- [ ] **Step 2: Write the failing test**

```python
# v2/tests/test_graph_fixture_koutis.py
from __future__ import annotations
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import create_all
from v2.core.ingestion.decompose import decompose
from v2.core.ingestion.njit_adapter import parse_entity
from v2.core.ingestion.reconcile import reconcile_entity

FIXTURE = Path(__file__).parent / "fixtures" / "koutis_profile.html"


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(5,'Computer Science','computer-science','department')")
    c.commit()
    yield c
    c.close()


def test_saved_cs_profile_populates_graph_consistently(conn):
    html = FIXTURE.read_text(encoding="utf-8")
    rec = parse_entity("https://people.njit.edu/profile/ikoutis", html)
    reconcile_entity(conn, 5, rec.entity_id, decompose(rec), rec=rec)

    person = conn.execute(
        "SELECT id,name,attrs FROM nodes WHERE type='Person' AND key=?",
        (rec.entity_id,)).fetchone()
    assert person is not None and person["name"] == "Ioannis Koutis"
    assert "ioannis.koutis@njit.edu" in person["attrs"]

    # home appointment: faculty, in CS
    role = conn.execute(
        "SELECT category FROM edges e JOIN nodes o ON o.id=e.dst_id "
        "WHERE e.src_id=? AND e.type='has_role' AND o.key='computer-science'",
        (person["id"],)).fetchone()
    assert role is not None and role["category"] == "faculty"

    # at least one structured research area, and every researches edge is structured
    rs = conn.execute(
        "SELECT area_source FROM edges WHERE src_id=? AND type='researches' AND is_active=1",
        (person["id"],)).fetchall()
    assert len(rs) >= 1 and all(r["area_source"] == "structured" for r in rs)
```

- [ ] **Step 3: Run test to verify it passes**

Run: `.venv/bin/python -m pytest v2/tests/test_graph_fixture_koutis.py -v`
Expected: PASS. (If it fails because the live page changed when captured, inspect `rec` — the adapter, not the graph, is the variable.)

- [ ] **Step 4: Commit**

```bash
git add v2/tests/fixtures/koutis_profile.html v2/tests/test_graph_fixture_koutis.py
git commit -m "test(graph): saved CS profile fixture -> graph consistency (Phase 1a)"
```

---

### Task 8: Wire the live ingest caller to populate the graph

**Files:**
- Modify: `scripts/ingest_faculty.py:197` (the `reconcile_entity(...)` call inside `commit`)
- Test: full suite (no new unit test — covered by Task 5/7)

- [ ] **Step 1: Pass `rec` through**

In `scripts/ingest_faculty.py`, inside `commit`, the loop is `for rec, items in items_by_entity:` and it calls `reconcile_entity(conn, org_id, rec.entity_id, items)`. Add `rec=rec`:

```python
            res = reconcile_entity(conn, org_id, rec.entity_id, items, rec=rec)
```

- [ ] **Step 2: Run the full suite to confirm no regression**

Run: `.venv/bin/python -m pytest bot/tests/ v2/tests/ -q`
Expected: the graph tests pass; only the known pre-existing failures remain
(`test_local_server.py` CSRF/403, `test_departments.py::test_ds_is_registered_but_flagged_js`).

- [ ] **Step 3: Commit**

```bash
git add scripts/ingest_faculty.py
git commit -m "feat(ingestion): live faculty ingest populates the graph (rec passed to reconcile)"
```

---

## Phase 1a done — what exists now

- `nodes`/`edges`/`raw_pages` tables; Org nodes bridge `organizations` (B2); `part_of` derived from `parent_id`.
- A faculty ingest now writes Person + `has_role`(category) + structured `researches` edges **in the same transaction** as the text rows (B1), proven consistent on a saved CS fixture.
- No new crawl coverage, no LLM, no retrieval changes — those are Phases 1b / 2 / 3.

## Not in this plan (later phases)

- Hub-first discovery, College Administration, dual/joint roles, section-scoped deactivation → **Phase 1b**.
- LLM prose enrichment (additive `researches` edges) → **Phase 2**.
- Graph-traversal retrieval skills, retire old `faculty_in_department`, re-point P2.5 facets → **Phase 3**.
