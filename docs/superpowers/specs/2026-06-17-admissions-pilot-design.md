# Admissions Content Pilot (Categories A/B/C, partial E) — Design

**Date:** 2026-06-17
**Status:** IMPLEMENTED (2026-06-17). Design established by precedent (reuses the international
pilot pipeline verbatim); content-first build driven by the maintainer.
**Relates to:** `project_day_to_day_intents` (3rd content pilot), `2026-06-17-international-pilot-design.md`
(identical pipeline), `2026-06-17-office-routing-pilot-design.md` (University Admissions org + 26
staff already captured).

## Goal
Answer the graduate-admissions intents (A: how to apply; B: application status; C: test
scores/English; partial E: I-20 timing, financial aid) with overview + route-to-Admissions
content, from the NJIT graduate-admissions page (maintainer-provided + verified).

## Design (by precedent — no new mechanism)
- **Content:** `bot/data/sources/admissions/<slug>.md`, overview style, drafted from the NJIT
  "How to Apply for Graduate Admission" page and verified by the maintainer.
- **Ingest/KG:** `admissions` folder → the existing **University Admissions org** (slug
  `graduate-admissions`) via `ingest_office_docs.py`; section chunker, per-section `entity_id`,
  `source='dashboard'`, `doc_type='policy'`, gated, embed, prune.
- **Answering:** RAG + rerank (no heads-up — admissions isn't immigration/billing/funding).
- **Gate:** `v2/tests/test_admissions_gold.py` — fast, chunk-level, intents at rank ≤2 + a guard
  set (incl. international, to prevent cannibalizing OPT/visa routing).

## Docs (4)
| slug | covers |
|---|---|
| `how-to-apply` | online application, one-program-at-a-time, $75 fee, checklists (transcripts, rec letters — 3 for PhD, SOP, resume, portfolio) |
| `test-scores-english` | GRE/GMAT by college (YWCC PhD requires it; MTSM 3.0 waiver), English minimums (TOEFL 79, IELTS 6.5, Duolingo 120, PTE 57) |
| `application-status-faq` | connect.njit.edu/apply, no phone decisions, unofficial docs, change-program-after-1-year, non-matriculated 9 cr (not F-1/J-1), auto financial-aid, I-20 timing |
| `collaborative-phd` | part-time employer-collaborative Ph.D. (traditional + employer-sponsored tracks) |

## Out of scope
- Full E (accepting offer / deposit / UCID / email / deferral) — the admitted-students page was
  not provided; ship A/B/C now.
- A full crawler (future Spec B).
