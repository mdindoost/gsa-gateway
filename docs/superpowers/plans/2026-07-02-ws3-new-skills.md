# WS3 — Three evidence-backed KG skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `contact_of_person` (B1), `orgs_by_type` (B3), and `title_of_person` (B4) — three structured KG skills the data already supports — wired into the router + slot extractor, inheriting WS2 resolution, with no retrieval/generation/family-classifier-core changes.

**Architecture:** B1/B4 are crisp person-attribute projections (delegating to the existing `person_attrs` reader and the `entity_card` titles-iteration); they intercept at the router's person-branch return sites via a `_person_skill(q)` cue-dispatch, leaving person resolution untouched. B3 mirrors `org_departments`' SQL generalized on `type`, and `org_departments` is refactored to delegate to it (coexist + DRY, per unanimous review). All three are deterministic factual reads (never LLM-reworded).

**Tech Stack:** Python 3.11, SQLite, pytest. Deterministic rule-router + constrained-JSON (Granite) slot-extractor fallback.

## Global Constraints

- **Do NOT touch** retrieval (`retriever.py`), generation (`ollama_client.py`), or the family classifier's coarse KG/RAG decision beyond adding these three as route targets.
- **Anti-fabrication (honest-partial):** a missing field NEVER yields a blank-success; state what's on file and what's missing. Never imply a contact/title exists when it doesn't.
- **Inherit WS2 resolution:** person slots resolve via the shared `_resolve_person`/`_resolve_person_slot` (fuzzy → corroborate-or-clarify → `person_disambig`); never a wrong person. B3 parent/type unresolved ⇒ abstain (⇒RAG).
- **Coexist:** keep `org_departments` as a routed skill (its gold rows, route site, and `_has_child_departments` guard stay); it delegates its SQL to `orgs_by_type`.
- **Compose with greeting (owner):** contact/title/orgs are **NOT** deterministic — they go through `compose_from_rows` (keep the "Hi there!" opener), anti-fabrication via the existing compose clauses. Do NOT add them to `_DETERMINISTIC_SKILLS`.
- **B3 enum = shared `ORG_TYPE_ENUM = ("club","department","college")`** (in skills.py; imported by router + slot_extractor). Router fast-path enumerates only **club/college** (plural noun) — department stays on the existing `org_departments` branch. "student organization(s)/org(s)/group(s)" → club. "school(s)" → **abstain** (not mapped).
- **Pronoun guard:** a bare personal pronoun subject ("his position", "contact about this") never resolves a person → RAG. Explicit guard in `_resolve_surname`, not data-dependent.
- **Test fixture pattern:** in-memory `create_all(":memory:")` + `ensure_org` + `project_appointment`; set Person contact attrs via direct SQL `UPDATE nodes SET attrs=?`.
- **Commits:** no Claude attribution / co-author trailer (owner standing rule).
- **Gate:** senior-eng + RAG review + Codex second opinion + owner sign-off before merge; TDD; show diff.

---

### Task 1: B1 `contact_of_person` skill (entity.py)

**Files:**
- Modify: `v2/core/retrieval/entity.py` (add after `person_attrs`, ~line 385)
- Test: `v2/tests/test_ws3_skills.py` (create)

**Interfaces:**
- Consumes: `person_attrs(conn, entity_id) -> dict`; `normalize_person_name(name)`.
- Produces: `contact_of_person(conn, entity_id: str) -> dict` = `{"name": str, "email": str|None, "phone": str|None, "office": str|None, "present": list[str]}` where `present` lists the subset of `["email","phone","office"]` that are non-empty.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_ws3_skills.py
from __future__ import annotations
import json, sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
import pytest
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment
from v2.core.retrieval import entity, skills


def _set_attrs(conn, key, **fields):
    row = conn.execute("SELECT attrs FROM nodes WHERE type='Person' AND key=?", (key,)).fetchone()
    attrs = json.loads(row[0]) if row and row[0] else {}
    attrs.update(fields)
    conn.execute("UPDATE nodes SET attrs=? WHERE type='Person' AND key=?", (json.dumps(attrs), key))
    conn.commit()


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    njit = ensure_org(c, "njit", "NJIT", None, type="university")
    ywcc = ensure_org(c, "ywcc", "Ying Wu College of Computing", "njit", type="college")
    cs = ensure_org(c, "cs", "Computer Science", "ywcc", type="department")
    ensure_org(c, "acm", "ACM Student Chapter", "njit", type="club")
    ensure_org(c, "wics", "Women in Computing Society", "njit", type="club")
    ensure_org(c, "mtsm", "Martin Tuchman School of Management", "njit", type="college")
    sync_org_nodes(c)
    project_appointment(c, person_key="d/koutis", name="Ioannis Koutis", org_id=cs,
                        category="faculty", titles=["Professor", "Department Chair"],
                        source_section="manual", source="dashboard")
    project_appointment(c, person_key="d/noattr", name="Nadia Noattr", org_id=cs,
                        category="faculty", titles=["Lecturer"], source_section="manual",
                        source="dashboard")
    _set_attrs(c, "d/koutis", email="ik@njit.edu", phone="973-555-0101", office="GITC 4400")
    project_appointment(c, person_key="d/onlyoffice", name="Ola Office", org_id=cs,
                        category="faculty", titles=["Professor"], source_section="manual",
                        source="dashboard")
    _set_attrs(c, "d/onlyoffice", office="GITC 1000")
    c.commit()
    yield c
    c.close()


def test_contact_full(conn):
    r = entity.contact_of_person(conn, "d/koutis")
    assert r["name"] == "Ioannis Koutis"
    assert r["email"] == "ik@njit.edu"
    assert r["phone"] == "973-555-0101"
    assert r["office"] == "GITC 4400"
    assert r["present"] == ["email", "phone", "office"]


def test_contact_partial_office_only(conn):
    r = entity.contact_of_person(conn, "d/onlyoffice")
    assert r["office"] == "GITC 1000"
    assert r["email"] is None and r["phone"] is None
    assert r["present"] == ["office"]


def test_contact_none_on_file(conn):
    r = entity.contact_of_person(conn, "d/noattr")
    assert r["present"] == []
    assert r["email"] is None and r["phone"] is None and r["office"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest v2/tests/test_ws3_skills.py -k contact -q`
Expected: FAIL — `AttributeError: module 'v2.core.retrieval.entity' has no attribute 'contact_of_person'`

- [ ] **Step 3: Write minimal implementation**

```python
# v2/core/retrieval/entity.py — insert after person_attrs()
_CONTACT_FIELDS = ("email", "phone", "office")


def contact_of_person(conn: sqlite3.Connection, entity_id: str) -> dict:
    """One person's contact channels (email/phone/office) from the Person node attrs. Honest-partial:
    each field is the value or None; ``present`` lists only the fields actually on file, so a caller
    never implies a channel that isn't there. Never fabricated."""
    attrs = person_attrs(conn, entity_id)
    row = conn.execute(
        "SELECT name FROM nodes WHERE type='Person' AND key=? AND is_active=1",
        (entity_id,)).fetchone()
    name = normalize_person_name(row[0]) if row else entity_id
    vals = {f: (attrs.get(f) or None) for f in _CONTACT_FIELDS}
    present = [f for f in _CONTACT_FIELDS if vals[f]]
    return {"name": name, **vals, "present": present}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest v2/tests/test_ws3_skills.py -k contact -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add v2/core/retrieval/entity.py v2/tests/test_ws3_skills.py
git commit -m "feat(ws3): B1 contact_of_person skill — honest-partial email/phone/office"
```

---

### Task 2: B4 `title_of_person` skill (entity.py)

**Files:**
- Modify: `v2/core/retrieval/entity.py` (add after `contact_of_person`)
- Test: `v2/tests/test_ws3_skills.py` (append)

**Interfaces:**
- Consumes: `has_role` edges (`attrs.titles` or `[category]`), `normalize_person_name`.
- Produces: `title_of_person(conn, entity_id: str) -> dict` = `{"name": str, "titles": list[tuple[str, str]]}` — a de-duplicated, org-ordered list of `(title, org_name)`; empty list if no active roles.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_ws3_skills.py — append
def test_title_multi(conn):
    r = entity.title_of_person(conn, "d/koutis")
    assert r["name"] == "Ioannis Koutis"
    assert ("Professor", "Computer Science") in r["titles"]
    assert ("Department Chair", "Computer Science") in r["titles"]


def test_title_dedup_and_order(conn):
    r = entity.title_of_person(conn, "d/koutis")
    # no duplicate (title, org) pairs
    assert len(r["titles"]) == len(set(r["titles"]))


def test_title_none(conn):
    # a person with a role but empty titles falls back to the category label, never empty-blank
    r = entity.title_of_person(conn, "d/noattr")
    assert r["titles"]  # ["Lecturer", "Computer Science"] present
    assert r["titles"][0][1] == "Computer Science"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest v2/tests/test_ws3_skills.py -k title -q`
Expected: FAIL — no attribute `title_of_person`

- [ ] **Step 3: Write minimal implementation**

```python
# v2/core/retrieval/entity.py — insert after contact_of_person()
def title_of_person(conn: sqlite3.Connection, entity_id: str) -> dict:
    """One person's title(s)/position(s): a de-duplicated [(title, org_name)] list read from each active
    has_role edge (attrs.titles, or the category label as fallback), org-ordered. Empty list if the
    person holds no active role — the caller renders an honest 'no listed position', never fabricated.
    Mirrors the entity_card titles-iteration so the two never drift."""
    row = conn.execute(
        "SELECT id, name FROM nodes WHERE type='Person' AND key=? AND is_active=1",
        (entity_id,)).fetchone()
    if not row:
        return {"name": entity_id, "titles": []}
    nid, raw = row
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for eattrs, cat, oname in conn.execute(
            "SELECT e.attrs, e.category, o.name FROM edges e JOIN nodes o ON o.id=e.dst_id "
            "WHERE e.src_id=? AND e.type='has_role' AND e.is_active=1 ORDER BY o.name", (nid,)):
        titles = (json.loads(eattrs) if eattrs else {}).get("titles") or [cat]
        for t in titles:
            pair = (t, oname)
            if t and pair not in seen:
                seen.add(pair)
                out.append(pair)
    return {"name": normalize_person_name(raw), "titles": out}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest v2/tests/test_ws3_skills.py -k title -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add v2/core/retrieval/entity.py v2/tests/test_ws3_skills.py
git commit -m "feat(ws3): B4 title_of_person skill — dedup (title, org) list, honest-empty"
```

---

### Task 3: B3 `orgs_by_type` skill + `org_departments` delegation (skills.py)

**Files:**
- Modify: `v2/core/retrieval/skills.py` (`org_departments` ~line 115; add `orgs_by_type` before it)
- Test: `v2/tests/test_ws3_skills.py` (append)

**Interfaces:**
- Produces: `orgs_by_type(conn, org_type: str, parent_org_id: int|None = None) -> list[str]` — active org names of `type=org_type`, optionally filtered to `parent_id=parent_org_id`, `ORDER BY name`.
- Refactor: `org_departments(conn, org_id) -> list[str]` now `return orgs_by_type(conn, "department", org_id)`.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_ws3_skills.py — append
def test_orgs_by_type_clubs(conn):
    assert skills.orgs_by_type(conn, "club") == ["ACM Student Chapter", "Women in Computing Society"]


def test_orgs_by_type_colleges(conn):
    got = skills.orgs_by_type(conn, "college")
    assert "Ying Wu College of Computing" in got and "Martin Tuchman School of Management" in got


def test_orgs_by_type_parent_scoped(conn):
    ywcc_id = conn.execute("SELECT id FROM organizations WHERE slug='ywcc'").fetchone()[0]
    assert skills.orgs_by_type(conn, "department", ywcc_id) == ["Computer Science"]


def test_org_departments_delegates(conn):
    ywcc_id = conn.execute("SELECT id FROM organizations WHERE slug='ywcc'").fetchone()[0]
    assert skills.org_departments(conn, ywcc_id) == skills.orgs_by_type(conn, "department", ywcc_id)


def test_orgs_by_type_empty(conn):
    assert skills.orgs_by_type(conn, "club", 999999) == []  # bogus parent → empty, not error


def test_orgs_by_type_unknown_type(conn):
    assert skills.orgs_by_type(conn, "office") == []  # off-enum type → [], never raises
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest v2/tests/test_ws3_skills.py -k orgs_by_type -q`
Expected: FAIL — no attribute `orgs_by_type`

- [ ] **Step 3: Write minimal implementation**

```python
# v2/core/retrieval/skills.py — insert near the top (module constant) + before def org_departments(...)
# Shared enum: the ONLY org types WS3 enumerates. Imported by router.py + slot_extractor.py so the
# guard lives in one place (review MAJOR: validate org_type in the skill, not only at call sites).
ORG_TYPE_ENUM: tuple[str, ...] = ("club", "department", "college")


def orgs_by_type(conn: sqlite3.Connection, org_type: str,
                 parent_org_id: int | None = None) -> list[str]:
    """Active org names of a given ``type`` (e.g. 'club', 'college', 'department'), optionally scoped to
    a parent (``parent_id``). The single type-filtered enumeration; org_departments delegates here so the
    child-enumeration SQL lives in ONE place (WS3 coexist + DRY). Unknown type ⇒ [] (never raises)."""
    if org_type not in ORG_TYPE_ENUM:
        return []
    if parent_org_id is None:
        rows = conn.execute(
            "SELECT name FROM organizations WHERE type=? AND is_active=1 ORDER BY name",
            (org_type,))
    else:
        rows = conn.execute(
            "SELECT name FROM organizations WHERE type=? AND is_active=1 AND parent_id=? "
            "ORDER BY name", (org_type, parent_org_id))
    return [r[0] for r in rows]
```

Then replace the body of `org_departments`:

```python
def org_departments(conn: sqlite3.Connection, org_id: int) -> list[str]:
    """Immediate child org names that are actual departments (e.g. YWCC → Computer Science,
    Data Science, …). Delegates to orgs_by_type(type='department', parent=org_id) — one SQL path."""
    return orgs_by_type(conn, "department", org_id)
```

- [ ] **Step 4: Run tests to verify they pass (incl. the existing org_departments test)**

Run: `python -m pytest v2/tests/test_ws3_skills.py -k orgs_by_type -q && python -m pytest v2/tests/ -k org_department -q`
Expected: PASS (both)

- [ ] **Step 5: Commit**

```bash
git add v2/core/retrieval/skills.py v2/tests/test_ws3_skills.py
git commit -m "feat(ws3): B3 orgs_by_type + org_departments delegation (coexist + DRY)"
```

---

### Task 4: Render arms + deterministic set (structured_answer.py)

**Files:**
- Modify: `v2/core/retrieval/structured_answer.py` — `run()` (~line 47, add arms), `format_answer()` (~line 335, add arms). **Do NOT modify `_DETERMINISTIC_SKILLS`** (owner: compose with greeting).
- Test: `v2/tests/test_ws3_render.py` (create)

**Interfaces:**
- Consumes: `entity.contact_of_person`, `entity.title_of_person`, `skills.orgs_by_type` (Tasks 1-3); `Route`.
- Produces (result dicts from `run`): contact → `{skill, name, email, phone, office, present}`; title → `{skill, name, titles}`; orgs_by_type → `{skill, org_type, parent_name, rows}`. `format_answer` renders each (the canonical Facts string handed to `compose_from_rows` + the offline fallback). **The three are NOT deterministic** — they compose (keep the "Hi there!" greeting); anti-fabrication rides on the existing compose clauses (same as `entity_card`).

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_ws3_render.py
from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.retrieval import structured_answer as sa


def test_contact_full_render():
    out = sa.format_answer({"skill": "contact_of_person", "name": "Ioannis Koutis",
                            "email": "ik@njit.edu", "phone": "973-555-0101", "office": "GITC 4400",
                            "present": ["email", "phone", "office"]})
    assert "ik@njit.edu" in out and "973-555-0101" in out and "GITC 4400" in out


def test_contact_partial_states_missing():
    out = sa.format_answer({"skill": "contact_of_person", "name": "Ola Office",
                            "email": None, "phone": None, "office": "GITC 1000", "present": ["office"]})
    assert "GITC 1000" in out
    assert "email" in out.lower() and "phone" in out.lower()  # explicitly names what's missing


def test_contact_none_on_file():
    out = sa.format_answer({"skill": "contact_of_person", "name": "Nadia Noattr",
                            "email": None, "phone": None, "office": None, "present": []})
    assert "don't have" in out.lower() or "not on file" in out.lower()


def test_title_render():
    out = sa.format_answer({"skill": "title_of_person", "name": "Ioannis Koutis",
                            "titles": [("Professor", "Computer Science"),
                                       ("Department Chair", "Computer Science")]})
    assert "Professor" in out and "Department Chair" in out and "Computer Science" in out


def test_title_category_fallback_reads_ok():
    # a category-only title ("faculty") renders as a title-listing, not "is faculty at" (review MINOR)
    out = sa.format_answer({"skill": "title_of_person", "name": "Nadia Noattr",
                            "titles": [("faculty", "Computer Science")]})
    assert "faculty" in out and "Computer Science" in out


def test_title_empty_render():
    out = sa.format_answer({"skill": "title_of_person", "name": "No Role", "titles": []})
    assert "don't have" in out.lower()


def test_orgs_by_type_count_and_list():
    out = sa.format_answer({"skill": "orgs_by_type", "org_type": "club", "parent_name": None,
                            "rows": ["ACM Student Chapter", "Women in Computing Society"]})
    assert "2" in out and "ACM Student Chapter" in out and "Women in Computing Society" in out


def test_orgs_by_type_singular_grammar():
    out = sa.format_answer({"skill": "orgs_by_type", "org_type": "college", "parent_name": None,
                            "rows": ["Ying Wu College of Computing"]})
    assert "1 college" in out and "colleges" not in out  # explicit singular, not a plural hack


def test_orgs_by_type_empty():
    out = sa.format_answer({"skill": "orgs_by_type", "org_type": "club", "parent_name": None,
                            "rows": []})
    assert "don't have" in out.lower()


def test_all_three_compose_not_deterministic():
    # owner decision: contact/title/orgs COMPOSE (keep the greeting) — must NOT be verbatim-only
    for skill in ("contact_of_person", "title_of_person", "orgs_by_type"):
        assert not sa.is_deterministic({"skill": skill})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest v2/tests/test_ws3_render.py -q`
Expected: FAIL (format_answer returns None / KeyError; is_deterministic False)

- [ ] **Step 3: Write minimal implementation**

In `run()`, add after the `entity_card` arm (~line 51):

```python
    if skill == "contact_of_person":
        return {"skill": skill, **entity.contact_of_person(conn, a["entity_id"])}
    if skill == "title_of_person":
        return {"skill": skill, **entity.title_of_person(conn, a["entity_id"])}
    if skill == "orgs_by_type":
        parent_name = None
        if a.get("parent_org_id") is not None:
            pr = conn.execute("SELECT name FROM organizations WHERE id=?",
                              (a["parent_org_id"],)).fetchone()
            parent_name = pr[0] if pr else None
        return {"skill": skill, "org_type": a["org_type"], "parent_name": parent_name,
                "rows": skills.orgs_by_type(conn, a["org_type"], a.get("parent_org_id"))}
```

**Do NOT touch `_DETERMINISTIC_SKILLS`** — the three compose (owner: keep the greeting). Anti-fabrication
rides on the existing `compose_from_rows` clauses `entity_card` already uses.

In `format_answer()`, add arms (after the `entity_card` arm ~line 335). Add these helpers near `_join` and the arms themselves:

```python
# explicit (singular, plural) labels — no plural[:-1] hack (review MINOR)
_ORG_TYPE_LABEL = {"club": ("club", "clubs"), "department": ("department", "departments"),
                   "college": ("college", "colleges")}


def _fmt_missing(labels: list[str]) -> str:
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} or {labels[1]}"
    return ", ".join(labels[:-1]) + f", or {labels[-1]}"


# --- arms inside format_answer(result): ---
    if skill == "contact_of_person":
        name = result["name"]
        order = [("email", "Email"), ("phone", "Phone"), ("office", "Office")]
        have = [(lbl, result[k]) for k, lbl in order if result.get(k)]
        if not have:
            return f"I don't have contact information on file for {name}."
        body = "; ".join(f"{lbl}: {val}" for lbl, val in have)
        missing = [lbl.lower() for k, lbl in order if not result.get(k)]
        note = f" (No {_fmt_missing(missing)} on file.)" if missing else ""
        return f"{name} — {body}.{note}"

    if skill == "title_of_person":
        name = result["name"]
        titles = result["titles"]
        if not titles:
            return f"I don't have a listed position for {name}."
        # title-listing style ("Professor, Computer Science; …") so a category fallback ("faculty")
        # reads naturally instead of an awkward "is faculty at" (review MINOR).
        parts = [f"{t}, {o}" for t, o in titles]
        return f"{name} — {'; '.join(parts)}."

    if skill == "orgs_by_type":
        rows = result["rows"]
        sing, plur = _ORG_TYPE_LABEL.get(result["org_type"],
                                         (result["org_type"], result["org_type"] + "s"))
        scope = f" under {result['parent_name']}" if result.get("parent_name") else " at NJIT"
        if not rows:
            return f"I don't have any {plur} on file{scope}."
        if len(rows) == 1:
            return f"There is 1 {sing}{scope}: {_join(rows)}."
        return f"There are {len(rows)} {plur}{scope}: {_join(rows)}."
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest v2/tests/test_ws3_render.py -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add v2/core/retrieval/structured_answer.py v2/tests/test_ws3_render.py
git commit -m "feat(ws3): render arms for contact/title/orgs_by_type (compose, keep greeting)"
```

---

### Task 5: Router fast-path (router.py)

**Files:**
- Modify: `v2/core/retrieval/router.py` — add cue regexes (~after line 174), `_person_skill` helper (~after line 196), B3 branch (~line 546 region), person-branch return sites (~lines 581-600)
- Test: `v2/tests/test_ws3_router.py` (create)

**Interfaces:**
- Consumes: `_resolve_person`, `_resolve_surname`, `_find_org`, `_has_child_departments`, `_is_university_root`, `_FACULTY_CUE`, `_DEPT_ENUM`, `Route`.
- Produces: `route()` returns `Route("contact_of_person"|"title_of_person", {entity_id, name})` and `Route("orgs_by_type", {org_type, parent_org_id})`; helper `_person_skill(q) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_ws3_router.py
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
from v2.core.retrieval.router import route


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    njit = ensure_org(c, "njit", "NJIT", None, type="university")
    ywcc = ensure_org(c, "ywcc", "Ying Wu College of Computing", "njit", type="college")
    cs = ensure_org(c, "cs", "Computer Science", "ywcc", type="department")
    ensure_org(c, "gsa", "Graduate Student Association", "njit", type="gsa")
    ensure_org(c, "acm", "ACM Student Chapter", "gsa", type="club")
    ensure_org(c, "wics", "Women in Computing Society", "gsa", type="club")
    ensure_org(c, "mtsm", "Martin Tuchman School of Management", "njit", type="college")
    sync_org_nodes(c)
    project_appointment(c, person_key="d/koutis", name="Ioannis Koutis", org_id=cs,
                        category="faculty", titles=["Professor"], source_section="manual",
                        source="dashboard")
    c.commit()
    yield c
    c.close()


def _skill(conn, q):
    r = route(conn, q)
    return r.skill if r else None


def test_email_routes_contact(conn):
    assert _skill(conn, "Koutis's email") == "contact_of_person"


def test_contact_phrase_routes_contact(conn):
    assert _skill(conn, "how do I contact professor Koutis") == "contact_of_person"


def test_title_routes_title(conn):
    assert _skill(conn, "what is Koutis's position") == "title_of_person"


def test_what_does_x_do_routes_title(conn):
    assert _skill(conn, "what does Koutis do") == "title_of_person"


def test_who_is_still_entity_card(conn):
    assert _skill(conn, "who is Koutis") == "entity_card"


def test_clubs_routes_orgs_by_type(conn):
    r = route(conn, "what clubs are there")
    assert r.skill == "orgs_by_type" and r.args["org_type"] == "club"


def test_list_student_orgs_routes_club(conn):
    r = route(conn, "list student organizations")
    assert r.skill == "orgs_by_type" and r.args["org_type"] == "club"


def test_how_many_clubs(conn):
    r = route(conn, "how many clubs")
    assert r.skill == "orgs_by_type" and r.args["org_type"] == "club"


def test_list_colleges(conn):
    r = route(conn, "list the colleges")
    assert r.skill == "orgs_by_type" and r.args["org_type"] == "college"


def test_list_colleges_at_njit_is_unscoped(conn):
    # "at NJIT" resolves the root but must NOT scope colleges to it (blocker: eager parent)
    r = route(conn, "list the colleges at NJIT")
    assert r.skill == "orgs_by_type" and r.args["org_type"] == "college"
    assert r.args["parent_org_id"] is None


def test_departments_in_ywcc_stays_org_departments(conn):
    assert _skill(conn, "departments in Ying Wu College of Computing") == "org_departments"


def test_faculty_in_dept_not_orgs_by_type(conn):
    # head-noun is faculty; 'department' merely names the org — must NOT become orgs_by_type
    assert _skill(conn, "list faculty in Computer Science department") != "orgs_by_type"


def test_who_is_chair_stays_people_by_role(conn):
    assert _skill(conn, "who is the chair of Computer Science") == "people_by_role"


# ── over-match negatives (review BLOCKER: bare what/which + singular type noun must NOT fire B3) ──
def test_which_college_is_x_in_not_b3(conn):
    assert _skill(conn, "which college is Computer Science in") != "orgs_by_type"


def test_what_college_should_i_apply_not_b3(conn):
    assert _skill(conn, "what college should I apply to") != "orgs_by_type"


def test_which_department_is_koutis_in_not_b3(conn):
    # must not dump all departments (review BLOCKER: unscoped-dept cannibalization)
    assert _skill(conn, "which department is Koutis in") != "orgs_by_type"


def test_office_hours_not_contact(conn):
    # "office hours" is a schedule ask, not a contact field (review MINOR)
    assert _skill(conn, "Koutis's office hours") != "contact_of_person"


# ── pronoun hardneg (review MAJOR: bare pronoun stays out of KG, no wrong-person) ──
def test_pronoun_position_not_kg(conn):
    assert route(conn, "what is his position") is None


def test_pronoun_contact_not_kg(conn):
    assert route(conn, "who do I contact about this") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest v2/tests/test_ws3_router.py -q`
Expected: FAIL (email/title/clubs cases return entity_card or None)

- [ ] **Step 3: Write minimal implementation**

Add cue regexes after `_INFO_CUE` (~line 174):

```python
# WS3 person-attribute sub-cues: contact vs title vs the generic card at the person return site.
# 'office' excludes "office hours" (a schedule ask, not a contact field) — review MINOR.
_CONTACT_CUE = re.compile(r"\b(e-?mail|phone|contact|reach)\b|\bnumbers?\b|\boffice\b(?!\s+hours)")
_TITLE_CUE = re.compile(r"\b(title|position)\b|\bwhat\s+does\b.+\bdo\b")
# WS3 org-type enumeration (B3): a PLURAL type noun is the discriminator — 'clubs'/'colleges'/'student
# organizations' fire; SINGULAR 'college'/'club' ("which college is X in", "what college should I apply
# to", "what is the ACM club") do NOT (review BLOCKER: over-match). Departments are NOT enumerated here
# (they stay on the existing _DEPT_ENUM/org_departments branch). Pronoun subjects can't be surname-mined.
_B3_TYPE = re.compile(r"\b(clubs|colleges|student\s+(?:organizations|orgs|groups)|rgos)\b")
_B3_ENUM_VERB = re.compile(r"\b(list|name|show|how\s+many|what|which|any|are\s+there|do\s+we\s+have)\b")
# Personal pronouns (never a surname) always block; this/that only in the flagged shapes
# ("about this", "this one") so "is that Koutis?" isn't over-blocked.
_PRONOUN_SUBJ = re.compile(
    r"\b(his|her|hers|their|theirs|he|she|they|him|them)\b"
    r"|\b(?:about|contact|reach)\s+(?:this|that)\b|\b(?:this|that)\s+one\b")
```

Add the `_person_skill` helper (near `_resolve_person`, ~after line 374):

```python
def _person_skill(q: str) -> str:
    """Which person-attribute skill a resolved-person query wants: contact vs title vs the full card.
    Contact wins over title if both cue words appear (rare)."""
    if _CONTACT_CUE.search(q):
        return "contact_of_person"
    if _TITLE_CUE.search(q):
        return "title_of_person"
    return "entity_card"
```

Add an explicit pronoun guard at the TOP of `_resolve_surname` (review MAJOR — don't rely on the
data-dependent accident that "his"/"this" happens to resolve to no surname):

```python
def _resolve_surname(conn: sqlite3.Connection, q: str) -> dict | Route | None:
    stripped = _NAME_PREFIX.sub("", q)
    if _PRONOUN_SUBJ.search(stripped):   # "what is his position" / "who do I contact about this" → no KG
        return None
    if len(_qtokens(stripped)) > 4:
        return None
    # ... unchanged body ...
```

**Insert** the B3 branch immediately BEFORE the existing department branch (router.py:546). Do **NOT**
modify lines 546-550 — the department/faculty branch stays exactly as-is (no unscoped all-departments
route; that cannibalizes "which department is Koutis in"):

```python
    # ── org enumeration by TYPE (WS3 B3): clubs / colleges only. Fires ONLY on an enumerate verb + a
    # PLURAL type noun; SINGULAR "which college is X in" falls through to RAG. Parent scopes ONLY to a
    # NON-root org (blocker: "list colleges at NJIT" must NOT scope to the university root → None).
    tm = _B3_TYPE.search(q)
    if tm and _B3_ENUM_VERB.search(q):
        org_type = "college" if tm.group(1).startswith("college") else "club"
        parent = org_id if (org_id is not None and not _is_university_root(conn, org_id)) else None
        return Route("orgs_by_type", {"org_type": org_type, "parent_org_id": parent})

    # (unchanged, current router.py:546-550 — shown for placement only, do NOT edit)
    # if (_DEPT_ENUM.search(q) and org_id is not None and not _FACULTY_CUE.search(q)
    #         and _has_child_departments(conn, org_id)):
    #     return Route("org_departments", {"org_id": org_id})
    # if org_id is not None and _FACULTY_CUE.search(q):
    #     return Route("faculty_in_department", {"org_id": org_id})
```

At the person-branch return sites, dispatch by cue. Replace lines 581-586:

```python
    qn = _NAME_PREFIX.sub("", q).strip()
    if len(named) == 1 and (_PERSON_INTENT.search(q) or _PERSON_ATTR.search(q)
                            or _CONTACT_CUE.search(q) or _TITLE_CUE.search(q)
                            or _is_bare_name(qn, named[0])):
        return Route(_person_skill(q),
                     {"entity_id": named[0]["entity_id"], "name": named[0]["name"]})
    if len(named) > 1 and (_PERSON_INTENT.search(q) or _PERSON_ATTR.search(q)
                           or _CONTACT_CUE.search(q) or _TITLE_CUE.search(q)):
        return Route("person_disambig", {"candidates": named})
```

Replace the surname branch trigger + return (lines 593-600):

```python
    qn_toks = _qtokens(qn)
    if (_PERSON_INTENT.search(q) or _PERSON_ATTR.search(q) or _CONTACT_CUE.search(q)
            or _TITLE_CUE.search(q) or _NAME_PREFIX.search(q) or _INFO_CUE.search(q)
            or len(qn_toks) == 1):
        person = _resolve_surname(conn, q)
        if isinstance(person, Route):
            return person
        if isinstance(person, dict):
            return Route(_person_skill(q),
                         {"entity_id": person["entity_id"], "name": person["name"]})
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest v2/tests/test_ws3_router.py -q`
Expected: PASS (all — incl. the over-match negatives + pronoun hardnegs)

- [ ] **Step 5: Run the existing router suite (no regression)**

Run: `python -m pytest v2/tests/test_router.py v2/tests/test_router_precision.py v2/tests/test_router_robustness.py v2/tests/test_entity.py -q`
Expected: PASS (all pre-existing). If any fail, diagnose before proceeding — a cue may be too broad.

- [ ] **Step 6: Commit**

```bash
git add v2/core/retrieval/router.py v2/tests/test_ws3_router.py
git commit -m "feat(ws3): route contact/title/orgs_by_type — cue-dispatch at person sites + B3 branch"
```

---

### Task 6: Slot-extractor fallback (slot_extractor.py)

**Files:**
- Modify: `v2/core/retrieval/slot_extractor.py` — `KG_SKILL_NAMES` (~line 33), `REQUIRED_SLOTS` (~line 42), `build_schema` (~line 86), `resolve_and_validate` (~line 320 person branch; new B3 branch)
- Test: `v2/tests/test_ws3_slot.py` (create)

**Interfaces:**
- Consumes: `_resolve_person_slot`, `resolve_org_slot` (inner), `srouter._find_org`, `srouter.fuzzy_org`, `Route`.
- Produces: extractor may emit `contact_of_person`/`title_of_person` (slots `{person}`) and `orgs_by_type` (slots `{org_type}` + optional `org`); `resolve_and_validate` returns executable Routes or abstains.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_ws3_slot.py
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
from v2.core.retrieval.slot_extractor import (resolve_and_validate, KG_SKILL_NAMES,
                                              REQUIRED_SLOTS, build_schema)


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    njit = ensure_org(c, "njit", "NJIT", None, type="university")
    ywcc = ensure_org(c, "ywcc", "Ying Wu College of Computing", "njit", type="college")
    cs = ensure_org(c, "cs", "Computer Science", "ywcc", type="department")
    ensure_org(c, "gsa", "Graduate Student Association", "njit", type="gsa")
    ensure_org(c, "acm", "ACM Student Chapter", "gsa", type="club")
    sync_org_nodes(c)
    project_appointment(c, person_key="d/koutis", name="Ioannis Koutis", org_id=cs,
                        category="faculty", titles=["Professor"], source_section="manual",
                        source="dashboard")
    c.commit()
    yield c
    c.close()


def test_registry_has_three_new_skills():
    for s in ("contact_of_person", "title_of_person", "orgs_by_type"):
        assert s in KG_SKILL_NAMES
    assert REQUIRED_SLOTS["contact_of_person"] == ("person",)
    assert REQUIRED_SLOTS["title_of_person"] == ("person",)
    assert REQUIRED_SLOTS["orgs_by_type"] == ("org_type",)


def test_schema_has_org_type_enum():
    props = build_schema()["properties"]["slots"]["properties"]
    assert set(props["org_type"]["enum"]) == {"club", "department", "college"}


def test_resolve_contact(conn):
    r = resolve_and_validate(conn, "contact_of_person", {"person": "Koutis"}, "koutis email")
    assert r.skill == "contact_of_person" and r.args["entity_id"] == "d/koutis"


def test_resolve_title(conn):
    r = resolve_and_validate(conn, "title_of_person", {"person": "Koutis"}, "koutis position")
    assert r.skill == "title_of_person" and r.args["entity_id"] == "d/koutis"


def test_resolve_orgs_by_type_club(conn):
    r = resolve_and_validate(conn, "orgs_by_type", {"org_type": "club"}, "list clubs")
    assert r.skill == "orgs_by_type" and r.args["org_type"] == "club" and r.args["parent_org_id"] is None


def test_resolve_orgs_by_type_bad_type_abstains(conn):
    assert resolve_and_validate(conn, "orgs_by_type", {"org_type": "office"}, "list offices") is None


def test_resolve_orgs_by_type_parent(conn):
    r = resolve_and_validate(conn, "orgs_by_type", {"org_type": "club", "org": "GSA"}, "clubs in gsa")
    pid = conn.execute("SELECT id FROM organizations WHERE slug='gsa'").fetchone()[0]
    assert r.args["parent_org_id"] == pid


def test_resolve_contact_ambiguous_person_disambig(conn):
    project_appointment(conn, person_key="d/wang1", name="Guiling Wang",
                        org_id=conn.execute("SELECT id FROM organizations WHERE slug='cs'").fetchone()[0],
                        category="faculty", titles=["Professor"], source_section="manual", source="dashboard")
    project_appointment(conn, person_key="d/wang2", name="Jian Wang",
                        org_id=conn.execute("SELECT id FROM organizations WHERE slug='cs'").fetchone()[0],
                        category="faculty", titles=["Professor"], source_section="manual", source="dashboard")
    conn.commit()
    r = resolve_and_validate(conn, "contact_of_person", {"person": "Wang"}, "wang email")
    assert r.skill == "person_disambig"


def test_contact_resolves_without_identity_cue(conn):
    # B1/B4 skip the entity_card _identity_cued gate — the contact/title cue IS the intent (review MAJOR)
    r = resolve_and_validate(conn, "contact_of_person", {"person": "Koutis"}, "koutis")
    assert r.skill == "contact_of_person" and r.args["entity_id"] == "d/koutis"


def test_resolve_school_abstains(conn):
    # 'school' is not in the enum → abstain, never mapped to college (design deferral)
    assert resolve_and_validate(conn, "orgs_by_type", {"org_type": "school"}, "list schools") is None


def test_extract_slots_keeps_org_type(conn):
    # END-TO-END through extract_slots (BLOCKER: org_type must survive the slot whitelist). A stub
    # generator returns the JSON Granite would emit; the org_type slot must reach resolve_and_validate.
    from v2.core.retrieval.slot_extractor import extract_slots
    def stub(system, prompt, schema):
        return {"skill": "orgs_by_type", "slots": {"org_type": "club"}, "confidence": 0.9}
    res = extract_slots("what clubs are there", stub)
    assert res.skill == "orgs_by_type"
    assert res.slots.get("org_type") == "club"   # NOT stripped
    r = resolve_and_validate(conn, res.skill, res.slots, "what clubs are there")
    assert r.skill == "orgs_by_type" and r.args["org_type"] == "club"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest v2/tests/test_ws3_slot.py -q`
Expected: FAIL (skills not in registry; resolve returns None)

- [ ] **Step 3: Write minimal implementation**

Extend `KG_SKILL_NAMES` (line 33):

```python
KG_SKILL_NAMES: tuple[str, ...] = (
    "entity_card", "research_of_person", "metric_of_person", "link_of_person",
    "people_by_role", "people_by_name", "faculty_in_department", "people_in_org",
    "officers_in_org", "top_people_by_metric", "people_by_research_area",
    "count_people_by_research_area", "areas_in_org", "area_counts",
    "faculty_areas_in_department", "people_by_area_tag", "org_departments",
    "contact_of_person", "title_of_person", "orgs_by_type",
)
```

Extend `REQUIRED_SLOTS`:

```python
    "contact_of_person": ("person",),
    "title_of_person": ("person",),
    "orgs_by_type": ("org_type",),
```

Import the shared enum at the top of `slot_extractor.py` (near the other retrieval imports):

```python
from v2.core.retrieval.skills import ORG_TYPE_ENUM
```

Add the `org_type` enum to `build_schema()` slot properties (alongside `person`/`org`/`area`/`metric` at slot_extractor.py:97-104):

```python
                    "org_type": {"type": "string", "enum": list(ORG_TYPE_ENUM)},
```

**BLOCKER FIX — add `"org_type"` to the `extract_slots` slot whitelist (slot_extractor.py:177)** so a
model-emitted `org_type` is not stripped:

```python
    for k in ("person", "org", "area", "metric", "profile", "role", "order", "org_type"):
        v = slots.get(k)
        if isinstance(v, str) and v.strip():
            clean[k] = v.strip()
```

**Update `_SYSTEM` (slot_extractor.py:112-126)** — append three skill clauses before the closing paren:

```python
    " contact_of_person (X's email/phone/office — needs person); title_of_person (X's title/position "
    "— needs person); orgs_by_type (list/how-many CLUBS or COLLEGES — needs org_type in "
    "{club,college}, org optional as a parent)."
```

**Update the few-shots (slot_extractor.py:130-141)** — re-point the Koutis "reach" row (it is the WS1
finding B1 fixes) and add a B3 example:

```python
    ('I am trying to reach someone named Koutis',
     {"skill": "contact_of_person", "slots": {"person": "Koutis"}, "confidence": 0.85}),
    ('what clubs are there at NJIT',
     {"skill": "orgs_by_type", "slots": {"org_type": "club"}, "confidence": 0.9}),
```

In `resolve_and_validate`, add `contact_of_person`/`title_of_person` as a SEPARATE branch (NOT folded into `entity_card`/`research_of_person`) — WITHOUT the entity_card identity-cue gate, since the contact/title cue IS the intent:

```python
    # WS3 person-attribute skills — inherit WS2 resolution; ambiguous ⇒ person_disambig.
    if skill in ("contact_of_person", "title_of_person"):
        st = _resolve_person_slot(conn, slots["person"], message)
        if st[0] == "ambiguous":
            return Route("person_disambig", {"candidates": st[1]})
        if st[0] != "ok":
            return None
        return Route(skill, {"entity_id": st[1], "name": st[2]})

    # WS3 orgs_by_type — validate the type enum; optional parent via the shared org resolver.
    if skill == "orgs_by_type":
        org_type = slots["org_type"]
        if org_type not in ORG_TYPE_ENUM:     # 'school'/anything off-enum ⇒ abstain (never mapped)
            return None
        parent_id, named_unresolved = resolve_org_slot()
        if named_unresolved:
            return None                       # a parent WAS named but didn't resolve ⇒ abstain
        return Route("orgs_by_type", {"org_type": org_type, "parent_org_id": parent_id})
```

(Place both new branches after the existing `entity_card`/`research_of_person` block and before `people_by_name`, so `resolve_org_slot` — defined earlier in the function — is in scope.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest v2/tests/test_ws3_slot.py -q`
Expected: PASS (8 passed)

- [ ] **Step 5: Run the existing slot-extractor suite (no regression)**

Run: `python -m pytest v2/tests/test_slot_extractor.py v2/tests/test_slot_extractor_wiring.py v2/tests/test_ws2_fuzzy.py -q`
Expected: PASS (all pre-existing)

- [ ] **Step 6: Commit**

```bash
git add v2/core/retrieval/slot_extractor.py v2/tests/test_ws3_slot.py
git commit -m "feat(ws3): slot-extractor fallback — 3 skills, org_type enum, resolve/abstain arms"
```

---

### Task 7: Gold labels + regression gate + correctness Qs

**Files:**
- Modify: `eval/router/labeled_routes.jsonl` (append rows; do NOT touch existing test/hardneg rows)
- Modify: `eval/questions.txt` (append WS3 verification Qs — `feedback_grow_correctness_suite`)
- Reference: `eval/router/LABELING_PROTOCOL.md`, `eval/router/RUBRIC.md`, `eval/router/all_questions.jsonl`, `scripts/router_slot_bakeoff.py`

**Interfaces:**
- Consumes: the JSONL row schema used by existing rows (inspect a few first).
- Produces: ≥~15 labeled rows per new skill (real phrasings from `all_questions.jsonl` preferred, marked provenance; some `split:test`, some train), + hardneg rows for bare-pronoun cases; a passing bakeoff.

- [ ] **Step 1: Inspect the row schema + mine real phrasings**

Run:
```bash
grep -m3 "entity_card" eval/router/labeled_routes.jsonl        # see exact field shape
head -30 eval/router/LABELING_PROTOCOL.md eval/router/RUBRIC.md
grep -iE "email|contact|phone|clubs?|student organization|position|title|what does" eval/router/all_questions.jsonl | head -40
```
Expected: the JSONL fields (question, family, skill, split, annotator, provenance/notes…) and a list of real user phrasings to label.

- [ ] **Step 2: Append labeled rows**

Add rows following the observed schema (use REAL `all_questions.jsonl` phrasings where they exist, mark provenance `real`; fill to ≥~15/skill with clearly-marked `seed`). Cover:
- `contact_of_person`: "Koutis's email", "how do I contact professor <name>", "<name> phone number", "email for <name>", "<name>'s office" …
- `title_of_person`: "what is <name>'s position", "what does <name> do", "<name>'s title" …
- `orgs_by_type`: "what clubs are there", "how many clubs", "list student organizations", "name the clubs", "list the colleges" …
- **hardneg** (guard regression): "what is his position", "who do I contact about this?" → `family:KG` is WRONG here; label per protocol as the hardneg/CLARIFY split so the bakeoff proves the pronoun cases don't mis-fire.
- **hardneg (school→abstain deferral):** "list the schools", "how many schools are there" → NOT `orgs_by_type` (design defers `school`). Label as hardneg so a future classifier drift is caught.
- Include a few `split:test` rows for `entity_card` and `org_departments` so the new gate_e (below) has gold to measure against.

Split assignment per `LABELING_PROTOCOL.md` (some rows `split:test` for the blind gate, rest train/seed). Do NOT modify existing rows.

- [ ] **Step 3: Append correctness Qs to eval/questions.txt**

Add under an appropriate `# category` header:
```
# WS3 KG skills
Koutis's email
how do I contact professor Koutis
what is Koutis's position
what does Koutis do
what clubs are there
how many clubs
list student organizations
list the colleges
```

- [ ] **Step 4: Strengthen the bakeoff — add a per-skill non-regression gate (review MAJOR)**

The current gate only checks `family_accuracy` (gate_a); `skill_accuracy` is printed but ungated, so a
kNN skill regression on `entity_card`/`org_departments` (WS3's cannibalization risk) passes silently.
In `scripts/router_slot_bakeoff.py`: capture the prediction pairs (line 68-69 currently discard them —
change to `b_score, b_pairs = _fam_acc(before, test)` / `a_score, a_pairs = _fam_acc(after, test)`), then
add before the `=== GATES ===` block:

```python
    # (e) per-skill non-regression for the skills WS3 could cannibalize
    print("\n=== SKILL NON-REGRESSION (entity_card, org_departments) ===")
    def _ok(pairs, target):
        return sum(1 for ex, p in pairs if ex.family == "KG" and ex.skill == target and p.skill == target)
    def _tot(pairs, target):
        return sum(1 for ex, p in pairs if ex.family == "KG" and ex.skill == target)
    gate_e = True
    for target in ("entity_card", "org_departments"):
        b_ok, a_ok, tot = _ok(b_pairs, target), _ok(a_pairs, target), _tot(a_pairs, target)
        ok = a_ok >= b_ok
        gate_e = gate_e and ok
        print(f"  {target}: BEFORE {b_ok}/{tot} AFTER {a_ok}/{tot} [{'OK' if ok else 'REGRESS'}]")
```

Wire it into the final verdict: `core = gate_a and gate_b and gate_c and gate_e` and print gate_e in the
`=== GATES ===` list.

- [ ] **Step 5: Run the bakeoff regression gate**

Run: `python scripts/router_slot_bakeoff.py`
Expected: (a) blind-test **family accuracy ≥ the post-WS2 baseline the run prints**; (b) **0 new hardneg mis-fires**; (c) regression paraphrases pass; (e) **no skill regression** on entity_card/org_departments; the three new skills show non-zero correct dispatch. Record the printed numbers.

- [ ] **Step 6: Commit**

```bash
git add eval/router/labeled_routes.jsonl eval/questions.txt scripts/router_slot_bakeoff.py
git commit -m "eval(ws3): gold rows + hardneg pronoun/school guards + gate_e skill non-regression"
```

---

### Task 8: Live verification + full-suite regression + scope diff

**Files:** none (verification only) — produces the evidence for the merge gates.

- [ ] **Step 1: Live-DB case outputs (the §7 verification bar)**

Run each through the real X-ray on the LIVE DB and record the resolved skill + rendered answer:
```bash
for q in "Koutis's email" "how do I contact professor Koutis" "what clubs are there" \
         "list student organizations" "how many clubs" "what is Koutis's position" \
         "what does Koutis do"; do
  echo "== $q =="; bash scripts/ask.sh "$q" --answer 2>/dev/null | tail -20
done
```
Expected: contact→contact_of_person (real email); clubs→orgs_by_type (the live clubs, count+list); position/what-does→title_of_person. If Koutis is absent/typo'd live, confirm WS2 fuzzy resolves or clarifies (never wrong-person).

- [ ] **Step 2: Disambiguation set**

Confirm the §4a table on the live DB: "who is Koutis"→entity_card; "Koutis's email"→contact_of_person; "what is Koutis's title"→title_of_person; "who is the chair of CS"→people_by_role; "departments in YWCC"→org_departments; "what clubs are there"→orgs_by_type. No cannibalization.

- [ ] **Step 3: Anti-fabrication check**

Pick a live person with a missing contact field (`SELECT key,name FROM nodes WHERE type='Person' AND json_extract(attrs,'$.email') IS NULL LIMIT 1`) → ask "<name>'s email" → assert the answer honestly says not-on-file, never fabricates or blank-succeeds.

- [ ] **Step 4: Full-suite regression**

Run: `python -m pytest v2/tests/ -q`
Expected: no NEW failures vs the pre-WS3 baseline. Prove any residual fails are pre-existing (stash-compare against `main` if needed, per WS2 precedent). Record counts.

- [ ] **Step 5: Scope diff + goals checklist**

Run: `git diff --stat main...HEAD` and confirm ONLY the files in the spec's §9 changed — NO `retriever.py`, `ollama_client.py`, or family-classifier-core changes. Fill the spec's §10 goals checklist (shipped/deferred).

- [ ] **Step 6: Package for the HARD-GATE review**

Assemble: the 3 skill signatures, per-skill new label counts, case-by-case outputs, disambiguation results, bakeoff numbers, and the scope diff. Dispatch senior-eng + RAG reviewers + Codex second opinion with these artifacts; relay findings; get owner sign-off before merge + restart.

---

## Self-Review

**Spec coverage:** B1 (T1), B3 + delegation (T3), B4 (T2), render+deterministic (T4), routing incl. disambiguation contract (T5), slot-extractor incl. org_type enum + abstain (T6), labeling + hardneg + bakeoff (T7), the §7 verification bar + §8 merge gates + §9 scope diff + §10 checklist (T8). "school→abstain" honored (not mapped in T5/T6). Count+list render (T4). Coexist (T3/T5). All spec sections map to a task.

**Placeholder scan:** No TBD/TODO; every code step shows real code; every run step shows the command + expected result. (T7 mining is inherently data-dependent — its Step 1 command produces the exact phrasings to label, which is a real action, not a placeholder.)

**Type consistency:** `contact_of_person`→`{name,email,phone,office,present}` (T1) consumed by T4 render + T6 resolve. `title_of_person`→`{name,titles:[(t,o)]}` (T2) consumed by T4. `orgs_by_type(conn,org_type,parent_org_id=None)->list[str]` (T3) consumed by T4 run + T6 resolve. `ORG_TYPE_ENUM` defined in skills.py (T3), imported by slot_extractor (T6) and used by the router B3 branch (T5). `_person_skill(q)->str` (T5) returns exactly the T1/T2/entity_card skill names; `_B3_TYPE`/`_B3_ENUM_VERB`/`_PRONOUN_SUBJ`/`_CONTACT_CUE`/`_TITLE_CUE` all defined in T5. `_fam_acc` pairs captured as `b_pairs`/`a_pairs` (T7 gate_e). `REQUIRED_SLOTS` keys match `KG_SKILL_NAMES` additions (T6). Consistent throughout.

**Review fold (HARD-GATE, all GO-WITH-CHANGES):** all blockers/majors/minors from the senior-eng + RAG + Codex review are folded — org_type whitelist (T6), B3 cue tightened to plural-noun + drop unscoped-dept (T5), eager-parent fix (T5), `_CONTACT_CUE` in triggers (T5), `_SYSTEM`/few-shot + re-pointed Koutis row (T6), `ORG_TYPE_ENUM` validated in skill+router+extractor (T3/T5/T6), gate_e (T7), pronoun guard + hardneg tests (T5/T7), title category-fallback wording + office-hours + plural-label + fixture cleanups (T1/T4/T5). Owner greeting decision applied (compose, not deterministic). See spec §11.
