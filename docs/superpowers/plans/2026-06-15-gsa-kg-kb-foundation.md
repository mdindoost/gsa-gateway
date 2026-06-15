# GSA KG+KB Foundation (Plan 1 of 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up GSA's knowledge as the YWCC-style two-layer model — officers/DepReps/RGOs in the graph (`Person`/`Org`/`has_role`/`part_of`) and policy/program prose in the KB — provisioned manually (the path that always works), with a read-only spike that decides whether Plan 2 (Wix crawl automation) is worth building.

**Architecture:** Reuse the existing graph projection (`project_appointment`, `ensure_org`, `sync_org_nodes`, `org_node_id`) and KB tables (`knowledge_items` + `knowledge_vectors`). Two new ingest helpers (roster→KG, docs→KB) with thin gated CLI wrappers, one new structured-retrieval skill (`officers_in_org`) plus router patterns, a QA retirement, and an alignment check. Everything is source-tagged `dashboard` so a future crawl (`source='crawler'`) or `--reset` never clobbers it.

**Tech Stack:** Python 3.11, sqlite3 + sqlite-vec, Ollama `nomic-embed-text` (embeddings), tiktoken (chunking, via existing `bot/services/chunker.py`), pytest.

**Spec:** `docs/superpowers/specs/2026-06-15-gsa-website-kg-kb-crawl-design.md`

---

## File Structure

- `docs/superpowers/findings/2026-06-15-gsa-wix-extraction.md` — **created** by Task 1 (spike result).
- `v2/core/ingestion/roster.py` — **created**. `project_roster()` + `reconcile_roster()`: pure graph projection of an officer/RGO roster (no file/network I/O). Tested in isolation.
- `v2/core/ingestion/gsa_docs.py` — **created**. `chunk_doc()` + `upsert_doc_items()`: turn one prose doc into chunked `knowledge_items`. Pure (takes text in). Tested in isolation.
- `scripts/gsa_ingest_people.py` — **created**. Thin CLI: read `bot/data/gsa_people.yml` → `project_roster`/`reconcile_roster`; dry-run default + hardened backup on `--commit`.
- `scripts/gsa_ingest_docs.py` — **created**. Thin CLI: read `bot/data/sources/gsa/*.md` → `gsa_docs`; dry-run default + hardened backup on `--commit`.
- `scripts/gsa_retire_qa.py` — **created**. Thin CLI: deactivate the GSA `faq` items; prints the coverage checklist.
- `v2/core/retrieval/skills.py` — **modified**. Add `officers_in_org()`.
- `v2/core/retrieval/router.py` — **modified**. Add officer/governance routing.
- `scripts/verify_kg.py` — **modified**. Add `verify_gsa()`.
- `bot/data/gsa_people.yml` — **created** (seed roster, hand-filled from the site/contacts).
- `bot/data/sources/gsa/` — **created** dir for the prose source docs (`.md`/`.txt`).
- `bot/data/bot_features.md` — **rewritten** as a truthful, interface-agnostic capability doc.
- Tests under `v2/tests/`.

---

## Task 1: Wix extraction feasibility spike (read-only research)

This task ships **no production code** — it produces a written finding that decides whether Plan 2 (Wix crawl) is built or we stay manual. Do not write to the DB.

**Files:**
- Create: `docs/superpowers/findings/2026-06-15-gsa-wix-extraction.md`

- [ ] **Step 1: Download the people pages (project UA, read-only)**

```bash
cd /home/md724/gsa-gateway
mkdir -p /tmp/gsa_spike
UA="GSA-Gateway-Bot/1.0 (+https://github.com/mdindoost/gsa-gateway)"
for p in "" eboard deprep rgo governance; do
  curl -sL -A "$UA" "https://www.gsanjit.com/$p" -o "/tmp/gsa_spike/${p:-home}.html" \
    -w "$p HTTP %{http_code} %{size_download}b\n"
done
```

Expected: HTTP 200 for the pages that exist (some may 404 — record which).

- [ ] **Step 2: Look for structured data (embedded JSON / Wix CMS collections)**

```bash
cd /tmp/gsa_spike
for f in *.html; do
  echo "=== $f ==="
  grep -o -E "warmupData|__INITIAL_STATE__|wixapps|\"items\":\[|dataItems|Fernando|Durvish|Buschmann" "$f" \
    | sort | uniq -c
done
```

Record: do officer/DepRep/RGO names appear inside a parseable JSON blob (e.g. a `warmupData`/`items` array), or only as scattered rendered text?

- [ ] **Step 3: Try to isolate one roster as JSON**

If Step 2 shows a JSON blob, extract and pretty-print the candidate array to judge whether names+roles are cleanly keyed:

```bash
cd /tmp/gsa_spike
python3 - <<'PY'
import re, json, glob
for f in glob.glob("*.html"):
    html = open(f, encoding="utf-8", errors="ignore").read()
    for m in re.finditer(r'\{"[^"]*items"\s*:\s*\[.*?\]\}', html):
        try:
            obj = json.loads(m.group(0))
            print(f, "->", json.dumps(obj)[:300])
        except Exception:
            pass
PY
```

- [ ] **Step 4: Write the finding + decision**

Create `docs/superpowers/findings/2026-06-15-gsa-wix-extraction.md` with:
- which pages exist (URLs + HTTP status),
- whether rosters are in clean embedded JSON / a CMS collection (→ **CRAWL feasible**) or only rendered DOM (→ **MANUAL**),
- the concrete extraction method if crawlable (which JSON path / selector),
- a one-line **DECISION: CRAWL** or **DECISION: MANUAL**, with rationale.

- [ ] **Step 5: Commit**

```bash
cd /home/md724/gsa-gateway
git add docs/superpowers/findings/2026-06-15-gsa-wix-extraction.md
git commit -m "docs(gsa): Wix extraction feasibility spike — crawl-vs-manual decision"
```

> The remaining tasks (2–9) are **path-independent**: they build the same KG+KB whether the roster/docs arrive by crawl (Plan 2) or by hand. Proceed with them regardless of the spike's decision.

---

## Task 2: `officers_in_org` structured skill

**Files:**
- Modify: `v2/core/retrieval/skills.py` (add function after `faculty_in_department`)
- Test: `v2/tests/test_skills_officers.py`

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_skills_officers.py
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
from v2.core.retrieval.skills import officers_in_org, resolve_org


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(2,1,'Graduate Student Association','gsa','custom')")
    c.commit()
    yield c
    c.close()


def test_officers_in_org_returns_name_and_title(conn):
    phd = ensure_org(conn, "phd-club", "PhD Club", parent_slug="gsa", type="unit")
    sync_org_nodes(conn)
    project_appointment(conn, person_key="dashboard/gsa/mohith-oduru", name="Mohith Oduru",
                        org_id=2, category="officer", titles=["VP Finances"],
                        source_section="E-Board", source="dashboard")
    project_appointment(conn, person_key="dashboard/gsa/fernando", name="Fernando Vera Buschmann",
                        org_id=2, category="officer", titles=["GSA President"],
                        source_section="E-Board", source="dashboard")
    project_appointment(conn, person_key="dashboard/phd-club/ana", name="Ana Lee",
                        org_id=phd, category="officer", titles=["President"],
                        source_section="RGO", source="dashboard")
    gsa = resolve_org(conn, "gsa")
    officers = officers_in_org(conn, gsa)
    assert ("Fernando Vera Buschmann", "GSA President") in officers
    assert ("Mohith Oduru", "VP Finances") in officers
    # scoped to the GSA org itself, NOT the PhD Club sub-org
    assert all(name != "Ana Lee" for name, _ in officers)
    # the club roster is reachable by resolving that org
    assert ("Ana Lee", "President") in officers_in_org(conn, phd)


def test_officers_in_org_ignores_inactive_and_other_categories(conn):
    sync_org_nodes(conn)
    pid = project_appointment(conn, person_key="dashboard/gsa/x", name="Faculty X",
                              org_id=2, category="faculty", titles=["Professor"],
                              source_section="E-Board", source="dashboard")
    assert officers_in_org(conn, 2) == []   # 'faculty' is not an officer/deprep role
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /home/md724/gsa-gateway && source venv/bin/activate && python -m pytest v2/tests/test_skills_officers.py -q`
Expected: FAIL — `ImportError: cannot import name 'officers_in_org'`.

- [ ] **Step 3: Implement the skill**

Add to `v2/core/retrieval/skills.py` immediately after `faculty_in_department`:

```python
def officers_in_org(conn: sqlite3.Connection, org_id: int) -> list[tuple[str, str]]:
    """(name, title) for every active officer/DepRep appointed directly to this org.

    Queries the graph `has_role` edges (category 'officer'/'deprep') whose target Org node
    bridges this exact ``org_id`` (NOT descendants — GSA officers are distinct from an RGO's
    officers; resolve the RGO's id to list its officers). Title is the first entry in the
    edge's ``attrs.titles``; falls back to the category. Sorted by name."""
    rows = conn.execute(
        "SELECT p.name, e.attrs, e.category FROM edges e "
        "JOIN nodes p ON p.id=e.src_id "
        "JOIN nodes o ON o.id=e.dst_id "
        "WHERE e.type='has_role' AND e.is_active=1 AND p.is_active=1 "
        "AND e.category IN ('officer','deprep') "
        "AND json_extract(o.attrs,'$.org_id')=?",
        (org_id,)).fetchall()
    out: list[tuple[str, str]] = []
    for name, attrs, category in rows:
        titles = (json.loads(attrs) if attrs else {}).get("titles") or []
        out.append((name, titles[0] if titles else category))
    return sorted(set(out))
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest v2/tests/test_skills_officers.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add v2/core/retrieval/skills.py v2/tests/test_skills_officers.py
git commit -m "feat(retrieval): officers_in_org skill (officer/deprep roster from the graph)"
```

---

## Task 3: Router patterns for officer/governance questions

**Files:**
- Modify: `v2/core/retrieval/router.py`
- Test: `v2/tests/test_router_officers.py`

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_router_officers.py
from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import create_all
from v2.core.retrieval.router import route


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(2,1,'Graduate Student Association','gsa','custom')")
    c.commit()
    yield c
    c.close()


def test_who_are_the_gsa_officers_routes(conn):
    r = route(conn, "who are the GSA officers?")
    assert r is not None and r.skill == "officers_in_org" and r.args["org_id"] == 2


def test_who_is_the_gsa_president_routes(conn):
    r = route(conn, "who is the GSA president")
    assert r is not None and r.skill == "officers_in_org" and r.args["org_id"] == 2


def test_descriptive_question_still_falls_through(conn):
    # no officer cue + no org-scoped structured intent -> semantic RAG (None)
    assert route(conn, "what is the meaning of graduate research day") is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest v2/tests/test_router_officers.py -q`
Expected: FAIL — `who is the GSA president` returns None (no officer routing yet).

- [ ] **Step 3: Implement the routing**

In `v2/core/retrieval/router.py`, add this module-level regex after the `_LISTS_AREA` definition:

```python
# Officer / governance cue ("who are the OFFICERS", "who is the PRESIDENT / VP finance").
# Deliberately excludes 'professor'/'faculty' so it never hijacks the YWCC faculty branch.
_OFFICER = re.compile(
    r"\b(officers?|e-?board|executive board|president|vice[- ]president|\bvp\b|"
    r"treasurer|secretary|deprep|department representatives?)\b")
```

Then in `route()`, add this branch **immediately before** the `if "department" in q ...` line (so an org-scoped officer question wins over the generic department/faculty branches):

```python
    if org_id is not None and _OFFICER.search(q):
        return Route("officers_in_org", {"org_id": org_id})
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest v2/tests/test_router_officers.py v2/tests/test_router.py -q`
Expected: PASS (new tests pass; existing router tests still pass).

- [ ] **Step 5: Commit**

```bash
git add v2/core/retrieval/router.py v2/tests/test_router_officers.py
git commit -m "feat(retrieval): route officer/governance questions to officers_in_org"
```

---

## Task 4: roster→KG ingest (`project_roster` + `reconcile_roster`)

**Files:**
- Create: `v2/core/ingestion/roster.py`
- Test: `v2/tests/test_roster.py`

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_roster.py
from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import create_all
from v2.core.ingestion.roster import project_roster, reconcile_roster
from v2.core.retrieval.skills import officers_in_org

ROSTER = {
    "org": {"slug": "gsa", "name": "Graduate Student Association", "parent": "njit"},
    "people": [
        {"name": "Fernando Vera Buschmann", "title": "GSA President", "category": "officer",
         "email": "gsa-pres@njit.edu", "note": "Data Science PhD"},
        {"name": "Mohith Oduru", "title": "VP Finances", "category": "officer",
         "email": "gsa-vpf@njit.edu"},
    ],
    "rgos": [
        {"slug": "phd-club", "name": "PhD Club",
         "people": [{"name": "Ana Lee", "title": "President", "category": "officer"}]},
    ],
}


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.commit()
    yield c
    c.close()


def test_project_roster_creates_officers_and_rgos(conn):
    keys = project_roster(conn, ROSTER)
    gsa = conn.execute("SELECT id FROM organizations WHERE slug='gsa'").fetchone()[0]
    phd = conn.execute("SELECT id FROM organizations WHERE slug='phd-club'").fetchone()[0]
    assert ("Fernando Vera Buschmann", "GSA President") in officers_in_org(conn, gsa)
    assert ("Ana Lee", "President") in officers_in_org(conn, phd)
    # RGO is part_of GSA
    assert conn.execute("SELECT parent_id FROM organizations WHERE id=?", (phd,)).fetchone()[0] == gsa
    # returns the set of (org_id, person_key) it touched (for reconcile)
    assert any(k[1] == "dashboard/gsa/fernando-vera-buschmann" for k in keys)


def test_reconcile_roster_deactivates_departed_officer(conn):
    project_roster(conn, ROSTER)
    gsa = conn.execute("SELECT id FROM organizations WHERE slug='gsa'").fetchone()[0]
    # Mohith leaves: re-ingest without him, then reconcile
    smaller = dict(ROSTER, people=[ROSTER["people"][0]])
    present = project_roster(conn, smaller)
    removed = reconcile_roster(conn, present)
    names = [n for n, _ in officers_in_org(conn, gsa)]
    assert "Mohith Oduru" not in names
    assert "Fernando Vera Buschmann" in names
    assert removed == 1


def test_project_roster_idempotent(conn):
    project_roster(conn, ROSTER)
    project_roster(conn, ROSTER)            # second run: no duplicates
    gsa = conn.execute("SELECT id FROM organizations WHERE slug='gsa'").fetchone()[0]
    assert len(officers_in_org(conn, gsa)) == 2
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest v2/tests/test_roster.py -q`
Expected: FAIL — `ModuleNotFoundError: v2.core.ingestion.roster`.

- [ ] **Step 3: Implement `roster.py`**

```python
# v2/core/ingestion/roster.py
"""Project a GSA officer/RGO roster into the graph (manual people path). Pure: takes a
dict in, writes nodes/edges via the shared graph helpers, source='dashboard'. The crawl
adapter (Plan 2) would feed the SAME shapes, so this is the single projection path."""
from __future__ import annotations

import re
import sqlite3

from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _project_people(conn, org_id, org_slug, people) -> list[tuple[int, str]]:
    touched: list[tuple[int, str]] = []
    for p in people:
        key = f"dashboard/{org_slug}/{_slug(p['name'])}"
        pid = project_appointment(
            conn, person_key=key, name=p["name"], org_id=org_id,
            category=p.get("category", "officer"), titles=[p["title"]],
            source_section=p.get("source_section", "roster"), source="dashboard")
        # carry email/note as node attrs (additive; project_appointment passes attrs=None)
        extra = {k: p[k] for k in ("email", "note") if p.get(k)}
        if extra:
            row = conn.execute("SELECT attrs FROM nodes WHERE id=?", (pid,)).fetchone()
            import json
            attrs = json.loads(row[0]) if row and row[0] else {}
            attrs.update(extra)
            conn.execute("UPDATE nodes SET attrs=?, updated_at=datetime('now') WHERE id=?",
                         (json.dumps(attrs), pid))
        touched.append((org_id, key))
    return touched


def project_roster(conn: sqlite3.Connection, roster: dict) -> list[tuple[int, str]]:
    """Create the GSA org (if needed), its officers, and each RGO + its officers. Returns
    the list of (org_id, person_key) appointments touched — feed it to reconcile_roster."""
    o = roster["org"]
    gsa_id = ensure_org(conn, o["slug"], o["name"], parent_slug=o.get("parent"), type="custom")
    touched = _project_people(conn, gsa_id, o["slug"], roster.get("people", []))
    for rgo in roster.get("rgos", []):
        rid = ensure_org(conn, rgo["slug"], rgo["name"], parent_slug=o["slug"], type="unit")
        touched += _project_people(conn, rid, rgo["slug"], rgo.get("people", []))
    sync_org_nodes(conn)
    return touched


def reconcile_roster(conn: sqlite3.Connection, present: list[tuple[int, str]]) -> int:
    """Deactivate dashboard officer/deprep appointments that are no longer in the roster —
    scoped to the orgs the roster touched, so unrelated people are never affected. Returns
    the number of appointments retired. Mirrors the crawler's section-scoped M3 sweep."""
    present_set = set(present)
    org_ids = {oid for oid, _ in present}
    retired = 0
    for org_id in org_ids:
        rows = conn.execute(
            "SELECT e.id, p.key FROM edges e "
            "JOIN nodes p ON p.id=e.src_id JOIN nodes o ON o.id=e.dst_id "
            "WHERE e.type='has_role' AND e.is_active=1 AND e.source='dashboard' "
            "AND e.category IN ('officer','deprep') "
            "AND json_extract(o.attrs,'$.org_id')=?", (org_id,)).fetchall()
        for eid, pkey in rows:
            if (org_id, pkey) not in present_set:
                conn.execute("UPDATE edges SET is_active=0, updated_at=datetime('now') "
                             "WHERE id=?", (eid,))
                retired += 1
    return retired
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest v2/tests/test_roster.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/roster.py v2/tests/test_roster.py
git commit -m "feat(ingestion): roster->KG projection + reconcile (manual GSA people path)"
```

---

## Task 5: doc→KB ingest (`chunk_doc` + `upsert_doc_items`)

**Files:**
- Create: `v2/core/ingestion/gsa_docs.py`
- Test: `v2/tests/test_gsa_docs.py`

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_gsa_docs.py
from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import create_all
from v2.core.ingestion.gsa_docs import chunk_doc, upsert_doc_items


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(2,1,'Graduate Student Association','gsa','custom')")
    c.commit()
    yield c
    c.close()


def test_chunk_doc_splits_long_text():
    chunks = chunk_doc("word " * 2000)        # ~2000 tokens -> multiple <=350-token chunks
    assert len(chunks) >= 4
    assert all(c.strip() for c in chunks)


def test_upsert_doc_items_inserts_and_is_idempotent(conn):
    text = "GSA Travel Awards support graduate students presenting at conferences. " * 30
    n1 = upsert_doc_items(conn, org_id=2, slug="travel-award", title="GSA Travel Awards",
                          text=text, source_url="https://www.gsanjit.com/travel",
                          doc_type="policy")
    assert n1 >= 1
    active = conn.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND "
        "json_extract(metadata,'$.entity_id')='gsa-doc/travel-award'").fetchone()[0]
    assert active == n1
    # re-ingest: prior chunks retired, new ones active — no growth in active count
    n2 = upsert_doc_items(conn, org_id=2, slug="travel-award", title="GSA Travel Awards",
                          text=text, source_url="https://www.gsanjit.com/travel",
                          doc_type="policy")
    active2 = conn.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND "
        "json_extract(metadata,'$.entity_id')='gsa-doc/travel-award'").fetchone()[0]
    assert active2 == n2 and active2 == n1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest v2/tests/test_gsa_docs.py -q`
Expected: FAIL — `ModuleNotFoundError: v2.core.ingestion.gsa_docs`.

- [ ] **Step 3: Implement `gsa_docs.py`**

```python
# v2/core/ingestion/gsa_docs.py
"""Turn a GSA prose doc (constitution, bylaws, travel-award info, …) into chunked
knowledge_items for the KB. Pure (text in, rows written). Chunking reuses the running
bot's tiktoken chunker so v1 and v2 chunk identically. source/created_by='dashboard'."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from bot.services.chunker import DocumentChunker

_CHUNKER = DocumentChunker(Path(__file__).resolve().parents[2] / "bot" / "data")


def chunk_doc(text: str) -> list[str]:
    """Split prose into <=350-token chunks (sentence-aware), via the shared chunker."""
    return [c for c in _CHUNKER.split_text_by_tokens(text) if c.strip()]


def upsert_doc_items(conn: sqlite3.Connection, *, org_id: int, slug: str, title: str,
                     text: str, source_url: str | None, doc_type: str = "policy") -> int:
    """(Re)ingest one doc: retire any prior active chunks for this doc slug, insert the new
    chunks as knowledge_items (one per chunk, shared metadata.entity_id='gsa-doc/<slug>' so
    the retriever groups them), created_by='dashboard'. Returns the chunk count. The caller
    embeds afterwards via v2/scripts/embed_all.py (resumable). NOT committed here — the CLI
    wrapper owns the transaction + backup."""
    entity_id = f"gsa-doc/{slug}"
    conn.execute(
        "UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
        "WHERE is_active=1 AND json_extract(metadata,'$.entity_id')=?", (entity_id,))
    chunks = chunk_doc(text)
    for i, chunk in enumerate(chunks):
        meta = json.dumps({"entity_id": entity_id, "verified": True,
                           "natural_key": f"{entity_id}:{doc_type}:{i}"})
        # search_text is a GENERATED column (title || ' ' || content) — never insert it.
        cur = conn.execute(
            "INSERT INTO knowledge_items(org_id,type,title,content,metadata,version,"
            "source_url,is_active,created_by) VALUES(?,?,?,?,?,1,?,1,'dashboard')",
            (org_id, doc_type, title, chunk, meta, source_url))
        conn.execute("UPDATE knowledge_items SET root_id=? WHERE id=?",
                     (cur.lastrowid, cur.lastrowid))
    return len(chunks)
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest v2/tests/test_gsa_docs.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/gsa_docs.py v2/tests/test_gsa_docs.py
git commit -m "feat(ingestion): GSA doc->KB chunking + idempotent upsert"
```

---

## Task 6: gated CLI wrappers (people + docs) and the seed data

**Files:**
- Create: `scripts/gsa_ingest_people.py`, `scripts/gsa_ingest_docs.py`
- Create: `bot/data/gsa_people.yml`, `bot/data/sources/gsa/` (with at least one real doc)

- [ ] **Step 1: Create the seed roster `bot/data/gsa_people.yml`**

Hand-fill from `bot/data/contacts.yml` + the site (this file is the manual source of truth):

```yaml
org: {slug: gsa, name: "Graduate Student Association", parent: njit}
people:
  - {name: "Fernando Vera Buschmann", title: "GSA President", category: officer, email: gsa-pres@njit.edu, note: "Data Science PhD"}
  - {name: "Mohammad Dindoost", title: "VP Academic Affairs", category: officer, email: gsa-vpa@njit.edu, note: "Computer Science PhD; Chair of 3MRP and Graduate Research Day"}
  - {name: "Mohith Oduru", title: "VP Finances", category: officer, email: gsa-vpf@njit.edu, note: "Information Systems PhD"}
  - {name: "Durvish Paliwal", title: "VP Programming", category: officer, email: dp2225@njit.edu, note: "Data Science MS"}
  - {name: "Nistha Hiteshkumar Chauhan", title: "VP Communications", category: officer, email: nhc27@njit.edu}
  - {name: "Ritwik Reddy Kolan", title: "VP Public Relations", category: officer, email: rk982@njit.edu}
rgos: []   # add Recognized Graduate Organizations here as they are confirmed
```

- [ ] **Step 2: Create at least one prose source doc**

Create `bot/data/sources/gsa/about-gsa.md` (real content; expand from the site/PDFs):

```markdown
# About the Graduate Student Association

The Graduate Student Association (GSA) is the official governing body representing
graduate students at the New Jersey Institute of Technology (NJIT). GSA advocates for
graduate student interests, organizes academic and social events, manages graduate
student activity funds, and runs programs such as the Three Minute Research Presentations
(3MRP) and Graduate Research Day.

The General Assembly is GSA's legislative body, composed of Department Representatives and
the Executive Board. Recognized Graduate Organizations (RGOs) are graduate student clubs
operating under GSA recognition; they may apply for funding and run their own events.
```

- [ ] **Step 3: Implement `scripts/gsa_ingest_people.py`**

```python
#!/usr/bin/env python
"""Ingest the GSA officer/RGO roster (bot/data/gsa_people.yml) into the graph.
Dry-run by default; --commit takes a hardened backup first. Idempotent + reconciling
(officers no longer in the file are retired). Run embeds are not needed (people are graph
nodes, not KB items)."""
from __future__ import annotations
import argparse, sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
import yaml
from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection
from v2.core.ingestion.roster import project_roster, reconcile_roster


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--roster", default=str(REPO / "bot" / "data" / "gsa_people.yml"))
    ap.add_argument("--commit", action="store_true", help="write (hardened backup first)")
    args = ap.parse_args(argv)

    roster = yaml.safe_load(Path(args.roster).read_text(encoding="utf-8"))
    n_people = len(roster.get("people", [])) + sum(len(r.get("people", [])) for r in roster.get("rgos", []))
    print(f"roster: {n_people} people, {len(roster.get('rgos', []))} RGO(s)")
    if not args.commit:
        print("(dry run — pass --commit to write; a hardened backup is taken first)")
        return 0

    bkp = hardened_backup(args.db, "pre-gsa-people")
    print(f"backup: {bkp.name}")
    conn = get_connection(args.db)
    with conn:
        present = project_roster(conn, roster)
        retired = reconcile_roster(conn, present)
    print(f"committed: {len(present)} appointment(s) projected, {retired} retired.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Implement `scripts/gsa_ingest_docs.py`**

```python
#!/usr/bin/env python
"""Ingest GSA prose docs (bot/data/sources/gsa/*.md|*.txt) into the KB as chunked
knowledge_items. Dry-run by default; --commit takes a hardened backup, then reminds you to
embed. doc slug = filename stem; title = first markdown H1 or the stem."""
from __future__ import annotations
import argparse, sys, re
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection
from v2.core.ingestion.gsa_docs import upsert_doc_items
from v2.core.retrieval.skills import resolve_org

SRC = REPO / "bot" / "data" / "sources" / "gsa"


def _title(text: str, stem: str) -> str:
    m = re.search(r"^#\s+(.+)$", text, re.M)
    return m.group(1).strip() if m else stem.replace("-", " ").title()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args(argv)

    docs = sorted([*SRC.glob("*.md"), *SRC.glob("*.txt")])
    print(f"found {len(docs)} GSA source doc(s) in {SRC}")
    if not docs:
        return 0
    if not args.commit:
        for d in docs:
            print("  would ingest:", d.name)
        print("(dry run — pass --commit to write; a hardened backup is taken first)")
        return 0

    bkp = hardened_backup(args.db, "pre-gsa-docs")
    print(f"backup: {bkp.name}")
    conn = get_connection(args.db)
    gsa = resolve_org(conn, "gsa")
    if gsa is None:
        sys.exit("no GSA org — run gsa_ingest_people.py --commit first")
    total = 0
    with conn:
        for d in docs:
            text = d.read_text(encoding="utf-8")
            total += upsert_doc_items(conn, org_id=gsa, slug=d.stem, title=_title(text, d.stem),
                                      text=text, source_url=None, doc_type="policy")
    print(f"committed: {total} chunk(s) across {len(docs)} doc(s).")
    print("next: python v2/scripts/embed_all.py   # embed the new GSA chunks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Smoke-test both CLIs in dry-run (no DB writes)**

Run:
```bash
source venv/bin/activate
python scripts/gsa_ingest_people.py
python scripts/gsa_ingest_docs.py
```
Expected: each prints its plan and "(dry run …)" with exit 0; no DB change.

- [ ] **Step 6: Commit**

```bash
git add scripts/gsa_ingest_people.py scripts/gsa_ingest_docs.py bot/data/gsa_people.yml bot/data/sources/gsa/about-gsa.md
git commit -m "feat(gsa): gated CLIs + seed roster/doc for the manual KG+KB path"
```

---

## Task 7: Retire the GSA QA items (coverage checklist)

**Files:**
- Create: `scripts/gsa_retire_qa.py`
- Test: `v2/tests/test_gsa_retire_qa.py`

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_gsa_retire_qa.py
from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import create_all
from scripts.gsa_retire_qa import retire_gsa_qa


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(2,1,'GSA','gsa','custom')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(3,1,'MMI','mmi','custom')")
    for t in ("faq", "faq"):
        c.execute("INSERT INTO knowledge_items(org_id,type,title,content) VALUES(2,?, 'q','a')", (t,))
    c.execute("INSERT INTO knowledge_items(org_id,type,title,content) VALUES(3,'faq','m','a')")  # MMI
    c.execute("INSERT INTO knowledge_items(org_id,type,title,content) VALUES(2,'policy','p','a')")  # keep
    c.commit()
    yield c
    c.close()


def test_retire_gsa_qa_only_touches_gsa_faq(conn):
    n = retire_gsa_qa(conn)
    assert n == 2
    assert conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE org_id=2 AND type='faq' "
                        "AND is_active=1").fetchone()[0] == 0
    # MMI faq + GSA policy untouched
    assert conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE org_id=3 AND type='faq' "
                        "AND is_active=1").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE org_id=2 AND type='policy' "
                        "AND is_active=1").fetchone()[0] == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest v2/tests/test_gsa_retire_qa.py -q`
Expected: FAIL — `ModuleNotFoundError: scripts.gsa_retire_qa`.

- [ ] **Step 3: Implement `scripts/gsa_retire_qa.py`**

```python
#!/usr/bin/env python
"""Retire the legacy GSA Q&A (type='faq' under the GSA org) once the KG+KB replaces it.
Items are deactivated (is_active=0), kept for history. Dry-run by default; --commit takes a
hardened backup. Prints the retired titles as a coverage checklist to verify the new KB
answers each."""
from __future__ import annotations
import argparse, sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
import sqlite3
from v2.core.database.schema import get_connection
from v2.core.retrieval.skills import resolve_org


def retire_gsa_qa(conn: sqlite3.Connection) -> int:
    """Deactivate active GSA faq items; return the count. NOT committed here."""
    gsa = resolve_org(conn, "gsa")
    if gsa is None:
        return 0
    rows = conn.execute("SELECT id FROM knowledge_items WHERE org_id=? AND type='faq' "
                        "AND is_active=1", (gsa,)).fetchall()
    conn.executemany("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
                     "WHERE id=?", [(r[0],) for r in rows])
    return len(rows)


def main(argv=None) -> int:
    from scripts._area_tag_migrate import hardened_backup
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args(argv)
    conn = get_connection(args.db)
    gsa = resolve_org(conn, "gsa")
    titles = [r[0] for r in conn.execute(
        "SELECT title FROM knowledge_items WHERE org_id=? AND type='faq' AND is_active=1 "
        "ORDER BY title", (gsa,))] if gsa else []
    print(f"GSA QA items to retire: {len(titles)} (coverage checklist)")
    for t in titles:
        print("  -", t)
    if not args.commit:
        print("(dry run — pass --commit to write; a hardened backup is taken first)")
        return 0
    bkp = hardened_backup(args.db, "pre-gsa-qa-retire")
    print(f"backup: {bkp.name}")
    with conn:
        n = retire_gsa_qa(conn)
    print(f"committed: retired {n} GSA QA item(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest v2/tests/test_gsa_retire_qa.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/gsa_retire_qa.py v2/tests/test_gsa_retire_qa.py
git commit -m "feat(gsa): gated retirement of legacy GSA QA (with coverage checklist)"
```

---

## Task 8: GSA alignment check (`verify_gsa`)

**Files:**
- Modify: `scripts/verify_kg.py`
- Test: `v2/tests/test_verify_gsa.py`

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_verify_gsa.py
from __future__ import annotations
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import create_all
from v2.core.ingestion.roster import project_roster
from scripts.verify_kg import verify_gsa


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.commit()
    yield c
    c.close()


def test_verify_gsa_flags_missing_officers_and_leftover_qa(conn):
    # no officers yet, and an active GSA faq item present -> two problems
    from v2.core.graph.orgs import ensure_org
    gid = ensure_org(conn, "gsa", "GSA", parent_slug="njit", type="custom")
    conn.execute("INSERT INTO knowledge_items(org_id,type,title,content) VALUES(?,'faq','q','a')", (gid,))
    conn.commit()
    issues = verify_gsa(conn)
    assert any("no GSA officers" in i for i in issues)
    assert any("active GSA QA" in i for i in issues)


def test_verify_gsa_passes_after_seed(conn):
    project_roster(conn, {"org": {"slug": "gsa", "name": "GSA", "parent": "njit"},
                          "people": [{"name": "Fernando", "title": "President", "category": "officer"}]})
    conn.commit()
    assert verify_gsa(conn) == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest v2/tests/test_verify_gsa.py -q`
Expected: FAIL — `ImportError: cannot import name 'verify_gsa'`.

- [ ] **Step 3: Implement `verify_gsa` in `scripts/verify_kg.py`**

Add after `verify_kg`:

```python
def verify_gsa(conn: sqlite3.Connection) -> list[str]:
    """GSA-specific alignment: the GSA org has at least one officer, and no legacy QA
    (type='faq') remains active under GSA. Empty list = aligned."""
    issues: list[str] = []
    g = conn.execute("SELECT id FROM organizations WHERE slug='gsa' AND is_active=1").fetchone()
    if not g:
        return ["no GSA org found"]
    gid = g[0]
    officers = conn.execute(
        "SELECT COUNT(*) FROM edges e JOIN nodes o ON o.id=e.dst_id "
        "WHERE e.type='has_role' AND e.is_active=1 AND e.category IN ('officer','deprep') "
        "AND json_extract(o.attrs,'$.org_id')=?", (gid,)).fetchone()[0]
    if officers == 0:
        issues.append("no GSA officers in the graph (roster not ingested?)")
    leftover = conn.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE org_id=? AND type='faq' AND is_active=1",
        (gid,)).fetchone()[0]
    if leftover:
        issues.append(f"{leftover} active GSA QA item(s) remain (not retired)")
    return issues
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest v2/tests/test_verify_gsa.py v2/tests/test_verify_kg.py -q`
Expected: PASS (new + existing verify tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/verify_kg.py v2/tests/test_verify_gsa.py
git commit -m "feat(gsa): verify_gsa alignment check (officers present, QA retired)"
```

---

## Task 9: Truthful capability doc replaces `bot_features.md`

**Files:**
- Modify (rewrite): `bot/data/bot_features.md`

- [ ] **Step 1: Rewrite `bot/data/bot_features.md`**

Replace the entire file with interface-agnostic capability prose (no slash-command syntax except `/qrcode`):

```markdown
# What the GSA Gateway bot can help with

GSA Gateway is the Graduate Student Association's assistant. You can ask it questions in
plain language and it answers from official GSA information — it does not make things up.

You can ask it to:
- find a GSA officer or what a role does ("who is the VP of Finance?", "who runs
  programming?");
- explain GSA programs and events (3MRP, Graduate Research Day, the MMI Workshop, Friday
  Happy Hours);
- walk you through how to do things — apply for a travel award, become a Department
  Representative, get funding for a Recognized Graduate Organization, submit an initiative,
  or send feedback;
- point you to GSA documents (Constitution & Bylaws, Club Finance Bylaws) and campus
  resources (advising, wellness, international student support).

QR codes: use the `/qrcode` command to generate a GSA-branded QR code from a link or text.

GSA Gateway is built and maintained by the GSA VP of Academic Affairs.
```

- [ ] **Step 2: Verify it has no stale command references**

Run: `grep -nE "/(ask|events|event|resources|initiative|feedback|contact|help)\b" bot/data/bot_features.md`
Expected: no output (only `/qrcode` may remain, which this grep doesn't match).

- [ ] **Step 3: Commit**

```bash
git add bot/data/bot_features.md
git commit -m "docs(gsa): rewrite bot_features as truthful, interface-agnostic capability doc"
```

---

## Task 10: Live seed + end-to-end verification (gated)

**Files:** none (operational; uses the CLIs from Tasks 6–8).

- [ ] **Step 1: Dry-run everything against the live DB**

```bash
source venv/bin/activate
python scripts/gsa_ingest_people.py
python scripts/gsa_ingest_docs.py
python scripts/gsa_retire_qa.py
```
Expected: each prints its plan + "(dry run …)"; no DB change.

- [ ] **Step 2: Commit the writes (each takes its own hardened backup)**

```bash
python scripts/gsa_ingest_people.py --commit
python scripts/gsa_ingest_docs.py  --commit
python scripts/gsa_retire_qa.py    --commit
python v2/scripts/embed_all.py            # embed the new GSA chunks (resumable)
```
Expected: people projected; doc chunks inserted; QA retired; embed coverage 100%.

- [ ] **Step 3: Run the alignment check**

```bash
python - <<'PY'
from v2.core.database.schema import get_connection
from scripts.verify_kg import verify_gsa
issues = verify_gsa(get_connection("gsa_gateway.db"))
print("✓ GSA aligned" if not issues else ("✗ " + "\n  ".join(issues)))
PY
```
Expected: `✓ GSA aligned`.

- [ ] **Step 4: End-to-end retrieval smoke test**

```bash
python - <<'PY'
from v2.core.database.schema import get_connection
from v2.core.retrieval.router import route
from v2.core.retrieval.skills import officers_in_org
conn = get_connection("gsa_gateway.db")
r = route(conn, "who are the GSA officers?")
print("route:", r and (r.skill, r.args))
print("officers:", officers_in_org(conn, r.args["org_id"]))
PY
```
Expected: routes to `officers_in_org` with the GSA org id; prints the 6 officers with titles.

- [ ] **Step 5: Final full test run + commit any roster/doc content tweaks**

```bash
python -m pytest v2/tests/ -q
```
Expected: all green. Commit any content edits made while verifying.

---

## Notes for the implementer

- **Always dry-run before `--commit`.** Every write CLI takes a `hardened_backup` (see `scripts/_area_tag_migrate.py`) on `--commit`. Never write to `gsa_gateway.db` without it.
- **Source tag discipline:** GSA people/docs are `source`/`created_by='dashboard'`. A future crawler (`source='crawler'`) and the crawler `--reset` (`DELETE … WHERE source='crawler'`) must never touch them.
- **Don't insert `search_text`** — it's a generated column (`title || ' ' || content`).
- **`use venv`**: `source venv/bin/activate` before running anything (sqlite-vec + tiktoken + yaml live there).
- **PDFs:** the doc ingest reads `.md`/`.txt` only (no PDF dependency, by design). Convert a source PDF to text/markdown first — copy-paste its text into a `.md`, or run a one-off converter — then drop it in `bot/data/sources/gsa/`. Keeps the ingest dependency-free per YAGNI.
- After Plan 1, **Task 1's finding decides Plan 2**: if CRAWL, write the Wix-adapter plan against the real internals; if MANUAL, Plan 1 is the whole thing and updates happen via these CLIs / the dashboard.
```
