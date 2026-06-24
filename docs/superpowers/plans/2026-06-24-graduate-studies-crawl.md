# Graduate Studies Crawl — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Crawl `www.njit.edu/graduatestudies/` into the DB under the existing `graduate-studies` office — verbatim prose → KB (`type='policy'`) and the `contact.php` Personnel → KG staff — then clean-replace the old hodgepodge rows in a separate gated migration.

**Architecture:** A new `v2/core/ingestion/gradstudies_crawl.py` is a **copy of `eos_crawl.py`**, adapted: same path-prefix DFS (`_in_scope`), same verbatim prose + content-hash ingest, but a **new unit-header-aware roster parser** for `contact.php` (bare phones, accented/suffixed names, interleaved section headers) and a one-line change so the roster page's own office-contact prose is ALSO kept as KB. A gated CLI `scripts/crawl_gradstudies.py` (copy of `crawl_eos.py`) drives it. A separate gated migration `scripts/_gradstudies_cleanup_migrate.py` retires the stale/dup pre-existing rows. EOS/IST files are never modified → zero regression.

**Tech Stack:** Python 3.11, BeautifulSoup4, SQLite (+ sqlite-vec), pytest. Reuses `v2/core/ingestion/web_crawler.py` (fetch/clean/link), `v2/core/graph/orgs.py` (`ensure_org`/`sync_org_nodes`), `v2/core/graph/project.py` (`project_appointment`), `scripts/_area_tag_migrate.py` (`hardened_backup`).

## Global Constraints

- **Coverage rule — NO content exclusions.** Every in-scope `/graduatestudies/` page is captured verbatim. The path-prefix is the only boundary. No page is skipped for being "low value / a roster / a duplicate." (`feedback_crawler_complete_coverage`.)
- **Verbatim / mechanical-clean only.** Stored prose == `clean_text` of the page's main region. No summarizing/rewording/truncating. (CLAUDE.md hard line.)
- **Crawl brings data ONLY.** The crawler makes no serving/gating/staging/deletion decisions. Retiring old rows is the SEPARATE migration (Task 7), never the crawler.
- **Source-scoped.** Crawler writes/reads only `created_by='crawler'`. The 63 pre-existing rows (migration/dashboard/njit-crawl) are untouched by the crawl.
- **Anti-fabrication.** Capture only published contact (phone/email literally on the page); never invent. Unparseable roster rows → warn, never drop silently, never fabricate.
- **Reuse Org, don't recreate.** `ensure_org(slug='graduate-studies', name='Graduate Studies', parent_slug='njit', type='office')` resolves the EXISTING id 9. No new org / rename / alias (ND7).
- **PDF = capability gap.** PDF pages stored verbatim WITH their PDF link; PDF *bytes* flagged in the manifest, not extracted (ND5).
- **Graph-write helpers do NOT commit.** The CLI/migration owns the transaction.
- **Gated live writes.** `hardened_backup(...)` + dry-run default + `--commit`. Dev copy before live.
- **Embeddings.** After ingest, `python v2/scripts/embed_all.py <db>` (documents use `search_document:` prefix; handled inside embed_all). DB-only change → no bot restart.
- **Source tag:** crawler rows `created_by='crawler'`, `metadata.source='gradstudies_crawl'`.
- **Seed:** `https://www.njit.edu/graduatestudies/` (single entry point; canonical host — `graduatestudies.njit.edu` is dead).

**Setup (execution time, before Task 1):** create an isolated worktree on branch `feat/gradstudies-crawl` via the `superpowers:using-git-worktrees` skill. All paths below are relative to that worktree root.

---

### Task 1: Scaffold `gradstudies_crawl.py` from EOS + save real fixtures + path-scope test

Copy the proven EOS crawler, rename its identity constants, and lock in discovery against the REAL homepage fixture from the start (closing the gap the IST review caught late).

**Files:**
- Create: `v2/core/ingestion/gradstudies_crawl.py` (copy of `v2/core/ingestion/eos_crawl.py`, adapted)
- Create: `v2/tests/fixtures/gradstudies/home.html`, `contact.php.html`, `phd_credit.html`, `forms.html` (real saved pages)
- Create: `v2/tests/test_gradstudies_scope.py`

**Interfaces:**
- Produces: module `gradstudies_crawl` with `GRAD_SLUG="graduate-studies"`, `GRAD_NAME="Graduate Studies"`, and (inherited from EOS, unchanged) `_canon`, `_in_scope(seed_path, url_path)`, `crawl_entry(seed, fetch, max_depth=4, budget=300, stats=None)`, `extract_prose(url, html) -> ProsePage|None`, `classify_page(html) -> str`, dataclasses `StaffRecord(name,title,phone,email)`, `ProsePage`, `EntryResult`. (Roster + extract_entry + ingest are REPLACED in later tasks.)

- [ ] **Step 1: Save the real fixtures** (read-only fetches; verbatim HTML)

```bash
mkdir -p v2/tests/fixtures/gradstudies
python3 - <<'PY'
from v2.core.ingestion.web_crawler import make_fetcher
f = make_fetcher()
pages = {
  "home.html": "https://www.njit.edu/graduatestudies/",
  "contact.php.html": "https://www.njit.edu/graduatestudies/contact.php",
  "phd_credit.html": "https://www.njit.edu/graduatestudies/content/new-phd-credit-requirements",
  "forms.html": "https://www.njit.edu/graduatestudies/forms",
}
for name, url in pages.items():
    html = f(url)
    assert html, f"fetch failed: {url}"
    open(f"v2/tests/fixtures/gradstudies/{name}", "w", encoding="utf-8").write(html)
    print(name, len(html))
PY
```

- [ ] **Step 2: Copy the EOS crawler and rename its identity**

```bash
cp v2/core/ingestion/eos_crawl.py v2/core/ingestion/gradstudies_crawl.py
```
Then edit `v2/core/ingestion/gradstudies_crawl.py`:
- Module docstring line 1: `"""Graduate Studies (Office of Graduate Studies / GSO) crawler.` and update the `Spec:` line to `docs/superpowers/specs/2026-06-24-graduate-studies-crawl-design.md`.
- Replace the identity constants:
```python
GRAD_SLUG = "graduate-studies"
GRAD_NAME = "Graduate Studies"
```
(delete `EOS_SLUG`/`EOS_NAME`). Leave `_canon`, `_in_scope`, `crawl_entry`, `extract_prose`, `classify_page`, `_strip_recurring_assets`, `_url_rank`, the dataclasses, and `_main_region` UNCHANGED — the path-prefix scope and verbatim prose transfer as-is. (`parse_roster`, `extract_entry`, `ingest_eos` are rewritten in Tasks 2–4; for now they still reference `EOS_SLUG` inside `ingest_eos` — that function is replaced in Task 4, so to keep the module importable, temporarily rename `ingest_eos`→`ingest_gradstudies` and its two `EOS_SLUG` refs → `GRAD_SLUG`, `"eos_crawl"`→`"gradstudies_crawl"`, and `source_section="contacts"` stays.)

- [ ] **Step 3: Write the failing path-scope test (real homepage)**

```python
# v2/tests/test_gradstudies_scope.py
from pathlib import Path
from v2.core.ingestion import gradstudies_crawl as gc

FIX = Path(__file__).parent / "fixtures" / "gradstudies"
SEED = "https://www.njit.edu/graduatestudies/"


def test_in_scope_is_path_prefix_bound():
    sp = "/graduatestudies/"
    assert gc._in_scope(sp, "/graduatestudies/forms")
    assert gc._in_scope(sp, "/graduatestudies/content/new-phd-credit-requirements")
    assert not gc._in_scope(sp, "/parking/")          # off-path same host
    assert not gc._in_scope(sp, "/registrar/")


def test_real_homepage_reaches_every_key_section_and_stays_in_scope():
    home = (FIX / "home.html").read_text(encoding="utf-8")
    stub = '<html><body><div role="main"><h1>x</h1>body</div></body></html>'
    seen = []

    def fetch(u):
        seen.append(u)
        return home if u == SEED else stub

    list(gc.crawl_entry(SEED, fetch, max_depth=2, budget=400))
    for sec in ("/graduatestudies/forms",
                "/graduatestudies/current-students",
                "/graduatestudies/degree-programs",
                "/graduatestudies/graduate-faculty",
                "/graduatestudies/full-time-status-phd-students",
                "/graduatestudies/content/new-phd-credit-requirements"):
        assert f"https://www.njit.edu{sec}" in seen, f"homepage did not reach {sec}"
    assert all("/graduatestudies" in p for p in seen)   # never left the entry point
```

- [ ] **Step 4: Run — expect PASS** (scope + crawl_entry are inherited working code)

Run: `python3 -m pytest v2/tests/test_gradstudies_scope.py -q`
Expected: PASS (2 tests). If `/graduate-faculty` or any section is missing, the homepage nav changed — investigate before proceeding (do NOT loosen the assertion).

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/gradstudies_crawl.py v2/tests/fixtures/gradstudies v2/tests/test_gradstudies_scope.py
git commit -m "feat(gradstudies): copy EOS crawler + real fixtures; path-scope discovery verified on real homepage"
```

---

### Task 2: Unit-header-aware roster parser for `contact.php`

The GSO Personnel block differs from EOS: **bare phones** (`973-596-3462`, not `Phone#…`), accented/suffixed names, **multiple title lines**, and **interleaved section headers** ("Graduate Student Awards") that would corrupt an email-anchored split. Replace `parse_roster` with a phone-anchored, title-keyword-driven parser.

**Files:**
- Modify: `v2/core/ingestion/gradstudies_crawl.py` (replace `parse_roster`, the roster regex/anchor constants; extend `StaffRecord` with `unit`)
- Create: `v2/tests/test_gradstudies_roster.py`

**Interfaces:**
- Consumes: `clean_text(html)` output (string).
- Produces: `parse_roster(text: str) -> tuple[list[StaffRecord], list[str]]` returning `(records, warnings)`; `StaffRecord(name:str, title:str, phone:str, email:str, unit:str)`.

- [ ] **Step 1: Write the failing roster test (real fixture)**

```python
# v2/tests/test_gradstudies_roster.py
from pathlib import Path
from v2.core.ingestion.web_crawler import clean_text
from v2.core.ingestion import gradstudies_crawl as gc

FIX = Path(__file__).parent / "fixtures" / "gradstudies"


def test_parses_named_staff_with_inline_contact():
    text = clean_text((FIX / "contact.php.html").read_text(encoding="utf-8"))
    staff, warnings = gc.parse_roster(text)
    by = {s.name: s for s in staff}

    z = by["Sotirios G. Ziavras, D.Sc."]
    assert z.title == "Vice Provost for Graduate Studies and Dean of the Graduate Faculty"
    assert z.phone == "973-596-3462"
    assert z.email == "ziavras@njit.edu"
    assert z.unit == ""                                   # top group, no section header

    c = by["Cortney Wortman"]
    assert c.title == "Coordinator (Graduate Awards)"
    assert c.email == "wortman@njit.edu"
    assert c.unit == "Graduate Student Awards"            # section header captured, NOT the name

    assert "Graduate Student Awards" not in by            # header never became a person
    assert by["Clarisa González-Lenahan"].email == "clarisa.gonzalez-lenahan@njit.edu"  # accents kept


def test_non_roster_page_yields_nothing():
    text = clean_text((FIX / "phd_credit.html").read_text(encoding="utf-8"))
    staff, warnings = gc.parse_roster(text)
    assert staff == [] and warnings == []
```

- [ ] **Step 2: Run — expect FAIL**

Run: `python3 -m pytest v2/tests/test_gradstudies_roster.py -q`
Expected: FAIL (`parse_roster` returns EOS shape / `StaffRecord` has no `unit`).

- [ ] **Step 3: Replace the roster constants, `StaffRecord`, and `parse_roster`**

In `gradstudies_crawl.py`, replace the EOS roster constants (`_EMAIL`, `_PHONE`, `_ROSTER_ANCHORS`, `_BLOCK_END`) with:
```python
_EMAIL = re.compile(r"^[A-Za-z0-9._%+'-]+@njit\.edu$", re.I)
_PHONE = re.compile(r"\b(\d{3}-\d{3}-\d{4})\b")
_ROSTER_ANCHOR = "personnel"            # the GSO contact.php block header
_BLOCK_END = ("popular searches", "in this section", "appointments")
# Role words that mark a line as a TITLE (vs a name or a section header).
_TITLE_CUES = ("provost", "dean", "director", "coordinator", "manager", "assistant",
               "associate", "professor", "officer", "specialist", "administrator",
               "advisor", "vice president", "office", "chair", "analyst", "secretary")
```
Extend `StaffRecord`:
```python
@dataclass(frozen=True)
class StaffRecord:
    name: str
    title: str
    phone: str
    email: str
    unit: str = ""        # functional sub-section header on contact.php (e.g. "Graduate Student Awards")
```
Replace `parse_roster` with:
```python
def _is_title(line: str) -> bool:
    low = line.lower()
    return "(" in line or any(c in low for c in _TITLE_CUES)


def parse_roster(text: str) -> tuple[list[StaffRecord], list[str]]:
    """Parse the GSO contact.php 'Personnel' block. Each person renders as
        [section header?] / Name / Title(+more titles) / bare-phone / email
    Phone-anchored: a (bare-phone, email) adjacent pair marks a record tail; the lines
    above the phone are name + title(s), optionally preceded by a section header. Titles
    are detected by role cues so the name (no cue) and a leading section header (no cue,
    not a title) are told apart — the header becomes ``unit``, never the name. Anti-fab:
    a chunk that can't yield (name, >=1 title, email) is a WARNING, never dropped/invented.
    Returns ([], []) for any non-Personnel page (anchor absent) so it falls through to prose."""
    low = text.lower()
    i = low.find(_ROSTER_ANCHOR)
    if i == -1:
        return [], []
    block = text[i + len(_ROSTER_ANCHOR):]
    for marker in _BLOCK_END:
        j = block.lower().find(marker)
        if j != -1:
            block = block[:j]
    block = re.sub(r"\n+\s*@", "@", block)               # rejoin emails split before @
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]

    records: list[StaffRecord] = []
    warnings: list[str] = []
    seen: set[str] = set()
    chunk: list[str] = []          # lines accumulated since the previous record's email
    for ln in lines:
        if _EMAIL.match(ln):
            email = ln
            # phone = the last chunk line matching a bare phone
            phone, head = "", []
            for k in range(len(chunk) - 1, -1, -1):
                if _PHONE.search(chunk[k]) and not phone:
                    phone = _PHONE.search(chunk[k]).group(1)
                    head = chunk[:k]                     # everything above the phone
                    break
            if not head:
                warnings.append(f"no phone/name above email {email!r}")
                chunk = []
                continue
            # trailing title lines; the line just above them is the name; rest above = unit header
            t = len(head)
            while t > 0 and _is_title(head[t - 1]):
                t -= 1
            if t == 0 or t >= len(head):                 # no name, or no title
                warnings.append(f"unparseable record near {email!r}: {head!r}")
                chunk = []
                continue
            name = head[t - 1]
            unit = head[t - 2] if t - 2 >= 0 else ""     # nearest preceding section header
            title = head[t]                              # first title line
            if name not in seen:
                seen.add(name)
                records.append(StaffRecord(name=name, title=title, phone=phone,
                                           email=email, unit=unit))
            chunk = []
        else:
            chunk.append(ln)
    return records, warnings
```

- [ ] **Step 4: Run — expect PASS**

Run: `python3 -m pytest v2/tests/test_gradstudies_roster.py -q`
Expected: PASS (2 tests). If a real person is missed, inspect the printed `warnings` — fix the parser, do NOT hardcode the person.

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/gradstudies_crawl.py v2/tests/test_gradstudies_roster.py
git commit -m "feat(gradstudies): unit-header-aware contact.php roster parser (bare phones, multi-title, warnings)"
```

---

### Task 3: `extract_entry` — roster precedence keeps `contact.php` prose too

EOS drops a roster page's prose (roster-precedence). The coverage rule requires keeping `contact.php`'s office-contact prose (email/phone/hours/appointment steps) as KB. Adapt `extract_entry` to (a) use the `(staff, warnings)` tuple, (b) add `warnings` to `EntryResult`, and (c) when a page yields staff, STILL extract its prose.

**Files:**
- Modify: `v2/core/ingestion/gradstudies_crawl.py` (`EntryResult`, `extract_entry`)
- Create: `v2/tests/test_gradstudies_prose.py`

**Interfaces:**
- Consumes: `parse_roster -> (list[StaffRecord], list[str])`, `extract_prose`, `crawl_entry`.
- Produces: `EntryResult(seed, staff, prose, skipped, truncated=False, warnings=[])`; `extract_entry(seed, fetch, max_depth=4, budget=300) -> EntryResult`.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_gradstudies_prose.py
from pathlib import Path
from v2.core.ingestion import gradstudies_crawl as gc

FIX = Path(__file__).parent / "fixtures" / "gradstudies"


def test_contact_page_yields_both_staff_and_prose():
    contact = (FIX / "contact.php.html").read_text(encoding="utf-8")
    phd = (FIX / "phd_credit.html").read_text(encoding="utf-8")
    pages = {
        "https://www.njit.edu/graduatestudies/": contact,           # seed = contact (staff+prose)
        "https://www.njit.edu/graduatestudies/x": phd,              # a prose-only page
    }

    def fetch(u):
        return pages.get(u)

    # tiny self-contained site: seed links /x
    contact2 = contact.replace("</body>", '<a href="/graduatestudies/x">x</a></body>')
    pages["https://www.njit.edu/graduatestudies/"] = contact2
    res = gc.extract_entry("https://www.njit.edu/graduatestudies/", fetch, max_depth=2, budget=20)

    assert any("Ziavras" in s.name for s in res.staff)              # staff captured
    urls = {p.source_url for p in res.prose}
    assert "https://www.njit.edu/graduatestudies/" in urls         # contact prose ALSO kept
    assert any("graduatestudies@njit.edu" in p.content for p in res.prose)  # office email served


def test_verbatim_prose_unaltered():
    phd = (FIX / "phd_credit.html").read_text(encoding="utf-8")
    page = gc.extract_prose("https://www.njit.edu/graduatestudies/content/new-phd-credit-requirements", phd)
    from v2.core.ingestion.web_crawler import clean_text
    # content is exactly the mechanical clean of the main region — no rewriting
    assert page is not None and page.content and page.content in clean_text(str(
        __import__("bs4").BeautifulSoup(phd, "html.parser")))
```

- [ ] **Step 2: Run — expect FAIL**

Run: `python3 -m pytest v2/tests/test_gradstudies_prose.py -q`
Expected: FAIL (`extract_entry` still uses EOS `staff = parse_roster(...)` scalar + `continue` drops prose; `EntryResult` has no `warnings`).

- [ ] **Step 3: Adapt `EntryResult` and `extract_entry`**

Add `warnings` to `EntryResult`:
```python
from dataclasses import dataclass, field, replace
...
@dataclass
class EntryResult:
    seed: str
    staff: list[StaffRecord]
    prose: list[ProsePage]
    skipped: list[str]
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)
```
In `extract_entry`, replace the roster/prose branch so the roster page ALSO contributes prose (remove the `continue`):
```python
    seen_emails: set[str] = set()
    for url, html in crawl_entry(seed, fetch, max_depth=max_depth, budget=budget, stats=stats):
        staff, warns = parse_roster(clean_text(html))
        res.warnings.extend(warns)
        for s in staff:
            if s.email not in seen_emails:
                seen_emails.add(s.email)
                res.staff.append(s)
        # COVERAGE RULE: do NOT `continue` on a roster page — keep its prose too.
        page = extract_prose(url, html)
        if page is None:
            if not staff:
                res.skipped.append(url)
            continue
        h = hashlib.sha1(page.content.encode("utf-8")).hexdigest()
        if h not in by_hash:
            by_hash[h] = page
            order.append(h)
        elif _url_rank(page.source_url) < _url_rank(by_hash[h].source_url):
            by_hash[h] = page
```

- [ ] **Step 4: Run — expect PASS**

Run: `python3 -m pytest v2/tests/test_gradstudies_prose.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/gradstudies_crawl.py v2/tests/test_gradstudies_prose.py
git commit -m "feat(gradstudies): roster page keeps its office-contact prose too (coverage rule) + warnings on EntryResult"
```

---

### Task 4: `ingest_gradstudies` — reuse org 9, staff contact, prose policy, idempotent

Finalize the ingest: reuse the existing `graduate-studies` org, write staff with published contact, prose as `type='policy'`, idempotent on natural key + content-hash.

**Files:**
- Modify: `v2/core/ingestion/gradstudies_crawl.py` (`ingest_gradstudies`)
- Create: `v2/tests/test_gradstudies_ingest.py`

**Interfaces:**
- Consumes: `EntryResult`; `ensure_org`, `sync_org_nodes`, `project_appointment`, `_merge_person_attrs`, `_slug`.
- Produces: `ingest_gradstudies(conn, result, source="crawler") -> dict` with keys `org_id, staff, prose_inserted, prose_updated, prose_unchanged, skipped`.

- [ ] **Step 1: Write the failing test** (in-memory DB via the project schema)

```python
# v2/tests/test_gradstudies_ingest.py
import sqlite3
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from v2.core.ingestion import gradstudies_crawl as gc


def _db():
    conn = sqlite3.connect(":memory:")
    create_all(conn)
    ensure_org(conn, "njit", "NJIT", parent_slug=None, type="university")
    ensure_org(conn, "graduate-studies", "Graduate Studies", parent_slug="njit", type="office")
    return conn


def test_ingest_writes_staff_and_policy_prose_idempotently():
    conn = _db()
    res = gc.EntryResult(
        seed="https://www.njit.edu/graduatestudies/",
        staff=[gc.StaffRecord("Ester Flaim", "Assistant Director of Graduate Studies",
                              "973-596-8139", "ester.flaim@njit.edu", "")],
        prose=[gc.ProsePage("PhD Credit Requirements", "Verbatim policy text.",
                            "https://www.njit.edu/graduatestudies/content/new-phd-credit-requirements")],
        skipped=[])
    s1 = gc.ingest_gradstudies(conn, res); conn.commit()
    assert s1["staff"] == 1 and s1["prose_inserted"] == 1
    row = conn.execute("SELECT type, created_by FROM knowledge_items WHERE is_active=1").fetchone()
    assert row == ("policy", "crawler")
    # staff contact persisted
    import json
    attrs = json.loads(conn.execute(
        "SELECT attrs FROM nodes WHERE type='Person' AND name='Ester Flaim'").fetchone()[0])
    assert attrs["email"] == "ester.flaim@njit.edu" and attrs["phone"] == "973-596-8139"
    # re-ingest unchanged → no new row
    s2 = gc.ingest_gradstudies(conn, res); conn.commit()
    assert s2["prose_unchanged"] == 1 and s2["prose_inserted"] == 0
    assert conn.execute("SELECT count(*) FROM knowledge_items WHERE is_active=1").fetchone()[0] == 1
    # org reused (id 9-equivalent: exactly one graduate-studies org)
    assert conn.execute("SELECT count(*) FROM organizations WHERE slug='graduate-studies'").fetchone()[0] == 1
```

- [ ] **Step 2: Run — expect FAIL**

Run: `python3 -m pytest v2/tests/test_gradstudies_ingest.py -q`
Expected: FAIL (function name / EOS slug refs from the Task-1 temporary rename may differ; metadata source tag).

- [ ] **Step 3: Finalize `ingest_gradstudies`**

Ensure the function reads (identical to EOS `ingest_eos` except identity + source tag):
```python
def ingest_gradstudies(conn, result: EntryResult, source: str = "crawler") -> dict:
    """Write an EntryResult under the EXISTING 'graduate-studies' org (id 9, under njit):
      - staff -> Person + has_role(category='staff') + published contact attrs (KG)
      - prose -> knowledge_items type='policy' (served corpus), keyed by source_url,
        content-hash for recrawl diff, figures (incl. PDF links) in metadata.
    Recrawl is change-detection ONLY — removed pages/departed staff are NOT retired (ND6).
    Does NOT commit (caller owns the transaction)."""
    org_id = ensure_org(conn, GRAD_SLUG, GRAD_NAME, parent_slug="njit", type="office")
    sync_org_nodes(conn)
    for s in result.staff:
        key = f"{source}/{GRAD_SLUG}/{_slug(s.name)}"
        pid = project_appointment(
            conn, person_key=key, name=s.name, org_id=org_id, category="staff",
            titles=[s.title], source_section=(s.unit or "contacts"), source=source)
        _merge_person_attrs(conn, pid, {"email": s.email, "phone": s.phone})
    inserted = updated = unchanged = 0
    for p in result.prose:
        ch = hashlib.sha1(p.content.encode("utf-8")).hexdigest()
        meta = {"natural_key": p.source_url, "content_hash": ch,
                "images": [list(i) for i in p.images], "files": [list(f) for f in p.files],
                "source": "gradstudies_crawl"}
        row = conn.execute(
            "SELECT id, json_extract(metadata,'$.content_hash') FROM knowledge_items "
            "WHERE is_active=1 AND org_id=? AND json_extract(metadata,'$.natural_key')=? "
            "AND created_by=?", (org_id, p.source_url, source)).fetchone()
        if row and row[1] == ch:
            unchanged += 1
            continue
        if row:
            conn.execute("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') WHERE id=?",
                         (row[0],))
            updated += 1
        else:
            inserted += 1
        conn.execute(
            "INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,"
            "version,is_active,created_by) VALUES(?,?,?,?,?,?,1,1,?)",
            (org_id, "policy", p.title, p.content, json.dumps(meta), p.source_url, source))
    return {"org_id": org_id, "staff": len(result.staff), "prose_inserted": inserted,
            "prose_updated": updated, "prose_unchanged": unchanged, "skipped": len(result.skipped)}
```

- [ ] **Step 4: Run — expect PASS**

Run: `python3 -m pytest v2/tests/test_gradstudies_ingest.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/gradstudies_crawl.py v2/tests/test_gradstudies_ingest.py
git commit -m "feat(gradstudies): ingest_gradstudies — reuse org 9, staff contact, type=policy, idempotent recrawl"
```

---

### Task 5: Gated CLI `crawl_gradstudies.py` + manifest (PDF/unknown flags)

**Files:**
- Create: `scripts/crawl_gradstudies.py` (copy of `scripts/crawl_eos.py`, adapted)
- Create: `v2/tests/test_gradstudies_cli.py`

**Interfaces:**
- Consumes: `gradstudies_crawl.extract_entry`, `ingest_gradstudies`; `hardened_backup`, `get_connection`, `make_fetcher`.
- Produces: `main(argv=None) -> int`; `ENTRY_POINTS = ["https://www.njit.edu/graduatestudies/"]`.

- [ ] **Step 1: Write the failing CLI test** (dry-run writes nothing; manifest prints)

```python
# v2/tests/test_gradstudies_cli.py
import importlib, sys
from pathlib import Path


def test_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    mod = importlib.import_module("scripts.crawl_gradstudies")
    fix = Path(__file__).parent / "fixtures" / "gradstudies"
    home = (fix / "home.html").read_text(encoding="utf-8")
    contact = (fix / "contact.php.html").read_text(encoding="utf-8")

    def fake_fetcher():
        def fetch(u):
            if u.rstrip("/").endswith("graduatestudies"):
                return home
            if "contact.php" in u:
                return contact
            return '<html><body><div role="main"><h1>P</h1>policy text</div></body></html>'
        return fetch
    monkeypatch.setattr(mod, "make_fetcher", fake_fetcher)

    rc = mod.main(["--db", str(tmp_path / "none.db")])   # no --commit
    out = capsys.readouterr().out
    assert rc == 0
    assert "staff=" in out and "TOTAL" in out
    assert not (tmp_path / "none.db").exists()           # dry-run created no DB
```

- [ ] **Step 2: Run — expect FAIL** (module doesn't exist)

Run: `python3 -m pytest v2/tests/test_gradstudies_cli.py -q`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Create the CLI**

```bash
cp scripts/crawl_eos.py scripts/crawl_gradstudies.py
```
Edit `scripts/crawl_gradstudies.py`:
- Docstring: retitle to Graduate Studies; update the gated-workflow example commands to `crawl_gradstudies.py`; `Spec:` → the gradstudies spec.
- `from v2.core.ingestion import eos_crawl` → `from v2.core.ingestion import gradstudies_crawl`.
- Replace `ENTRY_POINTS`:
```python
# GSO is one office at one path-prefix; a single homepage seed walks the whole subtree.
ENTRY_POINTS = ["https://www.njit.edu/graduatestudies/"]
```
- In `main`, `res = eos_crawl.extract_entry(seed, fetch)` → `res = gradstudies_crawl.extract_entry(seed, fetch, budget=400)`.
- After the prose print loop, add the warnings + truncation surfacing:
```python
        for w in res.warnings:
            print(f"    ⚠ ROSTER WARNING: {w}")
```
  (the truncation `⚠ TRUNCATED` line is already printed from EOS).
- `hardened_backup(args.db, "pre-eos-crawl")` → `hardened_backup(args.db, "pre-gradstudies-crawl")`.
- `r = eos_crawl.ingest_eos(conn, res)` → `r = gradstudies_crawl.ingest_gradstudies(conn, res)`.
- Update the trailing print hints (`EOS`→`Graduate Studies`).

- [ ] **Step 4: Run — expect PASS**

Run: `python3 -m pytest v2/tests/test_gradstudies_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/crawl_gradstudies.py v2/tests/test_gradstudies_cli.py
git commit -m "feat(gradstudies): gated CLI crawl_gradstudies.py — homepage seed, budget 400, manifest warnings, dry-run default"
```

---

### Task 6: Classifier + budget-truncation tests, then EOS/IST regression (T4/T6/T8)

Round out the suite and prove the copy didn't disturb EOS or IST.

**Files:**
- Create: `v2/tests/test_gradstudies_classify.py`

- [ ] **Step 1: Write classifier + truncation tests**

```python
# v2/tests/test_gradstudies_classify.py
from pathlib import Path
from v2.core.ingestion import gradstudies_crawl as gc

FIX = Path(__file__).parent / "fixtures" / "gradstudies"


def test_classify():
    assert gc.classify_page((FIX / "contact.php.html").read_text(encoding="utf-8")) == "staff-roster"
    assert gc.classify_page((FIX / "phd_credit.html").read_text(encoding="utf-8")) == "prose"
    assert gc.classify_page("<html><body><div role='main'></div></body></html>") == "skip-empty"


def test_budget_truncation_flag():
    # a self-linking page chain longer than the budget sets truncated=True
    def fetch(u):
        n = u.rstrip("/").rsplit("/", 1)[-1]
        nxt = f"/graduatestudies/{int(n)+1}" if n.isdigit() else "/graduatestudies/1"
        return f'<html><body><div role="main"><h1>{n}</h1>x</div><a href="{nxt}">n</a></body></html>'
    stats = {}
    list(gc.crawl_entry("https://www.njit.edu/graduatestudies/0", fetch, max_depth=99, budget=5, stats=stats))
    assert stats["truncated"] is True
```

- [ ] **Step 2: Run — expect PASS** (both exercise inherited working code)

Run: `python3 -m pytest v2/tests/test_gradstudies_classify.py -q`
Expected: PASS.

- [ ] **Step 3: Full GSO suite + EOS/IST regression (T8)**

Run: `python3 -m pytest v2/tests/test_gradstudies_*.py v2/tests/test_eos_*.py v2/tests/test_ist_*.py -q`
Expected: ALL PASS (GSO ~9 + EOS 35 + IST 13). If any EOS/IST test changed behavior, you modified a shared file — revert that; the GSO crawler must be additive only.

- [ ] **Step 4: Commit**

```bash
git add v2/tests/test_gradstudies_classify.py
git commit -m "test(gradstudies): classifier + budget truncation; EOS/IST regression green"
```

---

### Task 7: Clean-replace migration (SEPARATE gated script — run AFTER the live crawl verifies)

Retire the stale `migration` (id 131, dead subdomain stub) + `njit-crawl` (~57) GSO rows and dedup the 4 `dashboard` Ph.D. rows, KEEPING any genuinely manual `dashboard` row not present on the live site. This is a curation decision — it lives OUTSIDE the crawler, runs only after the new crawler content is verified live, dry-run by default, hardened-backup gated.

**Files:**
- Create: `scripts/_gradstudies_cleanup_migrate.py`
- Create: `v2/tests/test_gradstudies_cleanup.py`

**Interfaces:**
- Produces: `select_retire(conn) -> list[dict]` (rows to deactivate, each `{id, created_by, title, source_url, reason}`); `main(argv=None) -> int` (dry-run unless `--commit`; `hardened_backup` first).

- [ ] **Step 1: Write the failing selection test**

```python
# v2/tests/test_gradstudies_cleanup.py
import sqlite3, importlib, sys
from pathlib import Path
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org


def _seed():
    conn = sqlite3.connect(":memory:"); create_all(conn)
    ensure_org(conn, "njit", "NJIT", parent_slug=None, type="university")
    oid = ensure_org(conn, "graduate-studies", "Graduate Studies", parent_slug="njit", type="office")
    def ins(cb, title, url, src=None):
        meta = '{"source":"%s"}' % src if src else "{}"
        conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,"
                     "version,is_active,created_by) VALUES(?,?,?,?,?,?,1,1,?)",
                     (oid, "policy", title, "c", meta, url, cb))
    ins("migration", "stub", "https://graduatestudies.njit.edu")                 # retire: dead stub
    ins("njit-crawl", "OGS — Forms", "https://www.njit.edu/graduatestudies/forms")  # retire: superseded
    for _ in range(4):
        ins("dashboard", "Ph.D. Credit Requirements",
            "https://www.njit.edu/graduatestudies/content/new-phd-credit-requirements")  # dedup 3
    ins("dashboard", "Hand-written GSA note", "https://internal/manual-only")     # KEEP: not on site
    ins("crawler", "Forms", "https://www.njit.edu/graduatestudies/forms")         # KEEP: new source
    conn.commit(); return conn


def test_select_retire():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    mod = importlib.import_module("scripts._gradstudies_cleanup_migrate")
    conn = _seed()
    retire = mod.select_retire(conn)
    ids_reasons = {(r["created_by"], r["reason"]) for r in retire}
    assert ("migration", "dead-subdomain-stub") in ids_reasons
    assert ("njit-crawl", "superseded-by-crawler") in ids_reasons
    assert sum(1 for r in retire if r["created_by"] == "dashboard") == 3   # dedup keeps 1 of 4
    # never retire the manual-only row or any crawler row
    assert all(r["source_url"] != "https://internal/manual-only" for r in retire)
    assert all(r["created_by"] != "crawler" for r in retire)
```

- [ ] **Step 2: Run — expect FAIL** (module missing)

Run: `python3 -m pytest v2/tests/test_gradstudies_cleanup.py -q`
Expected: FAIL.

- [ ] **Step 3: Write the migration**

```python
#!/usr/bin/env python3
"""Clean-replace the pre-crawler Graduate Studies KB rows (SEPARATE gated migration).

Run ONLY after crawl_gradstudies.py --commit has written + verified the new crawler rows.
Retires: the dead-subdomain migration stub, the superseded njit-crawl rows, and duplicate
dashboard rows (keeps one). KEEPS: any dashboard row whose source_url is NOT a live
www.njit.edu/graduatestudies page (genuinely manual). Source-scoped + dry-run + hardened backup.

Spec: docs/superpowers/specs/2026-06-24-graduate-studies-crawl-design.md (G7)
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection
from v2.core.graph.orgs import org_node_id  # noqa: F401  (org resolve via organizations table below)

GS_SLUG = "graduate-studies"


def select_retire(conn) -> list[dict]:
    oid = conn.execute("SELECT id FROM organizations WHERE slug=?", (GS_SLUG,)).fetchone()[0]
    rows = conn.execute(
        "SELECT id, created_by, title, source_url FROM knowledge_items "
        "WHERE is_active=1 AND org_id=?", (oid,)).fetchall()
    retire: list[dict] = []
    dash_by_url: dict[str, list[int]] = {}
    for rid, cb, title, url in rows:
        if cb == "migration":
            retire.append({"id": rid, "created_by": cb, "title": title,
                           "source_url": url, "reason": "dead-subdomain-stub"})
        elif cb == "njit-crawl":
            retire.append({"id": rid, "created_by": cb, "title": title,
                           "source_url": url, "reason": "superseded-by-crawler"})
        elif cb == "dashboard":
            dash_by_url.setdefault(url, []).append(rid)
        # crawler rows are NEVER retired here
    # dedup dashboard rows sharing a source_url: keep the lowest id, retire the rest
    for url, ids in dash_by_url.items():
        for rid in sorted(ids)[1:]:
            retire.append({"id": rid, "created_by": "dashboard", "title": "(duplicate)",
                           "source_url": url, "reason": "duplicate-dashboard-row"})
    return retire


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    args = ap.parse_args(argv)
    conn = get_connection(args.db)
    retire = select_retire(conn)
    for r in retire:
        print(f"  retire id={r['id']:>6} [{r['created_by']}] {r['reason']:<24} {r['source_url']}")
    print(f"=== {len(retire)} rows to retire ===")
    if not args.commit:
        print("(dry run — pass --commit; a hardened backup is taken first)")
        return 0
    hardened_backup(args.db, "pre-gradstudies-cleanup")
    conn.executemany("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') WHERE id=?",
                     [(r["id"],) for r in retire])
    conn.commit()
    print(f"RETIRED {len(retire)} rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run — expect PASS**

Run: `python3 -m pytest v2/tests/test_gradstudies_cleanup.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/_gradstudies_cleanup_migrate.py v2/tests/test_gradstudies_cleanup.py
git commit -m "feat(gradstudies): separate gated clean-replace migration (retire stale/dup, keep manual-only)"
```

---

## Post-build gated rollout (NOT code tasks — the HARD GATE, owner-driven)

1. **Both hard-gate reviews** (senior-eng + RAG/anti-fab) against this plan's goals → fold findings → owner sign-off.
2. **Dev-copy dry run:** `cp gsa_gateway.db /tmp/dev.db && python scripts/crawl_gradstudies.py --db /tmp/dev.db` → review manifest (staff, prose, skipped PDFs/unknowns, truncation, warnings). Then `--commit` on the dev copy; `python scripts/verify_kg.py`.
3. **Live crawl:** `python scripts/crawl_gradstudies.py --commit --embed` (hardened backup auto). Evidence: counts + `verify_kg`.
4. **Chat-verify:** "PhD credit requirements", "how do I submit my thesis", "graduate studies forms", "full-time status PhD", "new graduate student orientation", "who is the Vice Provost for Graduate Studies".
5. **Clean-replace migration:** `python scripts/_gradstudies_cleanup_migrate.py` (dry-run, review the retire list with owner) → `--commit` → re-verify counts → re-embed if needed.
6. **Merge** `feat/gradstudies-crawl` → main.

## Self-Review notes

- **Spec coverage:** G1 (Task 4 org reuse), G2 (Task 2 roster + Task 4 contact attrs), G3 (Tasks 3–4, no exclusions, type=policy), G4 (Task 5 manifest), G5 (Task 4 content-hash idempotent), G6 (Task 4 uniform output + Task 6 regression), G7 (Task 7 migration). ND5 PDF page+link (inherited `files` capture, flagged in manifest). ND6 departure-reconcile deferred (noted in `ingest_gradstudies` docstring + rollout step). T3/T8 real-homepage + regression in Tasks 1/6.
- **Deviation from EOS made explicit:** Task 3 keeps roster-page prose (coverage rule); Task 2 roster parser is net-new (unit-header-aware, bare phones) — not a rename of EOS's email-anchored parser.
- **Type consistency:** `StaffRecord(name,title,phone,email,unit="")`, `parse_roster -> (list, list)`, `EntryResult(...,warnings=[])`, `ingest_gradstudies(...) -> dict` used consistently across Tasks 2–5.
