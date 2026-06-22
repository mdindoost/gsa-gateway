# Prose Harvest — Plan D: Wave-1 Harvest + Go-Live — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development for the TDD tasks (D1–D2). The GO-LIVE RUNBOOK is a gated operational sequence — execute it step-by-step with explicit owner approval at each gate, NOT as autonomous tasks.

**Goal:** Register the Phase-1 Wave-1 office entry points, harvest their prose into the KB (`office_page`), and bring it live so the assistant answers everyday operational questions ("where do I park", "campus shuttle", "how do I get my photo ID", "where's the mailroom") — without diluting curated answers and without serving ungrounded high-stakes procedures.

**Architecture:** A gated, idempotent registration script creates the EOS org and registers the Wave-1 entry points (EOS cluster + OGI/Bursar/Registrar) into `crawl_entry_points`. Harvest runs through the Plan A/C engine (`harvest_office.py`). Generic prose goes live (`is_active=1`); high-stakes pages stay STAGED (`is_active=0`, invisible to retrieval) and are covered, grounded, by the existing live njit.edu fallback. Go-live = merge to `main` + restart (code, inert with empty corpus) → gated DB content write + embed.

**Tech Stack:** the Plan A–C modules (`entry_point_store`, `harvest_office.py`, `recrawl_offices.py`), `ensure_org`, `hardened_backup`, `embed_all.py`. Tests: pytest.

## Global Constraints

- **Orgs pre-exist before harvest.** EOS is created here; `ogi`/`bursar`/`registrar` already exist (offices under `njit`) — `ensure_org` is get-or-create and MUST NOT clobber their names. — Plan A/C invariant
- **Register seeds WITH a trailing slash** (`/parking/`, not `/parking`) — else self-extension/scope break. — Plan C [C3]
- **EOS cluster shares `org_slug='eos'`** (multiple entry points, one org); service areas are KB topics, NOT child orgs; NO org aliases (the `people_in_org` mis-route). — parking reconciliation
- **High-stakes stays staged.** OPT/CPT/I-20, deadlines, billing/$-amount pages are staged `is_active=0` and NOT activated in Wave-1 (the grounded-extract-for-staged step is deferred); they are covered grounded by the live fallback. — spec §4.3 [RA4]
- **Gated live writes:** dev-copy (`/tmp/dev.db`) first → inspect → owner go → live `--commit` + `hardened_backup` + `embed_all`. — repo invariant
- **`office_page` is excluded from the primary corpus** (Plan B) and answered only by the office tier on a primary miss — so adding content cannot dilute curated answers. — Plan B [SE2]
- **Eval grows:** Wave-1 verification + adversarial routing probes added to `eval/questions.txt` as a gate. — "grow correctness suite"
- HARD GATE: nothing merges to `main` / nothing live / no restart without explicit owner approval at each go-live gate.

---

## File structure

- **Create** `scripts/register_office_seeds.py` — gated, idempotent: ensure EOS org + register Wave-1 entry points.
- **Modify** `eval/questions.txt` — Wave-1 operational questions + adversarial routing probes.
- **Create** test `v2/tests/test_register_office_seeds.py`.

---

### Task 1: Wave-1 entry-point registration script

**Files:**
- Create: `scripts/register_office_seeds.py`
- Test: `v2/tests/test_register_office_seeds.py`

**Interfaces:**
- Consumes: `ensure_org`, `entry_point_store.add_seed`, `hardened_backup`, `get_connection`.
- Produces:
  - `WAVE1: list[dict]` — the registration spec (url, scope_prefix, org_slug, org_type, parent_slug, crawl_interval_days, org_name).
  - `register(conn) -> dict` — ensures each org (EOS created; existing orgs get-or-create, names NOT overwritten) and `add_seed`s each entry point. Idempotent. Returns counts.
  - `main(argv)` — gated CLI (dry-run default; `--commit` takes a backup first).

The Wave-1 set (URLs registered WITH trailing slashes; explicit scope_prefix):

```python
WAVE1 = [
    # EOS cluster — one org 'eos' (created here), multiple hubs (multisite njit.edu.parking)
    dict(url="https://www.njit.edu/parking/",          scope_prefix="/parking/",
         org_slug="eos", org_name="Environmental and Operational Services (EOS)"),
    dict(url="https://www.njit.edu/mailroom/",         scope_prefix="/mailroom/",         org_slug="eos"),
    dict(url="https://www.njit.edu/sustainability/",   scope_prefix="/sustainability/",   org_slug="eos"),
    dict(url="https://www.njit.edu/environmentalsafety/", scope_prefix="/environmentalsafety/", org_slug="eos"),
    # Existing offices — register entry points only (orgs already exist)
    dict(url="https://www.njit.edu/global/",   scope_prefix="/global/",   org_slug="ogi"),
    dict(url="https://www.njit.edu/bursar/",   scope_prefix="/bursar/",   org_slug="bursar"),
    dict(url="https://www.njit.edu/registrar/", scope_prefix="/registrar/", org_slug="registrar"),
]
```

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_register_office_seeds.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from v2.core.ingestion import entry_point_store as eps
from scripts.register_office_seeds import register


def test_register_creates_eos_keeps_existing_and_seeds_all(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    with conn:
        ensure_org(conn, slug="njit", name="NJIT", parent_slug=None, type="university")
        # pre-existing offices with their real names — must NOT be overwritten
        ensure_org(conn, slug="ogi", name="Office of Global Initiatives", parent_slug="njit", type="office")
        ensure_org(conn, slug="bursar", name="Office of the Bursar / Student Accounts", parent_slug="njit", type="office")
        ensure_org(conn, slug="registrar", name="Office of the Registrar", parent_slug="njit", type="office")
        register(conn)
    # EOS created with its proper parenthetical name
    eos = conn.execute("SELECT name,type FROM organizations WHERE slug='eos'").fetchone()
    assert eos is not None and "(EOS)" in eos["name"] and eos["type"] == "office"
    # existing org name preserved
    ogi = conn.execute("SELECT name FROM organizations WHERE slug='ogi'").fetchone()
    assert ogi["name"] == "Office of Global Initiatives"
    # all 7 entry points active, EOS cluster shares org_slug
    active = eps.list_active(conn, aspect="office")
    urls = {r["url"] for r in active}
    assert "https://www.njit.edu/parking/" in urls and "https://www.njit.edu/registrar/" in urls
    eos_eps = [r for r in active if r["org_slug"] == "eos"]
    assert len(eos_eps) == 4 and all(r["url"].endswith("/") for r in eos_eps)   # trailing slash


def test_register_is_idempotent(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    with conn:
        ensure_org(conn, slug="njit", name="NJIT", parent_slug=None, type="university")
        register(conn)
        register(conn)                              # second run must not duplicate
    n = conn.execute("SELECT COUNT(*) c FROM crawl_entry_points").fetchone()["c"]
    assert n == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_register_office_seeds.py -q`
Expected: FAIL — `ModuleNotFoundError: scripts.register_office_seeds`.

- [ ] **Step 3: Implement the registration script**

```python
#!/usr/bin/env python
"""Gated, idempotent registration of the Phase-1 Wave-1 office entry points (spec §6 / Plan D).
Creates the EOS org (existing orgs are get-or-create, names preserved) and registers each entry
point into crawl_entry_points. Dry-run default; --commit takes a hardened backup. Harvest next
with scripts/harvest_office.py."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection
from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.ingestion import entry_point_store as eps

WAVE1 = [
    dict(url="https://www.njit.edu/parking/", scope_prefix="/parking/", org_slug="eos",
         org_name="Environmental and Operational Services (EOS)"),
    dict(url="https://www.njit.edu/mailroom/", scope_prefix="/mailroom/", org_slug="eos"),
    dict(url="https://www.njit.edu/sustainability/", scope_prefix="/sustainability/", org_slug="eos"),
    dict(url="https://www.njit.edu/environmentalsafety/", scope_prefix="/environmentalsafety/", org_slug="eos"),
    dict(url="https://www.njit.edu/global/", scope_prefix="/global/", org_slug="ogi"),
    dict(url="https://www.njit.edu/bursar/", scope_prefix="/bursar/", org_slug="bursar"),
    dict(url="https://www.njit.edu/registrar/", scope_prefix="/registrar/", org_slug="registrar"),
]
INTERVAL_DAYS = 30          # owner-tunable recurrence cadence


def register(conn) -> dict:
    """Ensure each org exists (EOS created; existing orgs NOT renamed) and register each entry
    point. Idempotent (ensure_org is get-or-create; add_seed upserts to active)."""
    seen_orgs: set[str] = set()
    for ep in WAVE1:
        slug = ep["org_slug"]
        if slug not in seen_orgs:
            # ensure_org is get-or-create: only the EOS row is new; existing org names are kept.
            ensure_org(conn, slug=slug, name=ep.get("org_name", slug), parent_slug="njit", type="office")
            seen_orgs.add(slug)
        eps.add_seed(conn, url=ep["url"], scope_prefix=ep["scope_prefix"], org_slug=slug,
                     parent_slug="njit", org_type="office", crawl_interval_days=INTERVAL_DAYS)
    sync_org_nodes(conn)
    return {"orgs": len(seen_orgs), "entry_points": len(WAVE1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args(argv)
    print(f"register Wave-1: {len(WAVE1)} entry points → orgs eos/ogi/bursar/registrar")
    if not args.commit:
        print("(dry run — pass --commit; a hardened backup is taken first)")
        return 0
    bkp = hardened_backup(args.db, "pre-office-register")
    print(f"backup: {bkp.name}")
    conn = get_connection(args.db)
    with conn:
        print("  ", register(conn))
    print("next: python scripts/harvest_office.py --commit  (dev-copy first), then v2/scripts/embed_all.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest v2/tests/test_register_office_seeds.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/register_office_seeds.py v2/tests/test_register_office_seeds.py
git commit -m "feat(prose-harvest): Wave-1 office entry-point registration (EOS + ogi/bursar/registrar) [D5]"
```

---

### Task 2: Eval gate — Wave-1 questions + adversarial routing probes

**Files:**
- Modify: `eval/questions.txt`

**Interfaces:** none (data file). The harness `scripts/eval.sh` runs these through the real pipeline.

- [ ] **Step 1: Append the questions**

Add to `eval/questions.txt` (one question per line; keep the `# category` header style already used in the file):

```
# parking / operations (EOS)
where do I park on campus
how much is a parking permit
where is visitor parking
is there a campus shuttle
how do I get my photo ID card
where is the mailroom
who do I contact about a parking permit
what is EOS
# operations — adversarial routing probes (must NOT dead-end on empty people skills)
who works in parking
parking staff
parking office leadership
who is the parking director
# offices (global / bursar / registrar) — operational prose
how do I contact the bursar
where is the registrar office
what does the office of global initiatives do
```

- [ ] **Step 2: Sanity-check the file parses**

Run: `bash scripts/eval.sh --limit 3`
Expected: the harness runs (exercises 3 questions through the real pipeline) without a parse error. (Full accuracy numbers are validated in the go-live runbook, after content is live.)

- [ ] **Step 3: Commit**

```bash
git add eval/questions.txt
git commit -m "test(prose-harvest): Wave-1 operational eval questions + adversarial routing probes"
```

---

## GO-LIVE RUNBOOK (gated — owner approval at each ▶ gate; NOT autonomous)

The branch carries Plans A–D. Bringing it live is a sequence of gated steps. Stop at each ▶ for explicit approval.

**▶ Gate 0 — Final pre-merge review.** Confirm the per-plan opus reviews (A/B/C all MERGE-READY) + this Plan D are acceptable; optionally a final senior-eng + RAG review of the whole branch diff vs `main`. Owner signs off to merge.

**▶ Gate 1 — Merge code to `main` + restart (SAFE, zero behavior change).**
- `git checkout main && git merge --no-ff feat/prose-harvest` (owner runs / approves).
- `bash scripts/restart.sh` — the office tier is now live but the office corpus is EMPTY, so answers are byte-equivalent to before (verified in Plan B). Confirm clean boot + a couple of normal questions still answer as before.

**▶ Gate 2 — Dev-copy dry run (no live write).**
- `cp gsa_gateway.db /tmp/dev.db`
- `python scripts/register_office_seeds.py --db /tmp/dev.db --commit`
- `python scripts/harvest_office.py --db /tmp/dev.db --commit` (crawls Wave-1; generic→`is_active=1`, high-stakes→staged `is_active=0`).
- Inspect `/tmp/dev.db`: `office_page` active vs staged counts per org; spot-read a few pages; confirm each hub actually crawled (non-zero pages) and the quality gate didn't over-drop. If a hub crawled poorly (JS-only, wrong scope), fix the seed/scope and re-run on the dev copy.
- `python v2/scripts/embed_all.py` against a copy + `bash scripts/ask.sh "where do I park" --answer` (pointing at the dev DB) to sanity-check the office tier answers.

**▶ Gate 3 — Live write (owner go).**
- `python scripts/register_office_seeds.py --commit` (live; takes a hardened backup).
- `python scripts/harvest_office.py --commit` (live; hardened backup).
- `python v2/scripts/embed_all.py` (embeds the new active `office_page` rows). DB-only → **no restart needed**.

**▶ Gate 4 — Verify.**
- Chat: "where do I park", "visitor parking", "campus shuttle", "how do I get my photo ID", "where's the mailroom", "what is EOS" → answered from office prose with the njit.edu source link; the adversarial probes ("who works in parking") must NOT dead-end.
- High-stakes ("OPT/CPT steps", "tuition due date") → still answered by the live njit.edu fallback (grounded), since those pages are staged/inactive.
- `bash scripts/eval.sh` → coverage/accuracy incl. the new questions.

**▶ Gate 5 — Recurrence (optional, owner-set).** The entry points carry `crawl_interval_days=30`; `python scripts/recrawl_offices.py --commit` re-crawls due ones (change-detected). Schedule per owner preference.

### Deferred (loudly flagged)
- **High-stakes activation:** the verbatim grounded-extract for staged OPT/CPT/billing pages is NOT built — those pages stay staged/inactive in Wave-1 and are covered by the live fallback. Building grounded-extract-for-staged + an approve/activate step is a follow-up before any high-stakes office prose goes live.
- **Candidate activation UI + dashboard recrawl button** (Plan C deferral) — discovered `candidate` hubs are activated via CLI only, and the activation must set `org_slug`/`scope_prefix`/`crawl_interval_days` (a bare `activate()` leaves them NULL).
- **Wave-2 offices** (IT/student-life/rec/financial-aid/career, + dining/library iff a `www.njit.edu` hub) — after Wave-1 verifies.

## Self-review
- **Spec coverage:** Wave-1 registration (D1) ✓; eval gate (D2) ✓; go-live sequence with the safe code-first/content-second ordering (runbook) ✓; honest-partial preserved (high-stakes staged, live fallback covers them) ✓; no dilution (office_page excluded, Plan B) ✓.
- **Deferred loudly:** high-stakes grounded-extract/activation; candidate-activation UI + dashboard button; Wave-2.
- **Placeholder scan:** none — D1/D2 have full code; the runbook is exact commands at each gate.
- **Type consistency:** `register(conn)->dict`, `add_seed(..., crawl_interval_days=30)`, EOS cluster all `org_slug='eos'` with trailing-slash URLs — consistent with Plan C's seed/discover contracts.
```
