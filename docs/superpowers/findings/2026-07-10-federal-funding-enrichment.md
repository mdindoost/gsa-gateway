# Federal Research-Funding Enrichment (NSF + NIH) — findings + tool

**Date:** 2026-07-10 · **Status:** LIVE on YWCC · **Tool:** `scripts/funding_enrich.py`

Adds per-faculty federal research funding to the knowledge graph as additive `attrs.funding.<source>`
bags. Data-bringing only (per the crawl-brings-data hard line) — serving/rendering is separate.

## Sources landscape (decided)

| Source | Coverage | Per-faculty? | Dollars? | Status |
|---|---|---|---|---|
| **NSF Award Search** | CS / engineering / physical sci | yes, clean | yes (obligated) | ✅ integrated |
| **NIH RePORTER** | health / bio / medical / bioeng | yes, clean | yes | ✅ integrated |
| USASpending.gov | ALL federal (DOD/DARPA/DOE/NASA/USDA) | **no — institution-level only** | yes (NJIT total) | not per-faculty |
| **OpenAlex `grants`** | any funder in paper acknowledgments | yes, PI-attributed | usually **no $** (funder + award-id) | deferred to OpenAlex build |

**Decision:** NSF + NIH are the only clean, dollar-precise, per-faculty federal APIs. DOD/DARPA/DOE/NASA
fund NJIT but publish no PI-level API — their dollars live in USASpending at the *institution* level
only. OpenAlex `grants` can attribute those funders to a person (from paper acks) but typically without
dollar amounts, so it becomes a **funder-breadth** signal in the OpenAlex build ("$X NSF · $Y NIH · also
funded by DARPA, DOE"), not a dollar source.

## Matching gate (validated on YWCC, zero fabricated matches)

- Query by **full name**; candidate = **field-aware** match (surname ∈ last-name field AND given ∈
  first-name field). Pooling the fields leaks homonyms (e.g. "DONG, ZHI-WEI" → "Zhi Wei"), so matching
  is strictly per-field.
- **Lecturers excluded** (faculty-edge title contains "lecturer") — teaching track, no research grants.
- **NSF** identity: an `@njit.edu` award email OR `awardeeName = NJIT`. Attribution: only `awardeeName =
  NJIT` awards count toward the total; prior-institution awards are kept, flagged `at_njit=false`, and
  excluded (a moved-in PI's previous-institution grants must not be credited to NJIT — e.g. Bader's
  Georgia Tech tail exceeds his NJIT total). Harvests the NSF email alias when its local-part shares a
  name token. `>1` distinct njit email under one name → review (homonym signature).
- **NIH** is simpler: the `org_names = NJIT` filter gives identity + attribution in one query. Matches on
  the `PrincipalInvestigators` list (field-aware). `njit_total` counts **contact-PI** projects only
  (co-PI projects listed, `role`-tagged, excluded — no cross-faculty double-count). Summed per
  `core_project_num`; `exclude_subprojects=true` prevents P01/U54 parent+child double-counting. `>1`
  distinct NIH `profile_id` → review.

## Stored shape

```
attrs.funding.nsf  = {updated_at, njit_total, matched_by, awards:[{id,title,awardee,start,exp,obligated,at_njit}]}
attrs.funding.nih  = {updated_at, njit_total, matched_by:"org+name", projects:[{core,title,total,role,fy_first,fy_last}]}
attrs.email_aliases = [{email, source:"nsf", added}]   # crawled attrs.email untouched
```
Honest-partial: funding is per-agency — label "NSF awards" / "NIH projects", never a false grand "total
funding". NSF `njit_total` (obligated-to-date) and NIH `njit_total` (summed FY costs) are not
apples-to-apples — never sum the two into one figure.

## Tool usage (gated, idempotent, reversible)

```
python scripts/funding_enrich.py --org ywcc                 # dry-run, both sources
python scripts/funding_enrich.py --org ywcc --commit        # gated live write (hardened_backup first)
python scripts/funding_enrich.py --org ywcc --source nih    # one source
python scripts/funding_enrich.py --org ywcc --only wei      # targeted single-person re-run
```
Dry-run default; `--commit` takes a WAL-safe `hardened_backup` before writing; writes batched after the
network loop (no long write-lock); re-runs converge (funding overwritten as a dated snapshot, aliases
dedup). **WAL note:** a plain `cp` of the live DB for a dev copy misses commits still in the `-wal`
file — use the online-backup API (or just run the gated tool live).

## YWCC result (2026-07-10, live)

NSF 35 faculty $37,401,075 · NIH 2 faculty (Wei, Perl) $6,076,611 = **$43.5M federal across 36 faculty.**
Zhi Wei is the proof-point: NSF flagged him "no funding" but NIH shows $1.65M — multi-source is required
to avoid showing a funded professor as unfunded.

## Follow-ups

- Scale to other colleges: `funding_enrich.py --org <slug>` per college (same gate; per-college review tail).
- Recurring "update code": staleness-gated refresh + dashboard "Data Sources" job button.
- Rendering on FacultyFolio profiles + dept/college funding rollups: separate funding-rendering spec.
