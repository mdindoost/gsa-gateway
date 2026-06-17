# International Content Pilot (D+L) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Answer the international-student intents (I-20/SEVIS/arrival + CPT/OPT) with safe "overview + route-to-OGI" content, proven by a fast deterministic gate.

**Architecture:** 6 OGI overview docs (drafted from maintainer-provided OGI pages, verified) ingested under the existing OGI org via the existing office-doc pipeline; answered by the rerank stack + the live immigration heads-up; gated chunk-level (no LLM). Reuses `ingest_office_docs.py`, the section chunker, and the office-gate pattern.

**Tech Stack:** Python 3.11, the v2 ingestion/retrieval stack, pytest.

**Design spec:** `docs/superpowers/specs/2026-06-17-international-pilot-design.md` (read it).
**Branch:** `feat/international-pilot` (already created).

---

## File Structure

- Modify `scripts/ingest_office_docs.py` — add the `"international"` folder→OGI mapping.
- Create `bot/data/sources/international/<slug>.md` — 6 docs.
- Create `v2/tests/international_gold.py` — frozen `{question → gold token}` map.
- Create `v2/tests/test_international_gold.py` — the deterministic acceptance gate.

---

## Task 1: Wire the `international` folder to the OGI org

**Files:**
- Modify: `scripts/ingest_office_docs.py` (the `OFFICES` dict)

- [ ] **Step 1: Add the folder→org mapping**

In `scripts/ingest_office_docs.py`, change the `OFFICES` dict from:

```python
OFFICES: dict[str, tuple[str, str, str, str]] = {
    "graduate-studies": ("graduate-studies", "Graduate Studies", "njit", "office"),
    "ogi": ("ogi", "Office of Global Initiatives", "njit", "office"),
    "computer-science": ("computer-science", "Computer Science", "ywcc", "department"),
    "informatics": ("informatics", "Informatics", "ywcc", "department"),
}
```

to (add one line — the `international` folder also files under the existing `ogi` org):

```python
OFFICES: dict[str, tuple[str, str, str, str]] = {
    "graduate-studies": ("graduate-studies", "Graduate Studies", "njit", "office"),
    "ogi": ("ogi", "Office of Global Initiatives", "njit", "office"),
    "international": ("ogi", "Office of Global Initiatives", "njit", "office"),
    "computer-science": ("computer-science", "Computer Science", "ywcc", "department"),
    "informatics": ("informatics", "Informatics", "ywcc", "department"),
}
```

- [ ] **Step 2: Verify the dry-run recognizes the new folder mapping**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -c "from scripts.ingest_office_docs import OFFICES; print(OFFICES['international'])"`
Expected: `('ogi', 'Office of Global Initiatives', 'njit', 'office')`

- [ ] **Step 3: Commit**

```bash
cd /home/md724/gsa-gateway
git add scripts/ingest_office_docs.py
git commit -m "feat(international): map sources/international/ folder to the OGI org"
```

---

## Task 2: Draft the 6 OGI overview docs (then MAINTAINER VERIFIES)

**Files:**
- Create: `bot/data/sources/international/<slug>.md` (6 files)

- [ ] **Step 1: Draft from the maintainer-provided OGI content**

Using the OGI page content the maintainer brings (CPT, OPT, STEM OPT, I-20, SEVIS, on-campus
employment, maintaining status), create these 6 docs. Each is *what it is → eligibility →
general process & timing → "file/confirm through OGI" + link*. Keep the **formal term** in the
body (so topic queries retrieve it) and **avoid volatile specifics** (exact fees, form numbers).
Use clear `##` sections; the section chunker keeps each fact in its own chunk.

- `cpt.md` — must contain "Curricular Practical Training (CPT)"
- `opt-stem-opt.md` — must contain "Optional Practical Training (OPT)" and "STEM OPT"
- `i-20-and-arrival.md` — must contain "I-20", deferral, late arrival, international orientation
- `sevis.md` — must contain "SEVIS" and the SEVIS fee + transferring SEVIS
- `on-campus-employment.md` — must contain "on-campus employment" and F-1 hours
- `maintaining-f1-status.md` — must contain "maintain" + "F-1 status"; link the existing F-1
  full-time doc, do not duplicate it.

Template (front-matter title + source_url; body sections):

```markdown
---
title: "Curricular Practical Training (CPT) — Overview"
source_url: "https://www.njit.edu/global/cpt"
---

# Curricular Practical Training (CPT)

## What it is
Curricular Practical Training (CPT) is work authorization for F-1 students for an internship or
co-op that is an integral part of your program of study.

## Eligibility & timing
<overview from the OGI page — basic eligibility, when in your program, full vs part-time at a
high level>

## How to apply
CPT is authorized by the Office of Global Initiatives (OGI). You must apply and be approved by
OGI before you begin any employment. Start the process with OGI: https://www.njit.edu/global
```

- [ ] **Step 2: Note any new OGI office/person provided** (entity capture) — add named OGI
advisers/contacts the maintainer provides to a list for verification (do not auto-insert).

- [ ] **Step 3: MAINTAINER VERIFICATION CHECKPOINT (do not commit before this)**

Present the 6 drafted docs to the maintainer. Apply corrections. Proceed only once they confirm
the content is accurate (high-stakes — immigration).

- [ ] **Step 4: Verify the gold tokens are present in the drafted content**

Run:
```bash
cd /home/md724/gsa-gateway && for t in "Curricular Practical Training" "Optional Practical Training" "STEM OPT" "I-20" "SEVIS" "on-campus employment" "F-1 status"; do
  grep -rqi "$t" bot/data/sources/international/ && echo "OK  $t" || echo "MISSING  $t"
done
```
Expected: all `OK`. If any `MISSING`, add the formal term to the relevant doc.

- [ ] **Step 5: Commit the verified content**

```bash
cd /home/md724/gsa-gateway
git add bot/data/sources/international/
git commit -m "content(international): OGI overview docs (verified) — CPT/OPT/I-20/SEVIS"
```

---

## Task 3: Gated ingest + embed

**Files:** none (runs the ingester)

- [ ] **Step 1: Dry-run**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python scripts/ingest_office_docs.py 2>&1 | sed -n '/international:/,/(dry/p'`
Expected: lists the 6 international docs under org `ogi`, "(dry run …)".

- [ ] **Step 2: Commit to the DB + embed**

Run:
```bash
cd /home/md724/gsa-gateway
.venv/bin/python scripts/ingest_office_docs.py --commit 2>&1 | tail -3
.venv/bin/python v2/scripts/embed_all.py 2>&1 | grep -iE "Successfully|Failed" | tail -2
```
Expected: "backup: …", "committed: N chunk(s)", new chunks embedded (0 failed).

- [ ] **Step 3: Verify the docs are filed under OGI**

Run:
```bash
cd /home/md724/gsa-gateway && .venv/bin/python - <<'EOF'
from v2.core.database.schema import get_connection
c = get_connection("gsa_gateway.db")
for slug in ("cpt","opt-stem-opt","i-20-and-arrival","sevis","on-campus-employment","maintaining-f1-status"):
    n = c.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND "
                  "json_extract(metadata,'$.doc_id')=?", (f"gsa-doc/{slug}",)).fetchone()[0]
    print(f"  {slug}: {n} active chunk(s)")
EOF
```
Expected: each topic has ≥1 active chunk.

---

## Task 4: The international acceptance gate

**Files:**
- Create: `v2/tests/international_gold.py`, `v2/tests/test_international_gold.py`

- [ ] **Step 1: Write the gold map**

Create `v2/tests/international_gold.py`:

```python
"""Frozen {question -> gold token} for the international (D+L) gate. Token is a stable string
that must appear in a top-2 reranked chunk. Queries are student-phrased."""

INTL_GOLD = {
    "how do I apply for CPT": "Curricular Practical Training",
    "what is curricular practical training": "Curricular Practical Training",
    "how do I apply for OPT before graduation": "Optional Practical Training",
    "can I work after I finish my degree on OPT": "Optional Practical Training",
    "what is STEM OPT": "STEM OPT",
    "how do I request my I-20 after admission": "I-20",
    "what financial documents do I need for my I-20": "I-20",
    "what is the SEVIS fee": "SEVIS",
    "how do I transfer my SEVIS record to NJIT": "SEVIS",
    "can F-1 students work on campus": "on-campus",
    "how do I keep my F-1 status": "F-1 status",
    "my visa is delayed can I defer or arrive late": "I-20",
}

# Overlap guard: the office pilot must still own the OPT *job search*; OGI owns OPT *application*.
OVERLAP = {
    "who do I contact about my OPT job search": "Career Development",
    "how do I apply for OPT": "Optional Practical Training",
}

# No-regression guard.
GUARD = {
    "what is the maximum GSA travel award": "maximum of $900",
    "who do I contact about a billing hold": "Bursar",
    "who are the GSA officers": "officer",
}
```

- [ ] **Step 2: Write the gate test**

Create `v2/tests/test_international_gold.py`:

```python
import pytest
from v2.core.database.schema import get_connection
from v2.core.retrieval.retriever import V2Retriever
from v2.core.retrieval.embedder import Embedder
from v2.core.retrieval.reranker import CrossEncoderReranker
from v2.tests.international_gold import INTL_GOLD, OVERLAP, GUARD


@pytest.fixture(scope="module")
def retr():
    conn = get_connection("gsa_gateway.db")
    return V2Retriever(conn, Embedder(), reranker=CrossEncoderReranker())


def _hits(retr, q, token, k):
    return any(token.lower() in (c.content or "").lower() for c in retr.retrieve(q, limit=k))


@pytest.mark.slow
@pytest.mark.parametrize("q,token", list(INTL_GOLD.items()))
def test_intl_topic_in_top2(retr, q, token):
    assert _hits(retr, q, token, 2), f"{q!r} -> want {token!r} in top-2"


@pytest.mark.slow
@pytest.mark.parametrize("q,token", list(OVERLAP.items()))
def test_overlap_routes_correctly(retr, q, token):
    assert _hits(retr, q, token, 2), f"overlap {q!r} -> want {token!r} in top-2"


@pytest.mark.slow
@pytest.mark.parametrize("q,token", list(GUARD.items()))
def test_no_regression(retr, q, token):
    assert _hits(retr, q, token, 5), f"regression {q!r} -> want {token!r}"
```

- [ ] **Step 3: Run the gate**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest v2/tests/test_international_gold.py -m slow 2>&1 | grep -E "passed|failed|want" | tail -20`
Expected: all pass. **If a topic fails:** sharpen that doc's wording so the formal term + the asked concept are in one section, re-ingest+embed, re-run. **If the overlap fails** ("apply for OPT" pulls Career Dev, or "OPT job" pulls OGI): tighten the two docs — OGI `opt-stem-opt` leads with "Optional Practical Training (OPT) work authorization / applying", Career-Dev keeps "OPT/CPT **job search**" — re-ingest, re-run. Do not proceed until green.

- [ ] **Step 4: Commit**

```bash
cd /home/md724/gsa-gateway
git add v2/tests/international_gold.py v2/tests/test_international_gold.py
git commit -m "test(international): deterministic D+L gate (topics top-2, OPT overlap both ways, guard)"
```

---

## Task 5: Finalize

- [ ] **Step 1: Heads-up + end-to-end spot check**

Run:
```bash
cd /home/md724/gsa-gateway && .venv/bin/python -c "
from bot.core.headsup import apply_headsup
print('CPT heads-up:', 'YES' if 'confirm with' in apply_headsup('x','how do I apply for CPT').lower() else 'NO')
print('OPT heads-up:', 'YES' if 'confirm with' in apply_headsup('x','how do I apply for OPT').lower() else 'NO')
"
```
Expected: both YES (immigration heads-up fires).

- [ ] **Step 2: Entity capture (only if the maintainer verified any OGI people in Task 2 Step 2)** — add via the gated `people_editor` path (mirror `scripts/_ingest_office_people.py`); else skip.

- [ ] **Step 3: Mark spec implemented + record results**

In `docs/superpowers/specs/2026-06-17-international-pilot-design.md`, set Status to `Implemented (2026-06-17)` and append the gate result (N/N intents at rank ≤2, overlap status).

```bash
cd /home/md724/gsa-gateway
git add docs/superpowers/specs/2026-06-17-international-pilot-design.md
git commit -m "docs: mark international pilot implemented + record gate results"
```

- [ ] **Step 4: Report** the gate result + heads-up confirmation, then proceed to finishing-a-development-branch (merge + restart per the maintainer's call).
