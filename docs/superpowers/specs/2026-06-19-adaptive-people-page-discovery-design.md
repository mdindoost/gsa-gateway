# Adaptive Supplementary-Page Discovery — Design (task #8, the discovery half)

**Status:** design (pending senior review of SCOPING → gated build)
**Date:** 2026-06-19
**Goal:** every anchored department/college automatically gets ALL its people pages crawled
(/administration, /joint-faculty, …) — not just the one URL in the entry point — without hardcoding
each dept. Reuses the already-built+verified engine (merge titles + M3 union + roll-up-skip).

## What's already done (commit 8b62f3e)
Multi-page-per-org works: merge titles into one edge, M3 union across feeders, skip roll-up sections,
shared accumulator across explore() calls. Proven on CS via explicit CS_ADMIN/CS_JOINT entry points.
This design REPLACES those hardcoded entries with automatic discovery for every dept.

## Mechanism
In `explore()`'s `kind=="listing"` branch, AFTER parsing the main listing, enumerate the org's other
people pages and enqueue them as additional listings **for the SAME anchored org** (same org_slug /
parent / policy), so they share the run accumulator (merge + M3 union + roll-up-skip all apply).

Discovery is a bounded PATH PROBE on the listing's own host — NOT nav-link following (the senior
review flagged nav-following as pulling in centers/labs):
- base = host of the listing URL (cs.njit.edu, ece.njit.edu, …).
- candidate paths (allowlist): `/administration`, `/administration-and-faculty`, `/joint-faculty`,
  `/our-people`, `/people`, `/faculty`, `/staff`, `/leadership` — minus the path we're already on.
- keep a candidate only if it returns ≥1 `people.njit.edu/profile/<slug>` card.

## Scoping guards (the whole risk — review these)
1. **Discovered pages feed ONLY the current anchored org.** They never create new orgs. → preserves
   the MTSM "no department children" invariant (discovery can't mint MTSM departments) and avoids
   treating a research center as an org.
2. **Skip any URL that's already an explicit EntryPoint.** Prevents double-feed: MTSM's
   `management.njit.edu/administration` is the MTSM_ADMIN entry (→ `mtsm-administration`); discovery
   from MTSM_FACULTY (→ `mtsm`) must NOT also crawl it (would feed the wrong org). Compare against
   `ALL_ENTRY_POINTS` urls (+ already-visited urls this run).
3. **Dedup by slug-set, not just URL.** `/people` and `/our-people` often return the SAME roster at
   two URLs — crawl once (skip a candidate whose parsed slug-set equals one already crawled this run).
4. **Bounded allowlist only** (no arbitrary link-following) → can't wander into centers/labs/library.
5. **Politeness:** ~7 extra probes per dept; most 404. Use the existing `--delay`, and short-circuit
   (don't probe a path equal to the page we already fetched). Acceptable one-time cost; re-crawls hit
   the struct_hash skip.

## Removed after this lands
The hardcoded `CS_ADMIN` / `CS_JOINT` entry points (discovery covers them). Verify CS still resolves
identically (admin staff + joint kept, YWCC-grad roll-up skipped) after the switch.

## Verify (dev, per the rules)
Re-crawl a WAL-safe dev copy (backup API, NOT cp — the cp/WAL lesson). Confirm: per-dept people
counts rise where an /administration or /joint-faculty page exists; no dept loses people; no new
orgs created; MTSM still has no department children (`verify_kg`); CS identical to the hardcoded
result; roll-up sections still skipped. Add the new behaviors as questions to eval/questions.txt.

## Open question for review
Should discovery also apply to the HUB's child listings (CS/DS/Informatics reached via the YWCC hub)?
Yes — they're `kind=="listing"` once `child_for` resolves them, so the same branch covers them. Confirm
that's true and that probing e.g. `ds.njit.edu/...` / `informatics.njit.edu/...` is in scope.
