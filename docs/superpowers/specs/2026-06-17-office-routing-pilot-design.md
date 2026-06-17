# Office-Routing Content Pilot (Category M) + NJIT Entity Capture — Design

**Date:** 2026-06-17
**Status:** IMPLEMENTED (2026-06-17). Gate 15/15 — all 12 office intents (incl. the 4 adversarial overlap pairs) route to the correct office at rank<=2; 3 guard questions held. RAG-first held (no structured skill needed). 10 offices ingested, 3 legacy seeds retired. Directors captured in the office docs; standalone person-nodes deferred.

**Original status:** Approved + senior-eng review incorporated — C1 explicit legacy-seed retirement
(the 3 GSA-filed contacts get migrated, not duplicated), S1 migrate-not-enrich, S2 overlap
pairs as a gate acceptance criterion, N1 one-section-per-office, N2 person-capture routed
through verification (user-confirmed: capture but verify, don't drop, don't blind-insert), N3 rank≤2 on id-stable tokens, N4 doc_type=contact.
**Relates to:** `project_day_to_day_intents` (this is the first content pilot — category M of
the 150 intents), `2026-06-16-rerank-retrieval-design.md` (answering uses that stack),
`2026-06-17-answerability-router-design.md` (heads-up still fires).

## Goal

Answer "which office handles X / who do I contact about Y" by surfacing the right NJIT office
with its contact info — via the retrieval stack we already built (no new mechanism). And, as a
standing principle, **capture any new NJIT office or person we encounter into the KG/KB** while
building the directory, following the existing gated patterns.

This proves the content pipeline (draft → verify → ingest → gate) end-to-end on the lowest-risk,
most-structured, highest-everyday-value category, with a real 7-entry seed already in the KB.

## Principle: reuse what we built (no bespoke paths)

- **KB ingest** = the existing gated doc pipeline: `upsert_doc_items` → the **section-aware
  chunker** → **per-section `entity_id` + shared `doc_id`**, `source='dashboard'`, then
  `python v2/scripts/embed_all.py`; `hardened_backup` + dry-run default + `--commit`.
- **KG** = `ensure_org(...)` + `sync_org_nodes`; people via `people_editor.add_or_edit_person`
  (the caller owns the transaction, per the invariant). Contacts stay as **`contact`-type KB
  items** uniform with the existing 7.
- **Answering** = router (no new office skill) → **V2Retriever (hybrid + cross-encoder rerank)**
  → office doc surfaces → LLM answers. The immigration/billing/funding **heads-up still fires**.
- **Gate** = a deterministic chunk-level test in the style of `v2/tests/test_rerank_gold_chunks.py`.

## Components

### 1. Office directory content (drafted by me, verified by you)

~10 offices the M intents touch. Each office = one markdown doc in
`bot/data/sources/offices/<slug>.md` with a front-matter `title` + `source_url` (like the
office docs) and a body that includes a **`Handles:`** line (the topics it owns — this is what
makes topic questions retrieve it) plus contact (email / location / hours / link).

| slug | office | handles (topics) | in seed? |
|---|---|---|---|
| graduate-admissions | Office of Graduate Admissions | applying, application status, decisions, transcripts-for-admission | new |
| ogi | Office of Global Initiatives | visa, I-20, CPT, OPT, SEVIS, F-1 status | yes (id125) |
| registrar | Office of the Registrar | registration, add/drop, withdrawal, transcripts, enrollment certification | new |
| bursar | Office of the Bursar / Student Accounts | tuition, billing, payments, payment plans, refunds, financial holds | new |
| graduate-studies | Office of Graduate Studies | thesis/dissertation review, milestones, full-time certification | yes (id122) |
| career-development | Career Development Services | career fairs, internships, job search, resume help | new |
| dean-of-students | Dean of Students | personal emergencies, conduct, problem with a professor/escalation | new |
| oars | Office of Accessibility Resources & Services | disability accommodations | new |
| counseling | Counseling Center (C-CAPS) | mental health, personal emergencies, crisis support | yes (id123) |
| ist | IST / Technology Support Desk | NJIT email, VPN, Wi-Fi, Canvas, password resets | new |

**Authoring rule (senior review N1):** each office doc is a **single section** (H1 + body, no
subheadings) so the section chunker keeps `Handles:` + contact in **one chunk** — a topic query
must retrieve the contact line too.

**Legacy-seed migration, not in-place enrich (senior review C1/S1 — this is a real duplication
bug otherwise).** The 7 seed contacts (`id 122–128`) are filed under the **GSA org** (`org_id=2`),
`created_by='migration'`, with **no `doc_id`/`entity_id`** — so `upsert_doc_items`'s retire clause
(matches `doc_id`/`entity_id`) will **never** retire them, and re-ingesting OGI/Graduate
Studies/Counseling under their own office org would leave **two active copies** (noise the
reranker coin-flips on). Therefore the ingester **explicitly retires** the legacy seed for the 3
offices we re-author, via an explicit id map
`LEGACY_SEED = {"ogi": 125, "graduate-studies": 122, "counseling": 123}` (deactivate those rows,
gated, same transaction). The other 4 seeds (Library, Student Life/Highlander, Wellness,
Inclusive Excellence) are **not** re-authored in this pilot and are left untouched.

Contacts are drafted by fetching the public NJIT office pages for accuracy, then **you verify
every contact + the who-handles-what mapping in one pass** before the gated commit.

### 2. KG consistency + entity capture

- For each office: `ensure_org(slug, name, parent_slug='njit', type='office')` + `sync_org_nodes`
  (graduate-studies/ogi already exist → no-op; others created).
- **Entity capture (standing principle, with a verification guard — senior review N2):**
  - **New NJIT office** encountered → an org node as above (clean, bounded, free).
  - **New relevant person** (an office director / named contact) → captured **into the draft for
    you to verify**, then committed via `people_editor.add_or_edit_person(...)` (Person node +
    `has_role` + contact, `source='dashboard'`, gated). The senior review flagged the real risk:
    blindly auto-adding a name/title scraped off a page imports **unverified, churn-prone** KG
    data (a stale "contact Jane Doe" is worse than "contact the Bursar's office"). So we honor
    "don't drop anyone we find" **but route people through the same one-pass verification as the
    office contacts** — not blind auto-insert. (Pure auto person-capture without verification, and
    a *full* NJIT crawl, stay in the future crawler effort / Spec B, which has turnover reconcile.)

### 3. Answering

Unchanged stack: the question routes to RAG (no new structured skill — RAG-first, leveraging the
reranker), the right office doc is retrieved + reranked, the LLM answers with the office + contact.
A structured topic→office skill is **out of scope** unless the gate shows RAG misses.

## Testing & acceptance gate

`v2/tests/test_office_routing_gold.py` (deterministic, chunk-level, like the rerank gate):
- A frozen `{question → gold office}` map = the ~10 M intents **+ 3–5 author-independent
  paraphrases each** (~40–60 queries; phrase the queries like a student, NOT by reusing the doc's
  own `Handles:` wording, to avoid overfitting — senior review N3).
- **Must include the adversarial overlap pairs (senior review S2 — acceptance criterion, not
  optional):** Career Development vs OGI ("my OPT job search" → career-development), Registrar vs
  Bursar ("registration hold" → registrar; "billing hold" → bursar), Dean of Students vs
  Counseling ("I'm in crisis right now" → counseling). If RAG can't disambiguate these, we add a
  ~20-line topic→office structured skill (mirroring `officers_in_org`) **before** shipping — that's
  the only thing that would flip the RAG-first decision.
- Assert the gold office's chunk is **rank-1 or top-2** (not merely top-5 — "which ONE office" is
  a router answer; top-5 is too loose), matched on an **id-stable token** (office name or email
  like `international@njit.edu`), not a rewordable `Handles:` phrase.
- **0 regressions:** a guard set of existing non-office KB answers (travel award, CS GPA,
  officers) must still retrieve their gold chunk.
- End-to-end smoke: a couple of "which office handles X" questions answer naming the right office.

**Acceptance bar:** every M intent + paraphrase (incl. the overlap pairs) → correct office at
rank ≤2; 0 regressions; you've verified the directory content.

## Error handling / consistency

- All writes gated (`hardened_backup`, dry-run, `--commit`); `source='dashboard'` so the crawler
  never clobbers it; embeddings refreshed via `embed_all.py`; retired chunks pruned via the
  existing prune script. No DB-only contact left unembedded.

## Files

- Create `bot/data/sources/offices/<slug>.md` (one per office, ~10). Each carries front-matter
  `slug`, `name`, `parent` (=`njit`), `type` (=`office`), `source_url`; the body has the
  `Handles:` line + contact.
- Create **`scripts/ingest_offices.py`** (new, gated — mirrors `ingest_office_docs.py`'s safety
  model but **one org per doc**, not one per folder): for each `offices/*.md`, parse front-matter
  → `ensure_org(slug, name, parent_slug='njit', type='office')` → **retire the legacy seed** if
  the slug is in `LEGACY_SEED` (deactivate `id 122/123/125` in the same txn — C1) →
  `upsert_doc_items(org_id, slug=<office slug>, ..., doc_type='contact')` → `sync_org_nodes`.
  Dry-run default, `hardened_backup` on `--commit`, then reminds to `embed_all.py`, then the
  existing prune script removes the now-inactive rows. (The folder→single-org
  `ingest_office_docs.py` doesn't fit because each office is its own org; and it hardcodes
  `doc_type='policy'` — the new one passes `'contact'`.)
- Create `v2/tests/test_office_routing_gold.py` + a `v2/tests/office_gold.py` map.
- (Entity capture) use existing `v2/core/graph/orgs.py` + `v2/core/ingestion/people_editor.py`;
  no new modules.

## Out of scope (separate efforts)
- The other 14 intent categories.
- A full NJIT office/people crawler (future Spec B) — this pilot captures only what it encounters.
- A structured topic→office skill (add only if the gate shows RAG misses).
