# People & Roles Dashboard Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the admin add / edit / remove people and arbitrary free-text roles for any org from the dashboard (with an optional embedded bio), and make every role type answerable.

**Architecture:** A tested core module (`people_editor.py`) does the graph+KB writes by reusing existing helpers (`ensure_org`, `project_appointment`, `deactivate_edges`, KB insert), `source='dashboard'`. Two thin `local_server` endpoints wrap it. A new `people_in_org` structured skill + router route makes all role types answerable. The dashboard People tab gains the editor UI.

**Tech Stack:** Python 3.11, sqlite3, the v2 graph layer, `http.server` (local_server), vanilla JS (dashboard), pytest. `source venv/bin/activate` (or `.venv`) before pytest.

**Spec:** `docs/superpowers/specs/2026-06-15-people-roles-dashboard-editor-design.md`

---

## File Structure

- `v2/core/retrieval/skills.py` — **modify**: add `people_in_org`.
- `v2/core/retrieval/structured_answer.py` — **modify**: run + format for `people_in_org`.
- `v2/core/retrieval/router.py` — **modify**: route "who works at/in <org>" → `people_in_org`.
- `v2/core/ingestion/people_editor.py` — **create**: `add_or_edit_person`, `remove_person_role` (pure graph+KB writes; caller commits).
- `v2/local_server.py` — **modify**: `POST /people`, `POST /people/remove` (thin wrappers).
- `dashboard/app.js` — **modify**: People tab editor UI (org picker, table Edit/Remove, Add/Edit form).
- Tests: `v2/tests/test_skills_people_in_org.py`, `v2/tests/test_people_editor.py`, `v2/tests/test_local_server_people.py`, plus additions to `v2/tests/test_router_officers.py`, `v2/tests/test_structured_answer_officers.py`.

---

## Task 1: `people_in_org` skill

**Files:** Modify `v2/core/retrieval/skills.py` (add after `officers_in_org`). Test: create `v2/tests/test_skills_people_in_org.py`.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_skills_people_in_org.py
from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment
from v2.core.retrieval.skills import people_in_org, officers_in_org


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.commit()
    yield c
    c.close()


def test_people_in_org_returns_all_role_types(conn):
    gs = ensure_org(conn, "graduate-studies", "Graduate Studies", parent_slug="njit", type="office")
    sync_org_nodes(conn)
    project_appointment(conn, person_key="dashboard/graduate-studies/sotirios-ziavras",
                        name="Sotirios Ziavras", org_id=gs, category="admin",
                        titles=["Dean of Graduate Studies"], source_section="manual", source="dashboard")
    project_appointment(conn, person_key="dashboard/graduate-studies/ester-flaim",
                        name="Ester Flaim", org_id=gs, category="staff",
                        titles=["Assistant Director"], source_section="manual", source="dashboard")
    nt = [(n, t) for n, t, _ in people_in_org(conn, gs)]
    assert ("Sotirios Ziavras", "Dean of Graduate Studies") in nt   # admin
    assert ("Ester Flaim", "Assistant Director") in nt              # staff
    # officers_in_org (officer/deprep only) finds NEITHER of these
    assert officers_in_org(conn, gs) == []


def test_people_in_org_excludes_inactive(conn):
    gs = ensure_org(conn, "graduate-studies", "Graduate Studies", parent_slug="njit", type="office")
    sync_org_nodes(conn)
    project_appointment(conn, person_key="dashboard/graduate-studies/x", name="X",
                        org_id=gs, category="advisor", titles=["Advisor"],
                        source_section="manual", source="dashboard")
    conn.execute("UPDATE edges SET is_active=0 WHERE type='has_role'")
    assert people_in_org(conn, gs) == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `source venv/bin/activate 2>/dev/null || source .venv/bin/activate; python -m pytest v2/tests/test_skills_people_in_org.py -q`
Expected: FAIL — `ImportError: cannot import name 'people_in_org'`.

- [ ] **Step 3: Implement the skill** (add immediately after `officers_in_org` in `v2/core/retrieval/skills.py`)

```python
def people_in_org(conn: sqlite3.Connection, org_id: int) -> list[tuple[str, str, str | None]]:
    """(name, title, email) for EVERY active person with any role directly in this org —
    not just officers (cf. officers_in_org). Answers 'who works at/in <org>'. Title is the
    first of the edge's attrs.titles (falls back to category); email from the Person node.
    Sorted by name."""
    rows = conn.execute(
        "SELECT p.name, e.attrs, e.category, p.attrs FROM edges e "
        "JOIN nodes p ON p.id=e.src_id "
        "JOIN nodes o ON o.id=e.dst_id AND o.is_active=1 "
        "WHERE e.type='has_role' AND e.is_active=1 AND p.is_active=1 "
        "AND json_extract(o.attrs,'$.org_id')=?",
        (org_id,)).fetchall()
    out: list[tuple[str, str, str | None]] = []
    for name, eattrs, category, pattrs in rows:
        titles = (json.loads(eattrs) if eattrs else {}).get("titles") or []
        email = (json.loads(pattrs) if pattrs else {}).get("email")
        out.append((name, titles[0] if titles else category, email))
    return sorted(set(out), key=lambda r: r[0])
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest v2/tests/test_skills_people_in_org.py v2/tests/test_skills_officers.py -q`
Expected: PASS (new + existing officer tests).

- [ ] **Step 5: Commit**

```bash
git add v2/core/retrieval/skills.py v2/tests/test_skills_people_in_org.py
git commit -m "feat(retrieval): people_in_org skill (all role types, not just officers)"
```

---

## Task 2: Wire `people_in_org` into structured_answer

**Files:** Modify `v2/core/retrieval/structured_answer.py`. Test: add to `v2/tests/test_structured_answer_officers.py`.

- [ ] **Step 1: Write the failing test** (append to `v2/tests/test_structured_answer_officers.py`)

```python
def test_people_in_org_answer_lists_all_roles(conn):
    # conn fixture seeds GSA with 2 officers; add a staff person, then ask "who works at GSA"
    from v2.core.graph.project import project_appointment
    from v2.core.retrieval.router import Route
    gsa = conn.execute("SELECT id FROM organizations WHERE slug='gsa'").fetchone()[0]
    project_appointment(conn, person_key="dashboard/gsa/sam-staff", name="Sam Staff",
                        org_id=gsa, category="staff", titles=["Office Manager"],
                        source_section="manual", source="dashboard")
    conn.commit()
    ans = structured_answer.format_answer(
        structured_answer.run(conn, Route("people_in_org", {"org_id": gsa})))
    assert "Sam Staff" in ans and "Office Manager" in ans
    assert "people" in ans.lower()
```

(The existing `conn` fixture in that file already seeds GSA with officers via `project_roster`.)

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest v2/tests/test_structured_answer_officers.py::test_people_in_org_answer_lists_all_roles -q`
Expected: FAIL — `people_in_org` produces `rows=[]` → empty string (skill not wired).

- [ ] **Step 3: Implement** — in `v2/core/retrieval/structured_answer.py`, in `run()` add a branch next to the `officers_in_org` one:

```python
    elif skill == "people_in_org":
        rows = skills.people_in_org(conn, a["org_id"])     # list of (name, title, email)
```

and in `format_answer()` add, right after the `officers_in_org` block:

```python
    if skill == "people_in_org":
        if not rows:
            return f"I don't have people listed for {org}."
        listed = "; ".join(
            f"{title} — {name}" + (f" ({email})" if email else "")
            for name, title, email in rows)
        return f"{org} has {len(rows)} people: {listed}."
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest v2/tests/test_structured_answer_officers.py -q`
Expected: PASS (all, including the existing officer-answer tests).

- [ ] **Step 5: Commit**

```bash
git add v2/core/retrieval/structured_answer.py v2/tests/test_structured_answer_officers.py
git commit -m "feat(retrieval): wire people_in_org into structured_answer"
```

---

## Task 3: Router patterns for "who works at/in <org>"

**Files:** Modify `v2/core/retrieval/router.py`. Test: add to `v2/tests/test_router_officers.py`.

- [ ] **Step 1: Write the failing test** (append to `v2/tests/test_router_officers.py`)

```python
def test_who_works_at_routes_to_people_in_org(conn):
    conn.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
                 "VALUES(3,1,'Graduate Studies','graduate-studies','office')")
    conn.commit()
    r = route(conn, "who works at graduate studies")
    assert r is not None and r.skill == "people_in_org" and r.args["org_id"] == 3


def test_officers_still_route_to_officers(conn):
    r = route(conn, "who are the GSA officers")
    assert r is not None and r.skill == "officers_in_org" and r.args["org_id"] == 2
```

(The existing `conn` fixture seeds NJIT id=1 + GSA id=2.)

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest v2/tests/test_router_officers.py::test_who_works_at_routes_to_people_in_org -q`
Expected: FAIL — returns None (no people route yet).

- [ ] **Step 3: Implement** — in `v2/core/retrieval/router.py`, add this regex after the `_OFFICER` definition:

```python
# "who works at/in <org>", "people in <org>", "<org> staff/team" -> the full roster.
_PEOPLE = re.compile(
    r"\b(who works?\b|works? (?:at|in|for)\b|people (?:in|at|of)\b|"
    r"staff (?:of|at|in)\b|team (?:of|at|in)\b|members? of\b)")
```

and in `route()`, add this branch IMMEDIATELY AFTER the existing `_OFFICER` branch (so an explicit "officers" question still wins, but a general "who works at" routes to the full roster):

```python
    if org_id is not None and _PEOPLE.search(q):
        return Route("people_in_org", {"org_id": org_id})
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest v2/tests/test_router_officers.py v2/tests/test_router.py -q`
Expected: PASS (new + existing router tests; no regression).

- [ ] **Step 5: Commit**

```bash
git add v2/core/retrieval/router.py v2/tests/test_router_officers.py
git commit -m "feat(retrieval): route 'who works at <org>' to people_in_org"
```

---

## Task 4: `people_editor` core write module

**Files:** Create `v2/core/ingestion/people_editor.py`. Test: create `v2/tests/test_people_editor.py`.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_people_editor.py
from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from v2.core.ingestion.people_editor import add_or_edit_person, remove_person_role
from v2.core.retrieval.skills import people_in_org


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(2,1,'Graduate Student Association','gsa','custom')")
    c.commit()
    yield c
    c.close()


def test_add_person_creates_graph_and_bio(conn):
    res = add_or_edit_person(conn, org_id=2, name="Pat Sport", title="Sport Officer",
                             category="officer", email="pat@njit.edu",
                             about="Pat runs intramural sports nights for grad students.")
    conn.commit()
    assert res["person_key"] == "dashboard/gsa/pat-sport"
    people = people_in_org(conn, 2)
    assert ("Pat Sport", "Sport Officer", "pat@njit.edu") in people
    # bio knowledge_item exists, dashboard-sourced, linked by entity_id
    n = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND created_by='dashboard' "
                     "AND json_extract(metadata,'$.entity_id')=?", (res["person_key"],)).fetchone()[0]
    assert n == 1


def test_edit_person_is_idempotent_and_updates(conn):
    add_or_edit_person(conn, org_id=2, name="Pat Sport", title="Sport Officer",
                       category="officer", email="pat@njit.edu", about="v1")
    add_or_edit_person(conn, org_id=2, name="Pat Sport", title="Sports & Wellness Officer",
                       category="officer", email="pat2@njit.edu", about="v2")
    conn.commit()
    people = people_in_org(conn, 2)
    # exactly one Pat, updated title + email; old bio retired, one active bio
    assert [p for p in people if p[0] == "Pat Sport"] == [("Pat Sport", "Sports & Wellness Officer", "pat2@njit.edu")]
    n = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 "
                     "AND json_extract(metadata,'$.entity_id')='dashboard/gsa/pat-sport'").fetchone()[0]
    assert n == 1


def test_remove_person_role_soft_deletes(conn):
    add_or_edit_person(conn, org_id=2, name="Pat Sport", title="Sport Officer",
                       category="officer", email=None, about="bio")
    conn.commit()
    res = remove_person_role(conn, person_key="dashboard/gsa/pat-sport", org_id=2)
    conn.commit()
    assert res["removed"] is True and res["person_deactivated"] is True
    assert people_in_org(conn, 2) == []
    # bio retired (inactive), not deleted
    active = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 "
                          "AND json_extract(metadata,'$.entity_id')='dashboard/gsa/pat-sport'").fetchone()[0]
    kept = conn.execute("SELECT COUNT(*) FROM knowledge_items "
                        "WHERE json_extract(metadata,'$.entity_id')='dashboard/gsa/pat-sport'").fetchone()[0]
    assert active == 0 and kept == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest v2/tests/test_people_editor.py -q`
Expected: FAIL — `ModuleNotFoundError: v2.core.ingestion.people_editor`.

- [ ] **Step 3: Implement `v2/core/ingestion/people_editor.py`**

```python
"""Single-person manual authoring for the dashboard People & Roles editor: create/edit and
soft-remove a person + role (+ optional embedded bio). Pure graph/KB writes via the shared
helpers; the caller owns the transaction (commit) and any embed trigger. source='dashboard',
so the crawler never touches these."""
from __future__ import annotations

import json
import re
import sqlite3

from v2.core.graph.orgs import ensure_org, org_node_id, sync_org_nodes
from v2.core.graph.project import project_appointment


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _org_slug(conn: sqlite3.Connection, org_id: int) -> str:
    row = conn.execute("SELECT slug FROM organizations WHERE id=?", (org_id,)).fetchone()
    if not row:
        raise ValueError(f"no organization id={org_id}")
    return row[0]


def add_or_edit_person(conn: sqlite3.Connection, *, org_id: int, name: str, title: str,
                       category: str, email: str | None = None,
                       about: str | None = None, source: str = "dashboard") -> dict:
    """Upsert a Person + one has_role edge (free-text title, category) under org_id, merge
    email into the node attrs, and (re)write an optional bio knowledge_item. Idempotent on
    the person key. Returns {person_key, bio_item_id|None}. Does NOT commit."""
    org_slug = _org_slug(conn, org_id)
    key = f"{source}/{org_slug}/{_slug(name)}"
    sync_org_nodes(conn)
    pid = project_appointment(conn, person_key=key, name=name, org_id=org_id,
                              category=category, titles=[title],
                              source_section="manual", source=source)
    if email:
        row = conn.execute("SELECT attrs FROM nodes WHERE id=?", (pid,)).fetchone()
        attrs = json.loads(row[0]) if row and row[0] else {}
        attrs["email"] = email
        conn.execute("UPDATE nodes SET attrs=?, updated_at=datetime('now') WHERE id=?",
                     (json.dumps(attrs), pid))
    bio_id = None
    # retire any prior bio for this person, then (re)insert if 'about' is provided
    conn.execute("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
                 "WHERE is_active=1 AND json_extract(metadata,'$.entity_id')=? "
                 "AND created_by=?", (key, source))
    if about and about.strip():
        meta = json.dumps({"entity_id": key, "verified": True,
                           "natural_key": f"{key}:profile:main"})
        content = f"{name} — {title}. {about.strip()}"
        cur = conn.execute(
            "INSERT INTO knowledge_items(org_id,type,title,content,metadata,version,"
            "is_active,created_by) VALUES(?,?,?,?,?,1,1,?)",
            (org_id, "profile", name, content, meta, source))
        conn.execute("UPDATE knowledge_items SET root_id=? WHERE id=?",
                     (cur.lastrowid, cur.lastrowid))
        bio_id = cur.lastrowid
    return {"person_key": key, "bio_item_id": bio_id}


def remove_person_role(conn: sqlite3.Connection, *, person_key: str, org_id: int,
                       source: str = "dashboard") -> dict:
    """Soft-remove: deactivate this person's has_role edge to org_id; if they then have no
    other active role, deactivate the Person node; retire their bio. Returns
    {removed, person_deactivated}. Does NOT commit."""
    prow = conn.execute("SELECT id FROM nodes WHERE type='Person' AND key=?",
                        (person_key,)).fetchone()
    if not prow:
        return {"removed": False, "person_deactivated": False}
    pid = prow[0]
    onode = org_node_id(conn, org_id)
    edges = conn.execute(
        "SELECT id FROM edges WHERE src_id=? AND dst_id=? AND type='has_role' AND is_active=1",
        (pid, onode)).fetchall()
    for (eid,) in edges:
        conn.execute("UPDATE edges SET is_active=0, updated_at=datetime('now') WHERE id=?", (eid,))
    removed = bool(edges)
    remaining = conn.execute(
        "SELECT COUNT(*) FROM edges WHERE src_id=? AND type='has_role' AND is_active=1",
        (pid,)).fetchone()[0]
    person_deactivated = False
    if remaining == 0:
        conn.execute("UPDATE nodes SET is_active=0, updated_at=datetime('now') WHERE id=?", (pid,))
        person_deactivated = True
    conn.execute("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
                 "WHERE is_active=1 AND json_extract(metadata,'$.entity_id')=? AND created_by=?",
                 (person_key, source))
    return {"removed": removed, "person_deactivated": person_deactivated}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest v2/tests/test_people_editor.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/people_editor.py v2/tests/test_people_editor.py
git commit -m "feat(ingestion): people_editor — add/edit/soft-remove a person+role+bio"
```

---

## Task 5: `local_server` endpoints `POST /people` and `/people/remove`

**Files:** Modify `v2/local_server.py`. Test: create `v2/tests/test_local_server_people.py`.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_local_server_people.py
from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import sqlite3
import pytest

# the handler methods are pure functions of (conn, body); test them directly without HTTP
from v2.local_server import GatewayHandler  # noqa: E402  (class name per local_server.py)


@pytest.fixture()
def conn(tmp_path):
    from v2.core.database.schema import create_all
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(2,1,'GSA','gsa','custom')")
    c.commit()
    yield c
    c.close()


def test_post_person_and_remove(conn):
    # call the handler methods unbound (they only use conn + body)
    res = GatewayHandler._post_person(None, conn, {
        "org_id": 2, "name": "Pat Sport", "title": "Sport Officer",
        "role_type": "Officer", "email": "pat@njit.edu", "about": "runs sports"})
    assert res["success"] and res["person_key"] == "dashboard/gsa/pat-sport"
    assert res["needs_reindex"] is True
    from v2.core.retrieval.skills import people_in_org
    assert ("Pat Sport", "Sport Officer", "pat@njit.edu") in people_in_org(conn, 2)

    rem = GatewayHandler._post_person_remove(None, conn, {
        "person_key": "dashboard/gsa/pat-sport", "org_id": 2})
    assert rem["success"] and rem["removed"] is True
    assert people_in_org(conn, 2) == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest v2/tests/test_local_server_people.py -q`
Expected: FAIL — `AttributeError: ... has no attribute '_post_person'`.

- [ ] **Step 3: Implement** — in `v2/local_server.py`:

(a) add the two POST dispatch lines next to the existing ones (inside `do_POST`, in the `if path == ...` block with `/posts`, `/knowledge`, `/orgs`, `/settings`):

```python
                if path == "/people":
                    return self._json(self._post_person(conn, body))
                if path == "/people/remove":
                    return self._json(self._post_person_remove(conn, body))
```

(b) add the two handler methods next to `_post_org` (the role-type → category map keeps the controlled vocabulary out of the client):

```python
    _ROLE_TYPE_TO_CATEGORY = {
        "officer": "officer", "dept rep": "deprep", "deprep": "deprep",
        "staff": "staff", "advisor": "advisor", "admin": "admin",
    }

    def _post_person(self, conn, b):
        if not b.get("org_id") or not b.get("name") or not b.get("title"):
            raise ValueError("org_id, name and title are required")
        from v2.core.ingestion.people_editor import add_or_edit_person
        category = self._ROLE_TYPE_TO_CATEGORY.get(str(b.get("role_type", "officer")).lower(), "officer")
        res = add_or_edit_person(conn, org_id=b["org_id"], name=b["name"], title=b["title"],
                                 category=category, email=b.get("email"), about=b.get("about"))
        conn.commit()
        # a bio change needs the embed pass (same convention as _post_knowledge)
        return {"success": True, "needs_reindex": bool(b.get("about")), **res}

    def _post_person_remove(self, conn, b):
        if not b.get("person_key") or not b.get("org_id"):
            raise ValueError("person_key and org_id are required")
        from v2.core.ingestion.people_editor import remove_person_role
        res = remove_person_role(conn, person_key=b["person_key"], org_id=b["org_id"])
        conn.commit()
        return {"success": True, **res}
```

> Note: the request-handler class is `GatewayHandler` (in `v2/local_server.py`). The methods take `(self, conn, b)` like `_post_org`; the test calls them unbound with `self=None` since they don't use `self`.

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest v2/tests/test_local_server_people.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add v2/local_server.py v2/tests/test_local_server_people.py
git commit -m "feat(dashboard-api): POST /people and /people/remove endpoints"
```

---

## Task 6: Dashboard People tab editor UI

**Files:** Modify `dashboard/app.js` (the `renderPeople` area + a new editor render). No JS test harness exists in the repo, so this task is implement + **manual verification** against a running dashboard.

- [ ] **Step 1: Add org picker + per-row Edit/Remove + Add/Edit form to `renderPeople`**

In `dashboard/app.js`, extend `renderPeople()` so that, above the existing people table, it renders an **org selector** (from `SELECT id,name,slug FROM organizations WHERE is_active=1 ORDER BY name`, plus a "+ New club/org" button that calls the existing `/orgs` create), and adds an **Edit**/**Remove** button to each row of the people table (only for rows whose person node `source='dashboard'` — crawler rows render read-only). Below the table, render the **Add/Edit form**: `Name` (text), `Title` (text), `Role type` (`<select>` with Officer/Dept Rep/Staff/Advisor/Admin), `Email` (text), `About` (textarea), and a **Save** button.

Wire the actions through the dashboard's existing server/offline helpers:

```javascript
// Save (create/edit): POST /people in server mode; offline -> applyAndExport SQL note
function savePerson(orgId, form) {
  const body = { org_id: orgId, name: form.name, title: form.title,
                 role_type: form.role_type, email: form.email, about: form.about };
  if (isServerMode()) {
    serverFetch("/people", { method: "POST", body }).then(() => renderPeople());
  } else {
    // offline fallback: tell the admin to use server mode for graph edits
    alert("People editing requires the dashboard server (run local_server.py).");
  }
}

// Remove (soft delete): POST /people/remove
function removePerson(personKey, orgId) {
  if (!confirm("Remove this person/role? (kept in history, can be re-added)")) return;
  serverFetch("/people/remove", { method: "POST", body: { person_key: personKey, org_id: orgId } })
    .then(() => renderPeople());
}
```

(Match the existing `serverFetch` / `isServerMode` / `applyAndExport` helpers already used by the Posts and Settings tabs. The People-table query in `renderPeople` must also select `n.key` and `n.source` so Edit/Remove can pass the person key and hide controls on crawler rows.)

- [ ] **Step 2: Manual verification (run the dashboard server)**

```bash
source venv/bin/activate 2>/dev/null || source .venv/bin/activate
# point at a COPY so we don't touch the live DB during manual testing
cp gsa_gateway.db /tmp/dash_test.db
GSA_DB=/tmp/dash_test.db python v2/local_server.py   # or however local_server picks its DB
```
Open the dashboard, go to the People tab, select **GSA**, and:
- Add a person "Pat Sport / Sport Officer / Officer / pat@njit.edu / about…" → Save.
- Confirm it appears in the table and in the DB:
  `python -c "import sqlite3;print(sqlite3.connect('/tmp/dash_test.db').execute(\"SELECT name FROM nodes WHERE key='dashboard/gsa/pat-sport'\").fetchone())"`
- Click **Remove** on it → confirm it disappears and the node is `is_active=0`.
- Create a new club via "+ New club/org", add a person to it.

Expected: all actions reflected in the DB; crawler-sourced rows (YWCC faculty) show no Edit/Remove.

- [ ] **Step 3: Commit**

```bash
git add dashboard/app.js
git commit -m "feat(dashboard): People tab editor — add/edit/remove people + roles per org"
```

---

## Task 7: End-to-end verification (live, gated)

**Files:** none (operational). Mirrors how the GSA seed was verified.

- [ ] **Step 1: Run the full retrieval + people test suite**

Run: `python -m pytest v2/tests/test_skills_people_in_org.py v2/tests/test_people_editor.py v2/tests/test_local_server_people.py v2/tests/test_router_officers.py v2/tests/test_structured_answer_officers.py v2/tests/test_retrieval.py -q`
Expected: all pass.

- [ ] **Step 2: Smoke-test the structured answers on the live DB (read-only)**

```bash
python - <<'PY'
import sqlite3
from v2.core.retrieval import router as srouter, structured_answer
conn = sqlite3.connect("gsa_gateway.db")
for q in ["who works at graduate studies", "who are the GSA officers"]:
    rt = srouter.route(conn, q)
    print(q, "->", rt and rt.skill)
PY
```
Expected: "who works at graduate studies" → `people_in_org`; "who are the GSA officers" → `officers_in_org`.

- [ ] **Step 3: Commit any docs/notes** (e.g. update `bot_features.md` if you mention the editor). Optional.

---

## Notes for the implementer

- **venv:** `source venv/bin/activate 2>/dev/null || source .venv/bin/activate` before pytest.
- **Caller owns the transaction:** the graph helpers and `people_editor` functions do NOT commit; the `local_server` handlers commit (like `_post_org`). Keep that split.
- **Don't insert `search_text`** — it's a generated column on `knowledge_items`.
- **source tagging:** everything here is `source='dashboard'` / `created_by='dashboard'`. Never write `source='crawler'`.
- **Embedding:** `_post_person` returns `needs_reindex` (same as `_post_knowledge`); the bio is embedded by the existing `v2/scripts/embed_all.py` pass (resumable) — no new job is invented in this plan.
- **local_server class name:** the request-handler class is `GatewayHandler` (confirmed).
- After all tasks: final review, then merge per the team's branch workflow. This is **Spec A**; the crawler control page is **Spec B** (separate plan).
```
