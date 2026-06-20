# Scholar URL Discovery Job — design (v2, reviews folded)

**Date:** 2026-06-20
**Status:** DESIGN v2 — both expert reviews + feasibility spike folded in; approved to build (TDD)
**Builds on:** the Scholar refresh job ([[project_external_profiles]], `2026-06-20-scholar-refresh-job-design.md`),
`v2/core/ingestion/scholar.py`, `v2/integration/njit_search.py` (Brave), the Jobs control plane.

## Problem / goal

Most NJIT faculty have **no Google Scholar URL** in the KG (~75 of ~1,076 people have one; ECE 59/60, Applied
Eng 33/34, Math 107/108 lack one). Without a URL the refresh job can't give them metrics or research areas, and
NJIT profile pages rarely list Scholar. Goal: **discover + add Scholar URLs by searching — without EVER
attaching the wrong person's profile** (a wrong write fabricates someone else's citations/research onto a real
NJIT person, rendered deterministically with no answer-time guard).

## Feasibility spike (2026-06-20) — VALIDATED before build

Real Brave API + profile fetches on 5 faculty (Ansari, Zhou, Kondic, Turc, Han):
- Brave returned the person's `scholar.google.com/citations` URL in the top results for **5/5**.
- Fetched profiles show **"Verified email at njit.edu"** + a full affiliation line (incl. department) + interest tags (3/3 NJIT people).
- The verified line shows the **domain only** (`njit.edu`), **NOT the username** → can't match email local-part from Scholar → **unique-surname is the primary disambiguator**.
- A false multi-candidate (2nd "Catalin Turc" hit was actually "Mark Lyon @ unh.edu") is **correctly rejected** by the verified-njit + name gate.
- No captcha on 3 fetches, but volume (200–500) block risk is real → block detection required (see §2).

## The safety principle (the anti-fabrication boundary)

`classify_candidate` **IS the sole anti-fabrication boundary** for this feature — metrics/areas render
deterministically (`profile_fields.render_*` bypass the LLM), so there is **no second line of defense at answer
time**. Therefore the strict (auto-write) bar must be set high:

**STRICT (auto-write)** requires ALL of:
1. candidate profile shows **verified `njit.edu` email** (`#gsc_prf_ivh`), AND
2. **name matches** (precise rule, §2), AND
3. **the person's surname is UNIQUE among active NJIT people** (no homonym) — OR, if not unique, **a second
   corroborating signal** holds: the profile's **department/affiliation text matches the person's home-org**,
   OR **Scholar interests ∩ the person's existing crawler research areas** is non-empty.

**UNCERTAIN (review queue, never written):** name matches + verified njit.edu but surname collides and no
corroboration; multiple candidates that each pass strict; NJIT only in free text (no verified email); bare-initial
first-name; **>1 strict candidate for one person → automatically uncertain (never auto-pick by search rank).**
**BLOCKED:** profile fetch returns a captcha/robot page (no `#gsc_prf_in`) → its own state, NOT "uncertain" (§2).
**REJECT / SKIP:** name mismatch; non-njit verified domain; no candidate found.

## Decisions (locked with Mohammad, 2026-06-20)

Write policy = auto-write strict, queue uncertain, report-only review (v1, no accept/reject UI). Scope = dashboard
"Discover Scholar URLs" job (Data Sources tab), by college/department. Provider = Brave general web search
(server-side subprocess → programmatic Brave, not Claude WebSearch). Out of scope v1: approve/reject UI;
LinkedIn/ORCID; scheduling.

## Architecture (each piece independently testable; search + fetch INJECTED → tests need no network)

### 1. Candidate search — `v2/integration/njit_search.py`
Add `web_search(query, k=5, http_get=_default_get, key=None) -> [url]` — same Brave client/key/`_ENDPOINT` as
`search()` but **without** `site:njit.edu` (separate function → no regression to the scoped live-fallback search).
Same contract: `key` injectable, returns `[]` on any error (missing/exhausted key → skip, never crash). Fetch
top 5 (the Scholar URL may not be #1). [Spike: reliably returns the citations URL.]

### 2. Verify + classify — new `v2/core/ingestion/scholar_discovery.py` (pure; fetch injected)
- `parse_profile_identity(html) -> {name, verified_email_domain|None, affiliation, blocked: bool}` — reads
  `#gsc_prf_in` (name), `#gsc_prf_ivh` ("Verified email at <domain>" → domain), affiliation (`#gsc_prf_il...`).
  **`blocked=True` when `#gsc_prf_in` is absent / a captcha marker is present** (do NOT treat a blocked page as
  "no verified email").
- `name_matches(kg_name, profile_name) -> bool` — **precise rule:** NFKC-normalize, strip accents
  (José≈Jose), collapse punctuation/whitespace, casefold; split "Last, First" on comma; compare `(first, last)`.
  **Require full first-name equality** (after reorder). **A conflicting middle initial = mismatch** (David J. Lee ≠
  David K. Lee); a missing-on-one-side initial is neutral; a **bare-initial first name** ("S. Turc") = weak →
  never strict.
- `surname_is_unique(conn, kg_name) -> bool` — count active people sharing the surname (reuses the
  `persons_by_lastname` logic in `entity.py`). Drives the unique-surname strict gate.
- `corroborates(conn, person_key, identity, interests) -> bool` — department/affiliation match OR
  (Scholar interests ∩ person's existing `researches` areas) non-empty.
- `classify_candidate(conn, person_key, kg_name, identity, interests) -> "strict"|"uncertain"|"blocked"|"reject"`
  per §safety. `discover_for_person(conn, person, *, web_search, fetch) -> {decision, url, reason, html}` — search
  → fetch each candidate → if exactly one classifies strict → strict (return its html for reuse); if ≥2 strict →
  uncertain; else best uncertain; else skip/blocked.

### 3. Orchestrator — `scholar_discovery.run(conn, *, web_search, fetch, org_scope, limit, delay, today)`
- **Targets — `select_discovery_targets(conn, *, org_scope, limit)`** (explicit query, NOT "inverse of"):
  org-subtree via `org_descendants` + `json_extract(o.attrs,'$.org_id') IN (…)`, **`category='faculty'`** roles
  only (NEW constraint — discovery targets faculty, not staff/admin), `e/p/o.is_active=1`, **exclude anyone whose
  `profiles.scholar.url` is set**, DISTINCT keys, capped at `limit`.
- Per target: `discover_for_person`. On **strict** → **no second fetch**: parse `parse_scholar_metrics` +
  `parse_scholar_interests` from the **already-fetched html**, then `set_person_profiles(url + metrics +
  provenance)` and `set_person_research_areas(_home_org_id(...))` directly. On **uncertain** → append to queue
  (no write). On **blocked** → count; **abort the run after N consecutive blocks** (Scholar captcha'd). Polite
  `delay` (default ≥ 3.0s). Hard **per-run Brave-call cap** (stops regardless of target count).
- `_home_org_id` (currently private in `scholar.py`) → make importable (or replicate the home-org lookup).
- **Provenance (reversibility):** strict writes tag the scholar bag: `scholar.discovered_by="auto"`,
  `discovered_at=<date>`, `match_basis="unique_surname"|"dept_match"|"interest_overlap"`. Manual entries leave
  these absent → a bad batch is one query to find + revert (the bag + that person's `source='scholar'` areas).
- Returns `{scanned, written, queued, skipped, blocked, brave_calls, queue:[(key,name,url,reason)], errors}`.
  Does NOT commit (caller owns txn).

### 4. CLI — `scripts/discover_scholar.py` (gated; mirrors refresh_scholar.py)
`--org/--department`, `--limit` (**conservative DEFAULT, e.g. 50** — "All" without a limit can't drain the pool),
`--delay`, `--embed`, `--commit`. Dry-run prints proposed strict writes + the uncertain queue (counts + sample).
Writes the full queue to **`<repo>/logs/scholar_review_<scope>_<date>.csv`** (absolute path so the dashboard can
link it). `--commit` → `hardened_backup`, strict writes, embed new areas (`_embed_cmd`, positional db_path),
still emits the CSV. **Note (documented):** un-actioned uncertain people are re-searched on each run (budget cost);
a `--skip-recently-queued` log is a future refinement, not v1.

### 5. Job plumbing — `bot/services/jobs.py` + `v2/local_server.py` + dashboard
- `build_discover_scholar_command(...)`, `start_discover_scholar(scope, limit, embed)`, `_default_build_cmd`
  branch, **new `_summarize` branch** for "Scholar discovery complete: N written, M queued of P."
- `POST /api/jobs/discover-scholar` (validate scope → 400, coerce limit → 400, 409 busy) — mirrors `_api_refresh_scholar`.
- Dashboard: add **"Discover Scholar URLs"** as a `refresh-what` option + scope dropdown + limit input + Run.
  **Scope counts:** `scholar_scope_list` gains a `mode="discover"` that counts faculty **WITHOUT** Scholar
  (the operator needs to see "(N without Scholar)" = how many will be searched = budget cost), vs the refresh
  job's "(N with Scholar)".

## Error handling / safety (summary)
Strict-only auto-write behind the verified-njit + unique-surname/corroboration gate (the anti-fabrication
boundary). Provenance-tagged for bulk revert. Gated (`hardened_backup`, dry-run, 409). Per-person failure
isolated; **systemic Scholar block → distinct `blocked` state + run abort** (never silent-degrade to "uncertain").
Brave budget protected by default limit + hard per-run cap (shared ~1,000/mo pool with the live fallback).

## Testing (TDD)
- `parse_profile_identity`: name + njit.edu verified email from real-shaped HTML; **`blocked=True` on a captcha page**.
- `name_matches`: "Ghosh, Arnob"↔"Arnob Ghosh" true; "David J. Lee"↔"David K. Lee" **false**; accents; bare-initial weak.
- `surname_is_unique` true for Koutis, **false for Wang/Li/Zhang**.
- `classify_candidate`: verified-njit + unique surname + match → strict; **verified-njit + COLLIDING surname, no
  corroboration → uncertain** (the headline anti-fabrication test); non-njit domain → reject; captcha → blocked;
  ≥2 strict candidates → uncertain.
- **Wrong-person fixture: two active NJIT "Wang"s, a verified-njit profile → MUST be uncertain, never strict.**
- `select_discovery_targets`: faculty-only, excludes people with a URL, org subtree incl. depts, distinct.
- `discover_for_person`: single-strict wins; ≥2 strict → uncertain; reuses fetched html (no 2nd fetch).
- `run`: writes only strict (with provenance), queues uncertain, aborts after N consecutive blocks, respects Brave cap.
- `build_discover_scholar_command` arg mapping; `start_discover_scholar` dispatch; route 409 + scope/limit validation.

## Reviews folded (2026-06-20)
**RAG/anti-fabrication = needs-rework (now addressed):** unique-surname + corroboration strict gate (homonym
blocker — live data: Wang×10, Li×8, 9 shared first+last pairs); precise `name_matches` (conflicting initial =
mismatch); provenance tags for revert; classifier-is-sole-boundary stated; ≥2-strict → uncertain; two-NJIT-Wangs
fixture. **Senior-eng = ship-with-fixes:** explicit faculty-without-scholar selection (+category); no double-fetch
(parse already-fetched html); hard Brave-budget cap + default limit; captcha/block detection + abort; `_summarize`
branch; discover-mode scope counts. **Spike validated Brave reliability + the verified-njit signal.**

## Goals checklist (fill at PR time)
- [ ] Brave un-scoped `web_search` (provider-isolated, [] on error)
- [ ] `parse_profile_identity` (+ blocked detection) / `name_matches` (precise) / `surname_is_unique` / `corroborates` / `classify_candidate`
- [ ] `select_discovery_targets` (faculty-only, no-url, subtree, distinct) + `discover_for_person` (no double-fetch, ≥2-strict→uncertain)
- [ ] `run`: strict-write + provenance tags, uncertain-queue, block-abort, Brave hard cap
- [ ] CLI `discover_scholar.py` (gated, dry-run, review CSV to logs/, default --limit, --embed positional)
- [ ] Job plumbing (build/start/dispatch/route/summary/validation) + dashboard option + discover-mode scope counts
- [ ] **Anti-fabrication: strict gate (verified-njit + unique-surname/corroboration) + two-NJIT-Wangs fixture**
- [ ] Review queue report-only (UI DEFERRED, flagged); LinkedIn/ORCID + scheduling OUT OF SCOPE (flagged)
