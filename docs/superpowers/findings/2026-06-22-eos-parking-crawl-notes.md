# EOS / Parking crawl notes — discovered pages + gotchas

**Date:** 2026-06-22
**Context:** Input for the systematic NJIT prose-harvest build (spec
`2026-06-22-njit-prose-harvest-design.md`, main `d35fa69`). Parking = entry-point #1.
These pages were discovered but **not yet fetched/cleaned/ingested** — only the `/parking/` hub
itself was fetched (cleanly, ~67 KB). The per-sub-page fetch was never run live.

## Key gotcha (the reason the existing sitemap pipeline can't reach this)

`https://www.njit.edu/parking/` is a **separate Drupal multisite (`njit.edu.parking`)** and is
**absent from both NJIT sitemaps** (`www.njit.edu/sitemap.xml`, `catalog.njit.edu/sitemap.xml`)
— verified 0 `/parking` URLs. So `_crawl_stage.py --bucket parking` stages **zero** pages. The
content is only reachable by **seeding the hub and following its links** (depth 1).

Other gotchas:
- **Cross-site, same-host links:** Mailroom / Sustainability / EHS / campus-transportation live
  on `www.njit.edu` (NOT under `/parking/`), e.g. `/mailroom/`, `/sustainability/`,
  `/environmentalsafety`, `/about/transportation-campus`. A same-*host* filter keeps them; a
  same-*path-prefix* filter would miss them — the follow set needs those prefixes explicitly.
- **Asset links to drop:** the hub emits Drupal CSS-include `<a href>`s ending `.css?delta=…`.
  `web_crawler.is_non_html` does NOT cover `.css/.js/.ico`, so the follow helper must drop those
  extensions separately (the worktree `select_seed_links` does).
- **Likely JS-only / off-host shells (UNVERIFIED — fetch each to confirm):** the SchoolDude
  Work-Order portal, the "parking availability app", and possibly the visitor-parking app are
  typically SaaS SPAs that `clean_text` returns empty for → must be skipped + flagged, not faked.
- **PDFs** (e.g. the Late-Night-Lyft announcement) are linked; out of scope for HTML extraction.

## Discovered pages (from the hub's outbound links)

Hub: `https://www.njit.edu/parking/`

### `/parking/*` sub-pages
- https://www.njit.edu/parking/2026-summer-hours
- https://www.njit.edu/parking/additional-njit-parking-available-essex-county-college-0
- https://www.njit.edu/parking/administrative-center-visitor-parking-494-broad-street
- https://www.njit.edu/parking/campus-parking-map-0
- https://www.njit.edu/parking/daily-parking-options-students-employees
- https://www.njit.edu/parking/daily-parking-options
- https://www.njit.edu/parking/department-announcements
- https://www.njit.edu/parking/electric-vehicles
- https://www.njit.edu/parking/employee-parking-fees
- https://www.njit.edu/parking/employee-transportation-tax-savings-opportunity
- https://www.njit.edu/parking/event-game-day-parking
- https://www.njit.edu/parking/facilities-systems-contacts
- https://www.njit.edu/parking/facilities-systems-photo-identification-and-parking-services
- https://www.njit.edu/parking/free-tire-air-pump-available
- https://www.njit.edu/parking/late-night-lyft-program
- https://www.njit.edu/parking/mobile-credential
- https://www.njit.edu/parking/nest-research-lyft-program
- https://www.njit.edu/parking/newark-street-parking
- https://www.njit.edu/parking/nj-transit-newark-light-rail
- https://www.njit.edu/parking/nj-transit-student-discount-program
- https://www.njit.edu/parking/njit-administrative-center-parking
- https://www.njit.edu/parking/njit-card-access
- https://www.njit.edu/parking/njitrutgers-shuttle-buses
- https://www.njit.edu/parking/overnight-parking
- https://www.njit.edu/parking/parking-0
- https://www.njit.edu/parking/parking-availability-app
- https://www.njit.edu/parking/parking-venturelink-client-employees
- https://www.njit.edu/parking/parking/special-events-bus-parking
- https://www.njit.edu/parking/photo-id-card-instructions-new-students
- https://www.njit.edu/parking/photo-identification
- https://www.njit.edu/parking/security-systems
- https://www.njit.edu/parking/transportation
- https://www.njit.edu/parking/visitor-parking
- https://www.njit.edu/parking/zipcar-njit

### Cross-site, same-host (www.njit.edu) — EOS service areas off `/parking/`
- https://www.njit.edu/mailroom/
- https://www.njit.edu/sustainability/
- https://www.njit.edu/environmentalsafety
- https://www.njit.edu/about/transportation-campus
- https://www.njit.edu/campus-parking-maps
- https://www.njit.edu/life/transportation-parking
- https://www.njit.edu/transportation-parking

### Suggested seed-mode invocation (worktree `_crawl_stage.py`)
```
python scripts/_crawl_stage.py \
  --seed https://www.njit.edu/parking/ \
  --follow '/parking/,/mailroom,/sustainability,/environmentalsafety,/about/transportation,/campus-parking-maps,/life/transportation-parking,/transportation-parking' \
  --prefix eos
```

## What exists in the worktree as reusable input
- The seed/link-follow staging mode (`select_seed_links`, `stage`, `_slug`) — reusable for any
  non-sitemap NJIT site, not just parking.
- The per-doc `org:` ingest support and the `operations` heads-up topic.
- Design spec `docs/superpowers/specs/2026-06-22-eos-parking-knowledge-design.md` (the
  ad-hoc design now superseded by the systematic prose-harvest spec).
