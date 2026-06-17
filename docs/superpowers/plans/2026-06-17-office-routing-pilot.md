# Office-Routing Content Pilot (Category M) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Answer "which office handles X / who do I contact about Y" by surfacing the right NJIT office + contact, using the existing rerank stack — proven by a deterministic office-routing gate.

**Architecture:** ~10 office directory docs (one markdown each, `Handles:` line + contact) ingested via a new gated `scripts/ingest_offices.py` (one org per doc + retire the 3 legacy GSA-filed seed contacts) into the existing KB/KG; answered by the V2Retriever (hybrid + cross-encoder rerank); gated by a chunk-level office-routing test. Reuses `upsert_doc_items`, `ensure_org`, `hardened_backup`, the section chunker, and the rerank gate pattern — no bespoke mechanisms.

**Tech Stack:** Python 3.11, SQLite + sqlite-vec, the v2 ingestion/retrieval stack, pytest, WebFetch (for accurate contacts).

**Design spec:** `docs/superpowers/specs/2026-06-17-office-routing-pilot-design.md` (read it).
**Branch:** `feat/office-routing-pilot` (already created).

---

## File Structure

- Create `scripts/ingest_offices.py` — gated ingester (one org per office doc + legacy-seed retirement).
- Create `bot/data/sources/offices/<slug>.md` — one per office (~10).
- Create `v2/tests/test_ingest_offices.py` — unit test for the retirement + ingest logic.
- Create `v2/tests/office_gold.py` — frozen `{question → gold office token}` map.
- Create `v2/tests/test_office_routing_gold.py` — the deterministic acceptance gate.

---

## Task 1: The gated office ingester (built + tested before content)

**Files:**
- Create: `scripts/ingest_offices.py`
- Test: `v2/tests/test_ingest_offices.py`

- [ ] **Step 1: Write the failing test**

Create `v2/tests/test_ingest_offices.py`:

```python
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest
from v2.core.database.schema import create_all
from scripts.ingest_offices import ingest_one_office, LEGACY_SEED


@pytest.fixture()
def conn():
    c = create_all(":memory:")
    c.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'NJIT','njit','university')")
    c.execute("INSERT INTO organizations(id,parent_id,name,slug,type) "
              "VALUES(2,1,'Graduate Student Association','gsa','custom')")
    # a legacy GSA-filed seed contact for OGI (id 125 mirrors production)
    c.execute("INSERT INTO knowledge_items(id,org_id,type,title,content,is_active,created_by) "
              "VALUES(125,2,'contact','Office of Global Initiatives (OGI)','old seed',1,'migration')")
    c.commit()
    yield c
    c.close()


def test_ingest_creates_office_org_and_contact_doc(conn):
    n = ingest_one_office(conn, slug="bursar", name="Office of the Bursar",
                          parent="njit", title="Office of the Bursar",
                          source_url="https://www.njit.edu/bursar",
                          body="Handles: tuition, billing, payments.\n\nEmail: studentaccounts@njit.edu")
    assert n >= 1
    org = conn.execute("SELECT id,type FROM organizations WHERE slug='bursar'").fetchone()
    assert org is not None and org["type"] == "office"
    active = conn.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND "
        "json_extract(metadata,'$.doc_id')='gsa-doc/bursar'").fetchone()[0]
    assert active == n


def test_legacy_seed_is_retired_not_duplicated(conn):
    # OGI is in LEGACY_SEED (id 125) — ingesting it must deactivate the old GSA-filed contact.
    ingest_one_office(conn, slug="ogi", name="Office of Global Initiatives",
                      parent="njit", title="Office of Global Initiatives (OGI)",
                      source_url="https://www.njit.edu/global",
                      body="Handles: visa, I-20, CPT, OPT.\n\nEmail: ogi@njit.edu")
    old = conn.execute("SELECT is_active FROM knowledge_items WHERE id=125").fetchone()["is_active"]
    assert old == 0  # retired
    active_ogi = conn.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND type='contact' "
        "AND content LIKE '%visa%'").fetchone()[0]
    assert active_ogi >= 1  # the new one is active; no duplicate of the old
    assert LEGACY_SEED["ogi"] == 125
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest v2/tests/test_ingest_offices.py -q`
Expected: FAIL (`ModuleNotFoundError`/`ImportError`: `ingest_one_office`).

- [ ] **Step 3: Implement `scripts/ingest_offices.py`**

Create `scripts/ingest_offices.py`:

```python
#!/usr/bin/env python
"""Ingest the NJIT office directory (bot/data/sources/offices/<slug>.md) as contact-type KB
docs, one ORG per office (parent = njit). Mirrors ingest_office_docs.py's safety model but
each office is its own org. Retires the legacy GSA-filed seed contacts it replaces so they
don't duplicate. Dry-run by default; --commit takes a hardened backup, then reminds to embed.
source/created_by='dashboard'.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from scripts.ingest_office_docs import parse_front_matter
from v2.core.database.schema import get_connection
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.ingestion.gsa_docs import upsert_doc_items

SRC = REPO / "bot" / "data" / "sources" / "offices"

# slug -> (display name, parent slug, type). Add offices here as the pilot grows.
OFFICES: dict[str, tuple[str, str, str]] = {
    "graduate-admissions": ("Office of Graduate Admissions", "njit", "office"),
    "ogi": ("Office of Global Initiatives", "njit", "office"),
    "registrar": ("Office of the Registrar", "njit", "office"),
    "bursar": ("Office of the Bursar / Student Accounts", "njit", "office"),
    "graduate-studies": ("Graduate Studies", "njit", "office"),
    "career-development": ("Career Development Services", "njit", "office"),
    "dean-of-students": ("Dean of Students", "njit", "office"),
    "oars": ("Office of Accessibility Resources & Services", "njit", "office"),
    "counseling": ("Counseling Center (C-CAPS)", "njit", "office"),
    "ist": ("IST / Technology Support", "njit", "office"),
}

# Legacy GSA-filed seed contacts (created_by='migration', under org 2) that we replace.
# Retire these so the new office-filed doc doesn't duplicate them (senior review C1).
LEGACY_SEED: dict[str, int] = {"ogi": 125, "graduate-studies": 122, "counseling": 123}


def ingest_one_office(conn: sqlite3.Connection, *, slug: str, name: str, parent: str,
                      title: str, source_url: str | None, body: str) -> int:
    """Ensure the office org, retire any legacy seed for this slug, then upsert the office
    contact doc. Returns the chunk count. Caller owns the transaction (no commit here)."""
    org_id = ensure_org(conn, slug=slug, name=name, parent_slug=parent, type="office")
    legacy_id = LEGACY_SEED.get(slug)
    if legacy_id is not None:
        conn.execute(
            "UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
            "WHERE id=? AND type='contact' AND is_active=1", (legacy_id,))
    return upsert_doc_items(conn, org_id=org_id, slug=slug, title=title,
                            text=body, source_url=source_url, doc_type="contact")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args(argv)

    docs = sorted(SRC.glob("*.md")) if SRC.is_dir() else []
    print(f"found {len(docs)} office doc(s) in {SRC}")
    for d in docs:
        known = "ok" if d.stem in OFFICES else "UNKNOWN SLUG (add to OFFICES)"
        print(f"   {d.name}  [{known}]")
    if not docs:
        return 0
    if any(d.stem not in OFFICES for d in docs):
        sys.exit("some office docs have no OFFICES entry — add them before committing")
    if not args.commit:
        print("(dry run — pass --commit to write; a hardened backup is taken first)")
        return 0

    bkp = hardened_backup(args.db, "pre-offices")
    print(f"backup: {bkp.name}")
    conn = get_connection(args.db)
    total = 0
    with conn:
        for d in docs:
            name, parent, otype = OFFICES[d.stem]
            title, source_url, sub_body = parse_front_matter(d.read_text(encoding="utf-8"), d.stem)
            total += ingest_one_office(conn, slug=d.stem, name=name, parent=parent,
                                       title=title, source_url=source_url, body=sub_body)
        sync_org_nodes(conn)
    print(f"committed: {total} chunk(s) across {len(docs)} office(s).")
    print("next: python v2/scripts/embed_all.py   then   scripts/_prune_retired_kb_migrate.py --commit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest v2/tests/test_ingest_offices.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd /home/md724/gsa-gateway
git add scripts/ingest_offices.py v2/tests/test_ingest_offices.py
git commit -m "feat(offices): gated office-directory ingester (one org/doc + legacy-seed retirement)"
```

---

## Task 2: Draft the office directory content (then USER VERIFIES)

**Files:**
- Create: `bot/data/sources/offices/<slug>.md` (10 files)

- [ ] **Step 1: Draft each office doc**

For each slug in `OFFICES`, create `bot/data/sources/offices/<slug>.md` as a **single-section**
doc (front-matter `title` + `source_url`, then one H1 + body — no subheadings, so `Handles:` +
contact stay in one chunk). Fetch the live NJIT office page (WebFetch) to get the accurate
email / location / hours / URL. Template:

```markdown
---
title: "Office of the Bursar / Student Accounts"
source_url: "https://www.njit.edu/bursar"
---

# Office of the Bursar / Student Accounts

Handles: tuition bills, billing questions, payments, payment plans, refunds, financial holds,
late fees, and student account issues.

Contact: studentaccounts@njit.edu · Student Mall, room 100 · Mon–Fri 9 AM–5 PM ·
https://www.njit.edu/bursar
```

Draft all 10 (graduate-admissions, ogi, registrar, bursar, graduate-studies, career-development,
dean-of-students, oars, counseling, ist). For OGI/graduate-studies/counseling, reuse the
accurate parts of the existing seed contacts (id 125/122/123) and add the `Handles:` line.

- [ ] **Step 2: Note any NJIT people/offices found (entity capture)**

While fetching pages, if a page names a **director / specific contact person** worth capturing,
add their name + title + office to a short list at the end of this step's notes for the user to
verify in Step 3 (do NOT auto-insert — person-capture is verified, per spec N2). If a **new
office** (not in `OFFICES`) is referenced and relevant, add it to `OFFICES` + a doc.

- [ ] **Step 3: USER VERIFICATION CHECKPOINT (do not commit before this)**

Present the 10 drafted docs (handles + contacts) and any captured people to the user. Apply
their corrections. Only proceed once the user confirms the directory is accurate.

- [ ] **Step 4: Commit the verified content**

```bash
cd /home/md724/gsa-gateway
git add bot/data/sources/offices/
git commit -m "content(offices): NJIT office directory (verified) — handles + contacts"
```

---

## Task 3: Gated ingest + embed + prune

**Files:** none (runs the ingester)

- [ ] **Step 1: Dry-run**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python scripts/ingest_offices.py`
Expected: lists 10 office docs, each `[ok]`, "(dry run …)". If any `[UNKNOWN SLUG]`, add it to `OFFICES`.

- [ ] **Step 2: Commit to the DB (hardened backup first)**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python scripts/ingest_offices.py --commit`
Expected: "backup: …", "committed: N chunk(s) across 10 office(s)."

- [ ] **Step 3: Embed + prune retired seeds**

Run:
```bash
cd /home/md724/gsa-gateway
.venv/bin/python v2/scripts/embed_all.py 2>&1 | grep -iE "Successfully|Failed" | tail -2
.venv/bin/python scripts/_prune_retired_kb_migrate.py --commit 2>&1 | tail -2
```
Expected: new office chunks embedded (0 failed); the 3 retired legacy seeds pruned.

- [ ] **Step 4: Verify no duplicates + offices present**

Run:
```bash
cd /home/md724/gsa-gateway && .venv/bin/python - <<'EOF'
from v2.core.database.schema import get_connection
c = get_connection("gsa_gateway.db")
for slug in ("ogi","bursar","registrar"):
    n = c.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND "
                  "json_extract(metadata,'$.doc_id')=?", (f"gsa-doc/{slug}",)).fetchone()[0]
    print(slug, "active office chunks:", n)
dup = c.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND type='contact' "
                "AND title LIKE '%Global Initiatives%'").fetchone()[0]
print("active OGI contacts (must be 1):", dup)
EOF
```
Expected: each office has ≥1 active chunk; exactly **1** active OGI contact (no duplicate).

---

## Task 4: The office-routing acceptance gate

**Files:**
- Create: `v2/tests/office_gold.py`, `v2/tests/test_office_routing_gold.py`

- [ ] **Step 1: Write the gold map (incl. the adversarial overlap pairs)**

Create `v2/tests/office_gold.py` (each value = an **id-stable token** — office name or email —
that must appear in a retrieved chunk; queries are **student-phrased, not the doc's wording**):

```python
"""Frozen {question -> gold office token} for the office-routing gate. The token is a stable
string (office name fragment or email) that must appear in a top-2 reranked chunk."""

OFFICE_GOLD = {
    # core intents
    "which office handles graduate admission questions": "Graduate Admissions",
    "who do I contact about my international student visa": "Global Initiatives",
    "which office handles course registration problems": "Registrar",
    "who do I contact about my tuition bill": "Bursar",
    "which office reviews my thesis and dissertation": "Graduate Studies",
    "which office handles career fairs and internships": "Career Development",
    "who do I contact if I need disability accommodations": "Accessibility",
    "where do I get help with NJIT email wifi and Canvas": "Technology Support",
    # adversarial overlap pairs (senior review S2)
    "who do I talk to about my OPT job search": "Career Development",
    "who handles a registration hold on my account": "Registrar",
    "who do I contact about a billing hold": "Bursar",
    "I am in a mental health crisis right now who do I contact": "Counseling",
}

# A guard set: existing non-office answers must NOT regress.
GUARD = {
    "what is the maximum GSA travel award": "maximum of $900",
    "who are the GSA officers": "officer",
    "what cumulative GPA must a CS PhD student maintain": "3.5",
}
```

- [ ] **Step 2: Write the gate test**

Create `v2/tests/test_office_routing_gold.py`:

```python
import pytest
from v2.core.database.schema import get_connection
from v2.core.retrieval.retriever import V2Retriever
from v2.core.retrieval.embedder import Embedder
from v2.core.retrieval.reranker import CrossEncoderReranker
from v2.tests.office_gold import OFFICE_GOLD, GUARD

TOP = 2  # "which ONE office" is a router answer — gold must be rank 1 or 2


@pytest.fixture(scope="module")
def retr():
    conn = get_connection("gsa_gateway.db")
    return V2Retriever(conn, Embedder(), reranker=CrossEncoderReranker())


@pytest.mark.slow
@pytest.mark.parametrize("q,token", list(OFFICE_GOLD.items()))
def test_office_in_top2(retr, q, token):
    chunks = retr.retrieve(q, limit=TOP)
    assert any(token.lower() in (c.content or "").lower() for c in chunks), \
        f"{q!r} -> want {token!r} in top-{TOP}"


@pytest.mark.slow
@pytest.mark.parametrize("q,token", list(GUARD.items()))
def test_no_regression(retr, q, token):
    chunks = retr.retrieve(q, limit=5)
    assert any(token.lower() in (c.content or "").lower() for c in chunks), \
        f"regression: {q!r} -> want {token!r}"
```

- [ ] **Step 3: Run the gate**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest v2/tests/test_office_routing_gold.py -m slow 2>&1 | grep -E "passed|failed|want" | tail -20`
Expected: all pass. **If an overlap pair fails** (e.g. "OPT job search" → OGI instead of Career-Dev): first sharpen the two offices' `Handles:` lines to disambiguate (e.g. Career-Dev "OPT/CPT job search and employment", OGI "OPT/CPT work authorization and immigration paperwork"), re-ingest+embed, re-run. If still failing after content sharpening, add a ~20-line topic→office structured skill (mirror `officers_in_org` in `skills.py` + a route in `router.py`) — this is the spec's documented fallback. Do not proceed until green.

- [ ] **Step 4: Commit**

```bash
cd /home/md724/gsa-gateway
git add v2/tests/office_gold.py v2/tests/test_office_routing_gold.py
git commit -m "test(offices): deterministic office-routing gate (overlap pairs, rank<=2, guard set)"
```

---

## Task 5: Verified person/office capture (only if any were found)

**Files:** none (uses existing `people_editor` / `ensure_org`, gated)

- [ ] **Step 1: If the user verified any named people in Task 2 Step 3**, add each via the gated
path:
```bash
cd /home/md724/gsa-gateway && .venv/bin/python - <<'EOF'
import sys; from pathlib import Path; sys.path.insert(0, str(Path('.').resolve()))
from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection
from v2.core.graph.orgs import sync_org_nodes
from v2.core.ingestion.people_editor import add_or_edit_person
hardened_backup("gsa_gateway.db", "pre-office-people")
conn = get_connection("gsa_gateway.db")
with conn:
    # one call per VERIFIED person, e.g.:
    # add_or_edit_person(conn, name="<verified name>", org_slug="<office slug>",
    #                    category="officer", titles=["<title>"], email="<email>")
    sync_org_nodes(conn)
print("people captured + org nodes synced")
EOF
.venv/bin/python v2/scripts/embed_all.py 2>&1 | grep -iE "Successfully|Failed" | tail -1
```
(If no people were verified, skip this task — offices-only is a valid pilot outcome.)

- [ ] **Step 2: Verify** any captured person resolves: `who is <name>` / `who works at <office>`.

---

## Task 6: Finalize

- [ ] **Step 1: Full sweep**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest v2/tests/test_ingest_offices.py v2/tests/test_office_routing_gold.py -q 2>&1 | tail -3` (the gate is slow; include `-m slow` for it).
Expected: green.

- [ ] **Step 2: Mark spec implemented + record results**

In `docs/superpowers/specs/2026-06-17-office-routing-pilot-design.md`, set Status to `Implemented (2026-06-17)` and append the gate result (N/N office intents at rank ≤2, overlap pairs status, any structured-skill fallback used).

```bash
cd /home/md724/gsa-gateway
git add docs/superpowers/specs/2026-06-17-office-routing-pilot-design.md
git commit -m "docs: mark office-routing pilot implemented + record gate results"
```

- [ ] **Step 3: Report** the gate result + whether RAG-first held or a structured skill was needed, then proceed to finishing-a-development-branch (merge + restart per the user's call).
