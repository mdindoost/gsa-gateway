# Research-Area Facet (P2.5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the clean `research_areas` tags a structured, enumerable dimension — answer "what areas does CS cover?", "which areas have the most faculty?", and "who lists X as a research area?" — without changing the recall-oriented "who works on X" path.

**Architecture:** `decompose` stores the discrete area list in the `research_areas` item's `metadata.areas`. Three new SQL skills read it via `json_each` (plain sqlite3, no vec, no new table): `areas_in_org`, `area_counts`, `people_by_area_tag`. The deterministic router sends enumeration/aggregation phrasings to them; structured_answer formats them with a coverage-basis line. Existing data is populated by a surgical one-time backfill (lossless reconstruction from our own `"; "`-join); future ingests write it natively.

**Tech Stack:** Python 3.12, sqlite3 (`json_each`, `json_extract`), pytest. All under `v2/core/`.

**Spec:** `docs/superpowers/specs/2026-06-14-research-area-facet-design.md`

---

### Task 1: `decompose` writes `metadata.areas`

**Files:**
- Modify: `v2/core/ingestion/decompose.py:72-76`
- Test: `v2/tests/test_decompose.py`

- [ ] **Step 1: Write the failing test**

Add to `v2/tests/test_decompose.py`:

```python
def test_research_areas_item_carries_metadata_areas_list():
    # the discrete list (not just the "; "-joined content) is stored as structured data
    item = next(i for i in decompose(koutis()) if i.type == "research_areas")
    assert item.metadata["areas"] == ["spectral graph theory", "graph sparsification"]


def test_no_research_areas_means_no_areas_key():
    rec = EntityRecord(entity_id="e1", name="Jane Doe", org="CS", source_url="u",
                       research_areas=[])
    items = [i for i in decompose(rec) if i.type == "research_areas"]
    assert items == []  # no research_areas item at all, so no stray metadata
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/test_decompose.py::test_research_areas_item_carries_metadata_areas_list -v`
Expected: FAIL with `KeyError: 'areas'`

- [ ] **Step 3: Write minimal implementation**

In `v2/core/ingestion/decompose.py`, replace the research_areas block (lines 72-76):

```python
    if rec.research_areas:
        cleaned = [a.strip() for a in rec.research_areas if a.strip()]
        if cleaned:
            areas = "; ".join(cleaned)
            items.append(mk("research_areas", f"{rec.name} — Research areas",
                            f"Research areas of {subj}: {areas}", "main",
                            extra={"areas": cleaned}))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest v2/tests/test_decompose.py -v`
Expected: PASS (all, including the two new tests)

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/decompose.py v2/tests/test_decompose.py
git commit -m "feat(ingestion): store discrete area list in research_areas metadata.areas"
```

---

### Task 2: `areas_in_org` + `area_counts` skills

**Files:**
- Modify: `v2/core/retrieval/skills.py` (add after `count_people_by_research_area`, end of file)
- Test: `bot/tests/test_skills.py`

- [ ] **Step 1: Write the failing test**

Add to `bot/tests/test_skills.py` (imports first — add `area_counts`, `areas_in_org` to the existing `from v2.core.retrieval.skills import (...)` block), then append:

```python
def _add_areas(conn, org, eid, name, areas):
    """Insert a research_areas item carrying metadata.areas (the P2.5 facet)."""
    import json as _json
    conn.execute(
        "INSERT INTO knowledge_items(org_id,type,title,content,metadata,is_active) "
        "VALUES(?,?,?,?,?,1)",
        (org, "research_areas", f"{name} — Research areas",
         f"Research areas of {name}: " + "; ".join(areas),
         _json.dumps({"entity_id": eid, "areas": areas})))
    # a matching profile so _display_name resolves the person's name
    conn.execute(
        "INSERT INTO knowledge_items(org_id,type,title,content,metadata,is_active) "
        "VALUES(?,?,?,?,?,1)",
        (org, "profile", name, f"Profile: {name}", _json.dumps({"entity_id": eid})))
    conn.commit()


def test_areas_in_org_is_distinct_casefolded_and_org_scoped(conn):
    _add_areas(conn, 5, "p/a", "Prof A", ["Machine Learning", "Graph Theory"])
    _add_areas(conn, 5, "p/b", "Prof B", ["machine learning", "Databases"])
    _add_areas(conn, 6, "p/c", "Prof C", ["Robotics"])  # DS, excluded when scope=CS
    # scope CS (org 5): ML appears twice in different casing -> one canonical entry
    assert areas_in_org(conn, 5) == ["Databases", "Graph Theory", "Machine Learning"]
    # scope YWCC (org 4) includes the DS child
    assert "Robotics" in areas_in_org(conn, 4)


def test_area_counts_counts_distinct_entities_sorted_desc(conn):
    _add_areas(conn, 5, "p/a", "Prof A", ["Machine Learning", "Graph Theory"])
    _add_areas(conn, 5, "p/b", "Prof B", ["machine learning"])
    counts = area_counts(conn, 5)
    assert counts[0] == ("Machine Learning", 2)        # most faculty first
    assert ("Graph Theory", 1) in counts
    # same distinct areas as areas_in_org
    assert len(counts) == len(areas_in_org(conn, 5))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest bot/tests/test_skills.py::test_areas_in_org_is_distinct_casefolded_and_org_scoped -v`
Expected: FAIL with `ImportError: cannot import name 'areas_in_org'`

- [ ] **Step 3: Write minimal implementation**

Append to `v2/core/retrieval/skills.py`:

```python
from collections import Counter


def _area_rows(conn: sqlite3.Connection, org_id: int | None) -> list[tuple[str, str]]:
    """(area_value, entity_id) for every tag on active research_areas items, optionally
    scoped to an org subtree. Reads metadata.areas via json_each."""
    clause, params = "", []
    if org_id is not None:
        ids = sorted(org_descendants(conn, org_id))
        clause = " AND k.org_id IN (%s)" % ",".join("?" * len(ids))
        params = list(ids)
    q = ("SELECT je.value, json_extract(k.metadata,'$.entity_id') "
         "FROM knowledge_items k, json_each(k.metadata,'$.areas') je "
         "WHERE k.type='research_areas' AND k.is_active=1 "
         "AND json_extract(k.metadata,'$.entity_id') IS NOT NULL" + clause)
    out: list[tuple[str, str]] = []
    for val, eid in conn.execute(q, params):
        if val and val.strip() and eid:
            out.append((val.strip(), eid))
    return out


def _canonical(forms: list[str]) -> str:
    """Pick the display casing for a case-folded group: most frequent surface form,
    ties broken alphabetically (cosmetic only — never a wrong fact)."""
    counts = Counter(forms)
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def areas_in_org(conn: sqlite3.Connection, org_id: int) -> list[str]:
    """Distinct research areas across an org subtree, case-folded for grouping and shown
    in a canonical casing. The new enumerable facet ('what areas does CS cover?')."""
    groups: dict[str, list[str]] = {}
    for val, _eid in _area_rows(conn, org_id):
        groups.setdefault(val.casefold(), []).append(val)
    return sorted((_canonical(forms) for forms in groups.values()), key=str.casefold)


def area_counts(conn: sqlite3.Connection, org_id: int) -> list[tuple[str, int]]:
    """(canonical_area, distinct_faculty_count) across an org subtree, most faculty first."""
    forms: dict[str, list[str]] = {}
    ents: dict[str, set[str]] = {}
    for val, eid in _area_rows(conn, org_id):
        k = val.casefold()
        forms.setdefault(k, []).append(val)
        ents.setdefault(k, set()).add(eid)
    out = [(_canonical(forms[k]), len(ents[k])) for k in forms]
    return sorted(out, key=lambda t: (-t[1], t[0].casefold()))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest bot/tests/test_skills.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add v2/core/retrieval/skills.py bot/tests/test_skills.py
git commit -m "feat(retrieval): areas_in_org + area_counts facet skills"
```

---

### Task 3: `people_by_area_tag` skill (precise exact-tag match)

**Files:**
- Modify: `v2/core/retrieval/skills.py` (append after `area_counts`)
- Test: `bot/tests/test_skills.py`

- [ ] **Step 1: Write the failing test**

Add `people_by_area_tag` to the skills import block in `bot/tests/test_skills.py`, then append:

```python
def test_people_by_area_tag_casefold_and_expansion(conn):
    _add_areas(conn, 5, "p/a", "Prof A", ["Machine Learning"])
    _add_areas(conn, 5, "p/b", "Prof B", ["large language models"])
    # exact tag, case-insensitive
    assert _names(people_by_area_tag(conn, "machine learning", org_id=5)) == {"Prof A"}
    # P2 expansion: "ml" -> matches the "Machine Learning" tag
    assert _names(people_by_area_tag(conn, "ml", org_id=5)) == {"Prof A"}
    # "llm" expands to "large language models" -> matches Prof B
    assert _names(people_by_area_tag(conn, "llm", org_id=5)) == {"Prof B"}
    # unmapped, unlisted area -> empty (honest)
    assert people_by_area_tag(conn, "astrophysics", org_id=5) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest bot/tests/test_skills.py::test_people_by_area_tag_casefold_and_expansion -v`
Expected: FAIL with `ImportError: cannot import name 'people_by_area_tag'`

- [ ] **Step 3: Write minimal implementation**

Append to `v2/core/retrieval/skills.py`:

```python
def people_by_area_tag(conn: sqlite3.Connection, area: str,
                       org_id: int | None = None) -> list[tuple[str, str]]:
    """Faculty (name, entity_id) who LIST ``area`` as a research-area tag — exact
    (case-folded) match against metadata.areas, with P2 expansion so 'ml'/'llm' hit the
    canonical tags. Precise, lower-recall (only faculty who list discrete areas)."""
    targets = {p.casefold() for p in expand_area(area)}
    eids = {eid for val, eid in _area_rows(conn, org_id) if val.casefold() in targets}
    return sorted((_display_name(conn, e), e) for e in eids)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest bot/tests/test_skills.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add v2/core/retrieval/skills.py bot/tests/test_skills.py
git commit -m "feat(retrieval): people_by_area_tag precise exact-tag match"
```

---

### Task 4: Router — enumeration/aggregation + "who lists X as a research area"

**Files:**
- Modify: `v2/core/retrieval/router.py` (module-level regexes near `_AREA_TRIGGER`; new branches in `route`)
- Test: `v2/tests/test_router.py`

- [ ] **Step 1: Write the failing test**

Add to `v2/tests/test_router.py` (follow its existing fixture/import style; `route` and the org fixture already exist there):

```python
def test_routes_area_enumeration_to_areas_in_org(conn):
    r = route(conn, "what research areas does Computer Science cover?")
    assert r is not None and r.skill == "areas_in_org" and r.args["org_id"] == 5


def test_routes_area_ranking_to_area_counts(conn):
    r = route(conn, "which research areas have the most faculty in YWCC?")
    assert r is not None and r.skill == "area_counts" and r.args["org_id"] == 4


def test_routes_who_lists_to_people_by_area_tag(conn):
    r = route(conn, "who lists graph as a research area in CS?")
    assert r is not None and r.skill == "people_by_area_tag"
    assert r.args["area"] == "graph" and r.args["org_id"] == 5


def test_who_works_on_still_routes_to_recall_skill(conn):
    # unchanged: "who works on X" must NOT switch to the low-recall tag facet
    r = route(conn, "who works on graph in CS?")
    assert r is not None and r.skill == "people_by_research_area"
```

(Use whatever connection fixture `test_router.py` already defines; if it builds its own org tree, reuse it. The org ids 4/5 match the existing Phase-1 router tests.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/test_router.py::test_routes_area_enumeration_to_areas_in_org -v`
Expected: FAIL (returns None or a different skill)

- [ ] **Step 3: Write minimal implementation**

In `v2/core/retrieval/router.py`, add module-level regexes after `_AREA_TRIGGER` (line 27):

```python
# Enumeration of the research-area facet ("what research areas does CS cover").
_ENUM_AREAS = re.compile(
    r"\b(?:research areas?|areas? of research|"
    r"(?:what|which|list|all|show)\s+(?:research\s+)?areas?)\b")
# Ranking/aggregation cue ("which areas have the MOST faculty").
_RANK = re.compile(r"\b(?:most|top|popular|biggest|largest|ranked|by count|how many people)\b")
# "who LISTS X as a research area" -> precise tag match.
_LISTS_AREA = re.compile(r"who\s+lists?\s+(.+?)\s+as\s+(?:an?\s+)?research\s+area")
```

In `route`, replace the body from the `if "how many" in q and area:` line through `return None` with:

```python
    # precise "who lists X as a research area" (before the generic area branches)
    m = _LISTS_AREA.search(q)
    if m:
        tag = m.group(1).strip()
        if org_phrase and org_phrase in tag:
            tag = tag.split(org_phrase)[0].strip()
        tag = tag.strip(" .,?")
        if tag:
            return Route("people_by_area_tag", {"area": tag, "org_id": org_id})

    if "how many" in q and area:
        return Route("count_people_by_research_area", {"area": area, "org_id": org_id})
    if area:
        return Route("people_by_research_area", {"area": area, "org_id": org_id})

    # enumeration / aggregation over the area facet (org required)
    if org_id is not None and _ENUM_AREAS.search(q):
        if _RANK.search(q):
            return Route("area_counts", {"org_id": org_id})
        return Route("areas_in_org", {"org_id": org_id})

    if "department" in q and org_id is not None and "faculty" not in q and "professor" not in q:
        return Route("org_departments", {"org_id": org_id})
    if ("faculty" in q or "professor" in q) and org_id is not None:
        return Route("faculty_in_department", {"org_id": org_id})
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest v2/tests/test_router.py -v`
Expected: PASS (all, including the existing Phase-1 router tests — confirm none regressed)

- [ ] **Step 5: Commit**

```bash
git add v2/core/retrieval/router.py v2/tests/test_router.py
git commit -m "feat(retrieval): route area enumeration/aggregation + who-lists to facet skills"
```

---

### Task 5: structured_answer — run + format the three new shapes

**Files:**
- Modify: `v2/core/retrieval/structured_answer.py` (`run` dispatch + `format_answer`)
- Test: `bot/tests/test_structured_answer.py`

- [ ] **Step 1: Write the failing test**

Add to `bot/tests/test_structured_answer.py` (follow its existing pattern of building a `Route`, calling `run`, then `format_answer`):

```python
def test_format_areas_in_org():
    from v2.core.retrieval.structured_answer import format_answer
    out = format_answer({"skill": "areas_in_org", "org_name": "Computer Science",
                         "area": None, "rows": ["Databases", "Machine Learning"]})
    assert "Computer Science" in out and "Databases" in out and "Machine Learning" in out
    assert "list research areas" in out  # the coverage-basis phrasing

def test_format_area_counts():
    from v2.core.retrieval.structured_answer import format_answer
    out = format_answer({"skill": "area_counts", "org_name": "Computer Science",
                         "area": None, "rows": [("Machine Learning", 5), ("Databases", 2)]})
    assert "Machine Learning (5)" in out and "Databases (2)" in out

def test_format_people_by_area_tag_empty_is_honest():
    from v2.core.retrieval.structured_answer import format_answer
    out = format_answer({"skill": "people_by_area_tag", "org_name": "CS",
                         "area": "astrophysics", "rows": []})
    assert "couldn't find" in out and "astrophysics" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest bot/tests/test_structured_answer.py::test_format_areas_in_org -v`
Expected: FAIL (returns "" for the unknown skill)

- [ ] **Step 3: Write minimal implementation**

In `v2/core/retrieval/structured_answer.py`, add to `run`'s dispatch (before the `else`):

```python
    elif skill == "areas_in_org":
        rows = skills.areas_in_org(conn, a["org_id"])
    elif skill == "area_counts":
        rows = skills.area_counts(conn, a["org_id"])
    elif skill == "people_by_area_tag":
        rows = [n for n, _ in skills.people_by_area_tag(conn, a["area"], a.get("org_id"))]
```

In `format_answer`, add before the final `return ""`:

```python
    if skill == "areas_in_org":
        if not rows:
            return f"I don't have research areas listed for {org}."
        return (f"Across the faculty who list research areas{scope}, "
                f"{len(rows)} areas appear: {_join(rows)}.")

    if skill == "area_counts":
        if not rows:
            return f"I don't have research areas listed for {org}."
        ranked = "; ".join(f"{a} ({n})" for a, n in rows)
        return (f"Research areas{scope}, by number of faculty who list them: {ranked}.")

    if skill == "people_by_area_tag":
        if not rows:
            return f"I couldn't find anyone who lists \"{area}\" as a research area{scope}."
        return f"{len(rows)} faculty list \"{area}\" as a research area{scope}: {_join(rows)}."
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest bot/tests/test_structured_answer.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add v2/core/retrieval/structured_answer.py bot/tests/test_structured_answer.py
git commit -m "feat(retrieval): render area facet answers (enumeration/counts/tag-match)"
```

---

### Task 6: Backfill helper for existing `research_areas` items

**Files:**
- Create: `scripts/backfill_research_area_tags.py`
- Test: `v2/tests/test_backfill_area_tags.py`

The transform is lossless: `decompose` joins areas with `"; "` and `_split_areas` guarantees no area contains `,`/`;`, so splitting the content tail on `"; "` exactly recovers the list.

- [ ] **Step 1: Write the failing test**

Create `v2/tests/test_backfill_area_tags.py`:

```python
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.backfill_research_area_tags import area_tags_from_content


def test_recovers_list_from_joined_content():
    c = "Research areas of Vincent Oria (Computer Science): Multimedia Databases; Spatio-temporal Databases; Recommender Systems"
    assert area_tags_from_content(c) == [
        "Multimedia Databases", "Spatio-temporal Databases", "Recommender Systems"]


def test_single_area_content():
    c = "Research areas of X (CS): Algorithms"
    assert area_tags_from_content(c) == ["Algorithms"]


def test_no_colon_returns_empty():
    assert area_tags_from_content("garbage with no separator") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest v2/tests/test_backfill_area_tags.py -v`
Expected: FAIL with `ModuleNotFoundError` / import error

- [ ] **Step 3: Write minimal implementation**

Create `scripts/backfill_research_area_tags.py`:

```python
#!/usr/bin/env python
"""One-time backfill: populate metadata.areas on existing active research_areas items.

Lossless — decompose joins areas with "; " and areas never contain ',' or ';', so the
content tail recovers the exact list. DEFAULT IS A DRY RUN; --commit writes (auto-backup
first). Going forward decompose writes metadata.areas natively.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def area_tags_from_content(content: str) -> list[str]:
    """Recover the discrete area list from a 'Research areas of X: A; B; C' string."""
    if ": " not in content:
        return []
    tail = content.split(": ", 1)[1]
    return [a.strip() for a in tail.split("; ") if a.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true", help="write changes (else dry run)")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, content, metadata FROM knowledge_items "
        "WHERE type='research_areas' AND is_active=1").fetchall()

    planned = []
    for r in rows:
        meta = json.loads(r["metadata"])
        if meta.get("areas"):
            continue  # already has it (native decompose)
        tags = area_tags_from_content(r["content"])
        if tags:
            meta["areas"] = tags
            planned.append((r["id"], json.dumps(meta), tags))

    print(f"{len(rows)} active research_areas items; {len(planned)} need backfill.")
    for _id, _m, tags in planned[:5]:
        print(f"  id={_id}: {tags}")

    if not args.commit:
        print("DRY RUN — pass --commit to write.")
        return 0

    backup = REPO / ".backups" / f"gsa_gateway.{datetime.now():%Y%m%d-%H%M%S}.pre-areas-backfill.db"
    backup.parent.mkdir(exist_ok=True)
    shutil.copy2(args.db, backup)
    print(f"backup: {backup}")
    conn.executemany("UPDATE knowledge_items SET metadata=? WHERE id=?",
                     [(m, i) for i, m, _t in planned])
    conn.commit()
    print(f"backfilled {len(planned)} items.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest v2/tests/test_backfill_area_tags.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/backfill_research_area_tags.py v2/tests/test_backfill_area_tags.py
git commit -m "feat(ingestion): one-time backfill for research_areas metadata.areas"
```

---

### Task 7: Run the backfill, verify live, full suite

**Files:** none (operational; gated)

- [ ] **Step 1: Full suite green before touching data**

Run: `.venv/bin/python -m pytest bot/tests/ v2/tests/ -q`
Expected: only the known pre-existing failures (`test_local_server.py` CSRF, `test_ds_is_registered_but_flagged_js`); everything else PASS.

- [ ] **Step 2: Backfill dry run**

Run: `.venv/bin/python scripts/backfill_research_area_tags.py`
Expected: prints "~26 active research_areas items; ~26 need backfill" with sample tag lists. Writes nothing.

- [ ] **Step 3: Backfill commit (auto-backup)**

Run: `.venv/bin/python scripts/backfill_research_area_tags.py --commit`
Expected: prints a `.pre-areas-backfill.db` backup path, then "backfilled N items."

- [ ] **Step 4: Verify the facet on live data**

Run:
```bash
.venv/bin/python - <<'PY'
import sqlite3
from v2.core.retrieval import skills
c=sqlite3.connect("gsa_gateway.db")
cs=skills.resolve_org(c,"cs"); ywcc=skills.resolve_org(c,"ywcc")
print("CS areas:", skills.areas_in_org(c,cs)[:10], "...")
print("CS area_counts top5:", skills.area_counts(c,cs)[:5])
print("who lists machine learning (CS):", [n for n,_ in skills.people_by_area_tag(c,"ml",cs)])
PY
```
Expected: a clean distinct area list, sensible counts, and a non-empty ML list.

- [ ] **Step 5: Restart the bot to serve the facet**

Run: `bash scripts/restart.sh`
Expected: Discord + Telegram up, dashboard on :5555.

- [ ] **Step 6: No commit** (data + restart only; code already committed in Tasks 1-6).

---

## Notes / follow-ups (out of scope here)

- Future `Refresh NJIT KB` runs populate `metadata.areas` natively via decompose (Task 1); the backfill is one-time for existing rows.
- The overview-deactivation gotcha (a no-`--overview` re-ingest drops overviews) is unrelated to this plan and tracked separately; the backfill avoids re-ingest entirely, so it doesn't touch overviews.
