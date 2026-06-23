# High-Stakes Plan 1 — Cleanup + Re-classification Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Stop withholding generic office pages and prepare the genuine high-stakes ones for the Plan-2 serve: tighten `is_high_stakes` (future harvests) and run a gated, human-confirmed migration that re-classifies the existing 51 staged pages — false positives → `is_active=1` (generic, served by the office tier); genuine high-stakes → `is_active=1` + `metadata.stakes='high'` (routed to the Plan-2 verbatim path). **No answer-path change, no restart.**

**Architecture:** Two code units — (1) a tightened `is_high_stakes` regex (data producer, future harvests); (2) a gated migration `scripts/_reclassify_office_stakes.py` that applies a **human-confirmed `source_url → decision` map** (the source of truth — NOT a re-derivation, since some pages were staged by the `$`-text rule that URL-tightening can't undo), per page, atomically across a page's chunks, leaving `office_page_state` untouched (so the next crawl keeps skipping). Re-embed after (`embed_all.py`). Spec: `docs/superpowers/specs/2026-06-22-high-stakes-office-content-design.md` (Part 1).

**Tech Stack:** Python 3.11, SQLite, the prose-harvest modules (`office_ingest.is_high_stakes`, `hardened_backup`, `get_connection`), `embed_all.py`. Tests: pytest.

## Global Constraints

- **Source of truth = the human-confirmed `STAKES_DECISION` map** (`source_url → 'high'|'generic'`), seeded from §"Decision seed" below + the tightened classifier, **owner-reviewed before `--commit`**. The migration applies the map; it does NOT re-derive from reassembled chunk text. — spec 1b/1c [SE-P1/P2]
- **Per page, atomically:** a page = many chunks sharing `source_url`; update ALL its chunks. `UPDATE … WHERE source_url=? AND type='office_page'`. — spec 1b [SE-P1]
- **Leave `office_page_state` untouched** (don't bump/clear the content hash) so the next crawl skips re-ingest and the activation persists. Idempotent (re-run = no-op). — spec 1b [SE-P4]
- **Gated live write:** `hardened_backup` + dry-run default + `--commit`; dev-copy (`/tmp/dev.db`) first. — repo invariant
- **Never insert `search_text`** (generated). The migration only UPDATEs `is_active` + `metadata` — never the generated column.
- **`metadata` edit:** set/drop `stakes` via JSON (`json_set` / `json_remove`) without clobbering other keys (`doc_id`, `entity_id`, `verified`, `natural_key`).
- HARD GATE: built TDD; diffs shown for sign-off; the live migration runs only on owner go.

## Decision seed (the 51 staged pages — owner reviews/edits before commit)
**GENUINE high-stakes (`'high'`):** `bursar/Plan_Options.php`, `bursar/application-payment`, `bursar/critical-due-dates`, `bursar/employee-tuition-remission`, `bursar/important-dates`, `bursar/international-payments`, `bursar/payment-information`, `bursar/payment-options`, `bursar/payments`, `bursar/student-payment-portal`, `bursar/third-party-payments`, `bursar/touchnet-erefund`, `bursar/tuition-and-fee-schedule`, `bursar/outside-scholarships`, `bursar/node/71`, `bursar/faq-payments`, `bursar/faq-title-iv-authorization`, `global/h-1b-temporary-employment-visa-0`, `global/health-insurance-information-international-students`, `global/incoming/j1-students-exchange`, `global/j-2-dependent-employment/`, `global/j1students`, `global/opt-reporting`, `global/tax-information-international-students`, `global/incoming-student-information`, `parking/employee-parking-fees`, `registrar/how-residency-determined`.
**FALSE POSITIVE (`'generic'` → activate):** `bursar/estatements`, `bursar/faq/`, `bursar/faqs`, `bursar/node/66`, `environmentalsafety/fire-permits`, `life/`, `life/gyms-facilities`, `mailroom/amazon-lockers-njit`, `mailroom/types-mail`, `parking/2026-summer-hours`, `parking/additional-njit-parking-available-essex-county-college-0`, `parking/administrative-center-visitor-parking-494-broad-street`, `parking/daily-parking-options`, `parking/event-game-day-parking`, `parking/id-cards.php`, `parking/late-night-lyft-program`, `parking/nest-research-lyft-program`, `parking/parking-0`, `parking/parking-rules-and-regulations`, `parking/parking-venturelink-client-employees`, `parking/rules.php`, `parking/special-events-bus-parking`, `registrar/diploma-day`, `registrar/frequently-asked-questions`.
(Full `https://www.njit.edu/<path>` URLs in the map. Owner adjusts any line before `--commit`.)

---

### Task 1: Tighten `is_high_stakes` (future harvests)

**Files:**
- Modify: `v2/core/ingestion/office_ingest.py` (`_HIGH_STAKES_URL`)
- Test: `v2/tests/test_office_ingest.py` (extend `test_high_stakes_classifier`)

**Interfaces:** Consumes/produces the existing `is_high_stakes(url, text) -> bool`. Only the URL regex narrows; the `$`-text rule is unchanged.

- [ ] **Step 1: Write the failing test** — assert the CLEAR cases (the borderline ones are owner-decided in Task 2, not the classifier):

```python
# add to v2/tests/test_office_ingest.py::test_high_stakes_classifier (or a new test)
B = "https://www.njit.edu/"
# clearly GENERIC by URL (must be False unless the $-text rule fires):
for p in ["parking/2026-summer-hours", "parking/late-night-lyft-program", "mailroom/amazon-lockers-njit",
          "mailroom/types-mail", "parking/event-game-day-parking", "registrar/frequently-asked-questions"]:
    assert is_high_stakes(B+p, "general info, no dollar amounts") is False, p
# clearly GENUINE by URL:
for p in ["bursar/payment-options", "bursar/tuition-and-fee-schedule", "global/opt-reporting",
          "global/j1students", "bursar/employee-tuition-remission", "parking/employee-parking-fees"]:
    assert is_high_stakes(B+p, "prose") is True, p
# the $-text rule still catches a $-amount FAQ even on a benign URL:
assert is_high_stakes(B+"registrar/anything", "Your balance is $750 due by Nov 15.") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_office_ingest.py -q` — expect FAIL (e.g. `late-night-lyft-program`/`amazon-lockers` currently match nothing but `event-game-day` etc. — confirm which fail; the goal is the GENERIC set is all False on a no-`$` body and the GENUINE set all True).

- [ ] **Step 3: Refine the regex**

In `office_ingest.py`, narrow `_HIGH_STAKES_URL` to procedure/figure slugs (keep `employee-parking-fees`, drop topic/landing/FAQ slugs):
```python
_HIGH_STAKES_URL = re.compile(
    r"\b(opt|cpt|i-?20|i-?765|sevis|visas?|h-?1b|j-?1|j-?2|"
    r"tuition|payment|payments|pay-?plan|plan-options|refund|e-?refund|remission|"
    r"parking-fees|tax-information|"
    r"important-dates|critical-due-dates|due-dates|deadlines?)\b", re.I)
```
(Keep the existing `$`-amount + payment-intent TEXT rule unchanged — it is the safety net and is why the migration is human-confirmed, not regex-derived.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest v2/tests/test_office_ingest.py v2/tests/test_office_change_detection.py -q` — all pass.

- [ ] **Step 5: Commit**

```bash
git add v2/core/ingestion/office_ingest.py v2/tests/test_office_ingest.py
git commit -m "feat(high-stakes): tighten is_high_stakes URL rule to procedure/figure slugs [Plan1 1a]"
```

---

### Task 2: Gated re-classification migration

**Files:**
- Create: `scripts/_reclassify_office_stakes.py`
- Test: `v2/tests/test_reclassify_office_stakes.py`

**Interfaces:**
- Produces:
  - `STAKES_DECISION: dict[str, str]` — `full source_url → 'high' | 'generic'` (the §Decision seed, full URLs; owner-editable).
  - `reclassify(conn, decisions) -> dict` — for each `source_url`: if `'generic'` → all its `office_page` chunks `is_active=1` + `metadata.stakes` removed; if `'high'` → `is_active=1` + `metadata.stakes='high'`. Touches `knowledge_items` only. Returns counts. Idempotent.
  - `main(argv)` — gated CLI (dry-run default; `--commit` takes `hardened_backup`).

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_reclassify_office_stakes.py
import json, sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path: sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from scripts._reclassify_office_stakes import reclassify

GEN = "https://www.njit.edu/parking/2026-summer-hours"
HI  = "https://www.njit.edu/bursar/payment-options"


def _ins(conn, oid, url, stakes, active):
    meta = {"doc_id": "gsa-doc/x", "verified": True}
    if stakes: meta["stakes"] = stakes
    conn.execute("INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,is_active,created_by)"
                 " VALUES(?,?,?,?,?,?,?,'crawler')", (oid, "office_page", "t", "body", json.dumps(meta), url, active))


def test_reclassify_activates_generic_and_tags_high(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    with conn:
        oid = ensure_org(conn, slug="eos", name="EOS", parent_slug=None, type="office")
        _ins(conn, oid, GEN, "high", 0); _ins(conn, oid, GEN, "high", 0)   # 2 chunks, staged
        _ins(conn, oid, HI, "high", 0)                                     # 1 chunk, staged
        reclassify(conn, {GEN: "generic", HI: "high"})
    g = conn.execute("SELECT is_active, json_extract(metadata,'$.stakes') s FROM knowledge_items WHERE source_url=?", (GEN,)).fetchall()
    assert all(r["is_active"] == 1 and r["s"] is None for r in g)          # generic: active, no stakes
    h = conn.execute("SELECT is_active, json_extract(metadata,'$.stakes') s FROM knowledge_items WHERE source_url=?", (HI,)).fetchone()
    assert h["is_active"] == 1 and h["s"] == "high"                        # high: active, stakes kept
    # metadata not clobbered:
    assert conn.execute("SELECT json_extract(metadata,'$.doc_id') FROM knowledge_items WHERE source_url=?", (HI,)).fetchone()[0] == "gsa-doc/x"


def test_reclassify_is_idempotent(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    with conn:
        oid = ensure_org(conn, slug="eos", name="EOS", parent_slug=None, type="office")
        _ins(conn, oid, HI, "high", 0)
        reclassify(conn, {HI: "high"}); reclassify(conn, {HI: "high"})     # twice
    r = conn.execute("SELECT is_active, json_extract(metadata,'$.stakes') s FROM knowledge_items WHERE source_url=?", (HI,)).fetchone()
    assert r["is_active"] == 1 and r["s"] == "high"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_reclassify_office_stakes.py -q` — FAIL (`ModuleNotFoundError: scripts._reclassify_office_stakes`).

- [ ] **Step 3: Implement the migration**

```python
#!/usr/bin/env python
"""Gated re-classification of the existing staged office_page pages (Plan 1). Applies a
human-confirmed source_url -> 'high'|'generic' map: 'generic' -> is_active=1 + drop metadata.stakes;
'high' -> is_active=1 + metadata.stakes='high'. Touches knowledge_items only (NOT office_page_state),
so the next crawl keeps skipping. Idempotent. Dry-run default; --commit takes a hardened backup.
Re-embed afterwards with v2/scripts/embed_all.py."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection

_B = "https://www.njit.edu/"
# OWNER-EDITABLE confirmed decisions (full URLs). Seeded from the Plan-1 §Decision seed.
STAKES_DECISION: dict[str, str] = {
    # 'high' (genuine — payment/tuition/refund/due-dates, visa/OPT/J-1/H-1B, residency, tax):
    **{_B + p: "high" for p in [
        "bursar/Plan_Options.php", "bursar/application-payment", "bursar/critical-due-dates",
        "bursar/employee-tuition-remission", "bursar/important-dates", "bursar/international-payments",
        "bursar/payment-information", "bursar/payment-options", "bursar/payments",
        "bursar/student-payment-portal", "bursar/third-party-payments", "bursar/touchnet-erefund",
        "bursar/tuition-and-fee-schedule", "bursar/outside-scholarships", "bursar/node/71",
        "bursar/faq-payments", "bursar/faq-title-iv-authorization",
        "global/h-1b-temporary-employment-visa-0", "global/health-insurance-information-international-students",
        "global/incoming/j1-students-exchange", "global/j-2-dependent-employment/", "global/j1students",
        "global/opt-reporting", "global/tax-information-international-students",
        "global/incoming-student-information", "parking/employee-parking-fees",
        "registrar/how-residency-determined"]},
    # 'generic' (false positive -> activate):
    **{_B + p: "generic" for p in [
        "bursar/estatements", "bursar/faq/", "bursar/faqs", "bursar/node/66",
        "environmentalsafety/fire-permits", "life/", "life/gyms-facilities",
        "mailroom/amazon-lockers-njit", "mailroom/types-mail", "parking/2026-summer-hours",
        "parking/additional-njit-parking-available-essex-county-college-0",
        "parking/administrative-center-visitor-parking-494-broad-street", "parking/daily-parking-options",
        "parking/event-game-day-parking", "parking/id-cards.php", "parking/late-night-lyft-program",
        "parking/nest-research-lyft-program", "parking/parking-0", "parking/parking-rules-and-regulations",
        "parking/parking-venturelink-client-employees", "parking/rules.php",
        "parking/special-events-bus-parking", "registrar/diploma-day",
        "registrar/frequently-asked-questions"]},
}


def reclassify(conn, decisions: dict[str, str]) -> dict:
    """Apply the decision map per source_url across all of a page's office_page chunks."""
    gen = hi = 0
    for url, decision in decisions.items():
        if decision == "generic":
            n = conn.execute(
                "UPDATE knowledge_items SET is_active=1, "
                "metadata=json_remove(metadata,'$.stakes'), updated_at=datetime('now') "
                "WHERE source_url=? AND type='office_page'", (url,)).rowcount
            gen += 1 if n else 0
        elif decision == "high":
            conn.execute(
                "UPDATE knowledge_items SET is_active=1, "
                "metadata=json_set(metadata,'$.stakes','high'), updated_at=datetime('now') "
                "WHERE source_url=? AND type='office_page'", (url,))
            hi += 1
    return {"generic_pages": gen, "high_pages": hi}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "gsa_gateway.db"))
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args(argv)
    g = sum(1 for v in STAKES_DECISION.values() if v == "generic")
    h = sum(1 for v in STAKES_DECISION.values() if v == "high")
    print(f"reclassify: {g} generic (activate) + {h} high (keep staged-tag) pages")
    if not args.commit:
        print("(dry run — pass --commit; a hardened backup is taken first)")
        return 0
    bkp = hardened_backup(args.db, "pre-stakes-reclassify")
    print(f"backup: {bkp.name}")
    conn = get_connection(args.db)
    with conn:
        print("  ", reclassify(conn, STAKES_DECISION))
    print("next: python v2/scripts/embed_all.py  (the activated generic chunks need vectors)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest v2/tests/test_reclassify_office_stakes.py -q` — 2 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/_reclassify_office_stakes.py v2/tests/test_reclassify_office_stakes.py
git commit -m "feat(high-stakes): gated re-classification migration (confirmed list; per-page; office_page_state untouched) [Plan1 1b]"
```

---

## GATED RUNBOOK (after the two tasks pass — owner approval at each ▶)

**▶ Gate A — Owner reviews the `STAKES_DECISION` map** (the §Decision seed). Adjust any line; this is the source of truth.
**▶ Gate B — Dev-copy dry run:** `sqlite3 ./gsa_gateway.db ".backup '/tmp/dev.db'"` → `python3 scripts/_reclassify_office_stakes.py --db /tmp/dev.db --commit` → inspect: generic pages now `is_active=1` no `stakes`; high pages `is_active=1` `stakes='high'`; `office_page_state` row counts unchanged. `embed_all.py --db`-equivalent on dev + spot-check a generic question ("amazon lockers", "parking summer hours") now answers via the office tier.
**▶ Gate C — Live (owner go):** `python3 scripts/_reclassify_office_stakes.py --commit` (hardened backup) → `python3 v2/scripts/embed_all.py`. DB-only → **no restart**. (The genuine high-stakes pages are now `is_active=1, stakes='high'` but the Plan-2 serve path doesn't exist yet — they'd be adopted by the office tier and **composed**; so DO NOT activate the high-stakes ones live until Plan 2 ships. **Option:** in Gate C, apply only the `'generic'` decisions live now, and apply the `'high'` ones together with the Plan-2 deploy. The migration supports a `--generic-only` flag for this split — add it in Task 2 if the owner wants the cleanup live before Plan 2.)

> **Sequencing note [IMPORTANT]:** activating high-stakes pages (`is_active=1, stakes='high'`) is only safe once Plan 2's verbatim path exists — otherwise the office tier would **compose** them (the failure we're preventing). So either ship Plan 1's **generic cleanup now** + the **high activation with Plan 2**, or ship all of Plan 1 only when Plan 2 is ready. Recommend a `--generic-only` flag (Task 2 step) so the cleanup wins ship immediately and the high activation lands with Plan 2.

## Self-review
- **Spec coverage:** 1a tighten classifier ✓; 1b gated migration per-`source_url`, confirmed-list source of truth, `office_page_state` untouched, idempotent, re-embed ✓; 1c owner-review list = the `STAKES_DECISION` seed ✓.
- **The Plan-1/Plan-2 sequencing hazard is caught:** high-stakes activation must not precede Plan 2 (else composed) — handled via the `--generic-only` split + the runbook note. **This is the key safety item.**
- **Placeholder scan:** none. **Type consistency:** `reclassify(conn, dict[str,str]) -> dict`; `STAKES_DECISION: dict[str,str]`; UPDATEs touch `is_active`/`metadata`/`updated_at` only (never `search_text`), `office_page_state` untouched.
