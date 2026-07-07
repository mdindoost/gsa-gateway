# FacultyFolio — surface NJIT "Research interests" prose (Background row)

**Date:** 2026-07-07
**Status:** Design (owner-approved interactively). Delta-spec — mirrors the crawled-awards/service pattern.
**Predecessor:** `2026-07-06-facultyfolio-crawled-research-awards-service-design.md`,
`2026-07-06-facultyfolio-ywcc-departments-hub-design.md`.

## Problem

FacultyFolio's "Areas of focus" shows discrete research-area **chips** — the UNION of a person's
NJIT-crawled area tags and their Google Scholar interest tags (deduped, any `source`). For faculty
whose NJIT "Research Interests" is a **sentence/paragraph** rather than a clean tag-list, the crawler
can't chip-ify it, so it produced **zero NJIT chips** and the chips fall back to Scholar only (e.g.
Mengjia Xu → `Machine learning / Graph Machine Learning / LLMs`). Meanwhile her *actual* NJIT text —

> "Machine learning theory; graph representation learning for diverse applications (e.g., Alzheimer's
> disease early stage detection, human brain aging trajectory detection, climate data modeling, etc.)"

— is stored in the KG (as a `research_statement` prose item) but **FacultyFolio never renders it**.

**Scope of the gap (verified live):** 332 faculty have an NJIT `research_statement`; 115 of them
produced no NJIT chips (65 show no chips at all, 50 show Scholar-only like Mengjia). None of the 332
have their descriptive NJIT text shown anywhere on the page.

## Decision (owner-approved)

- **"Areas of focus" stays exactly as-is — the UNION** of NJIT + Scholar chips. (Making it
  Scholar-only would empty out 135 NJIT-only faculty and strip NJIT tags off 82 more — rejected.)
- **Add a new "Research interests" row to the Background section, right after Education**, showing the
  NJIT `research_statement` **verbatim** (mechanical prefix-strip only). Omit the row when empty.

## Design

### Data source & trust boundary
`knowledge_items` where `type='research_statement'`, `created_by='crawler'`, matched by
`metadata.entity_id = <person key>`. This is crawler-sourced NJIT prose → inside FacultyFolio's
publish rule (crawler-only). It is NOT `type='about'` (LLM bios, HARD-EXCLUDED). Served verbatim per
the NJIT-verbatim hard line. FacultyFolio currently ignores this type entirely.

### `db.py`
- Read the statement: `research_statement_raw = _prose(conn, key, "research_statement")`, exposed on
  the `get_faculty` dict as `"research_statement_raw"` (string, `""` when absent).
- Add `"research_statement"` to `_PROSE_TYPES` so the trust-boundary sanity list (`_prose_types`)
  includes it (still all crawler prose). Update `test_trust_boundary_only_crawler_prose` accordingly.

### `format.py` — `clean_research_statement(raw) -> str`
Mechanical, verbatim after prefix-strip (same family as `format_service` / `format_education`; no
lookup tables, base spec §3.4):
1. `clean_mojibake` (strip U+FFFD, collapse whitespace).
2. Strip the provenance lead-in: `^Research statement of [^:]{1,160}:\s*` (dept-optional; strip to the
   first colon — mirrors `format_service`).
3. Strip a leading label echo: `^Research Interests[:.]?\s*` (case-insensitive).
4. Return the remainder **verbatim** (`""` if nothing remains).

Examples (real rows): `… : Research Interests Math Modeling. Business Risk management` →
`Math Modeling. Business Risk management`; `… (Computer Science): Research Interests Dr. Tsung-Chi
Lin's research focuses on advancing human robot interaction…` → `Dr. Tsung-Chi Lin's research focuses
on advancing human robot interaction…`.

### `render.py` — `about_rows`
Insert the row **after Education** in the `items` list:
`("Research interests", F.clean_research_statement(f.get("research_statement_raw") or ""))`, giving
the Background order: Appointment · Education · **Research interests** · Office · Teaching interests ·
Teaching. Add `"Research interests"` to `_ALWAYS_ADAPTIVE_ROWS` so it is **omitted when empty** even in
Fixed mode (like Teaching interests) — the ~half of faculty without a statement get no "Not listed"
clutter row.

### Templates / CSS
No change. The Background row grid (`120px | 1fr`, `line-height:1.5`) already wraps a multi-sentence
value cleanly; Jinja autoescape covers HTML. No length cap (verbatim; render whatever is stored).

## Non-goals / accepted
- **Redundancy** for the ~217 faculty whose statement echoes their clean-list chips: accepted — the
  prose sits in Background, the chips in Research (different sections, no side-by-side repeat).
- **Areas of focus** logic untouched (still the union).
- Chip-ifying prose interests in the crawler: out of scope (lossy; the `research_statement` prose is
  the honest representation).

## Testing
- `format`: prefix strip (dept-present + dept-absent), label-echo strip, verbatim body preserved,
  empty/`None` → `""`.
- `render.about_rows`: "Research interests" appears immediately after Education when present; omitted
  when empty (even in Fixed mode); value is the cleaned verbatim text.
- `render_profile`: Mengjia (mx6) renders the NJIT sentence in Background AND keeps her Scholar chips
  in Areas of focus; HTML in a statement is escaped.
- `db`: `get_faculty(mx6)["research_statement_raw"]` is populated; `_prose_types` may include
  `research_statement`; still `<= {education, teaching, profile, research_statement}`.
- Rebuild + spot-check a paragraph-statement page (tl459) and a list-statement page (hsieh).

## Generalizability
Reads `research_statement` for any person in any dept/college; no per-person/per-dept vocabulary; no
hardcoding. Applies university-wide the moment those pages are generated.

## Goals checklist
- [ ] Read `research_statement` (crawler) in `db.py`; trust-boundary list updated.
- [ ] `format.clean_research_statement` (mechanical prefix/label strip, verbatim rest).
- [ ] "Research interests" row after Education in Background; always-adaptive (omit when empty).
- [ ] "Areas of focus" union behavior unchanged (regression-guarded).
- [ ] Tests added; rebuild + deploy (batch, one rebuild).
