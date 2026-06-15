# GSA Wix Extraction Feasibility Spike — Finding

**Date:** 2026-06-15
**Task:** Task 1 of `docs/superpowers/plans/2026-06-15-gsa-kg-kb-foundation.md`
**Method:** read-only fetch of gsanjit.com (project UA), inspect for parseable structure.

## Pages (project UA fetch)

| Path | HTTP | Size |
|---|---|---|
| `/` (home) | 200 | ~1.42 MB |
| `/rgo` | 200 | ~677 KB |
| `/eboard` | 404 | — |
| `/deprep` | 404 | — |
| `/governance` | 404 | — |
| `/the-people` | 404 | — |

The nav labels (E-Board, DepRep, Governance) are **not** URL slugs — those paths 404. The
real content is reached through Wix's client-side router, not stable server URLs.

## Is the roster parseable from the served HTML?

**No.**

- The page is Wix/React: `warmupData`, `wixapps` (×71), `tpaWidgetNative` markers.
- The `"items"` / `"name"` JSON arrays present in the HTML are the **Wix app/widget
  catalog** ("Booking Calendar", "16 Languages Supported", "Blog Posts", …), not a people
  CMS collection.
- Of the 6 known officers, only **Fernando / Buschmann** appears in the served HTML
  (once). **Durvish Paliwal, Mohith Oduru, Nistha Chauhan, Ritwik Kolan do not appear at
  all** — they are rendered client-side after JS execution, so they are invisible to any
  non-headless fetch.

## DECISION: MANUAL

A deterministic crawler is not feasible: the roster is not in a clean embedded JSON/CMS
blob, the page URLs are unstable, and most officers aren't even in the served payload. The
only way to extract would be a headless browser — brittle, heavyweight, and contrary to
the established "crawler is YWCC-only / GSA is manual" principle.

**Therefore:** do **not** build Plan 2 (the Wix crawl adapter). The GSA KG+KB is
provisioned via the manual path built in Plan 1 (`bot/data/gsa_people.yml` +
`bot/data/sources/gsa/*.md` → the gated ingest CLIs), and kept current via the dashboard /
re-running those CLIs. Same KG+KB end state; updates are manual, as anticipated by the
spec's fallback.

## Implication

Plan 1 **is** the whole project for GSA. No Plan 2. Officer turnover (e.g. Mohith leaving
the E-Board) is handled by editing `gsa_people.yml` and re-running `gsa_ingest_people.py
--commit` (the `reconcile_roster` sweep deactivates the departed), or via dashboard edits.
