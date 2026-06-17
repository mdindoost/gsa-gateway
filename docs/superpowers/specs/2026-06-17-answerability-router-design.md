# High-Stakes Heads-Up + Router Precision — Design

**Date:** 2026-06-17
**Status:** IMPLEMENTED (2026-06-17). (simplified from the earlier cross-encoder answerability-gate design after
discussion — see "Design evolution" below). Self-reviewed; heavyweight senior review skipped by
agreement (the increment is now small and deterministic).
**Relates to:** `2026-06-16-rerank-retrieval-design.md`, `project_day_to_day_intents` (the
retrieval-safety step before scaling the KB to the 150 day-to-day intents, many high-stakes).

## Problem

Two issues the post-rerank eval exposed:
1. **High-stakes topics need a guardrail, not a guess.** As we add day-to-day content
   (visa/I-20/CPT/OPT, tuition/billing, funding), the bot must never present itself as the
   authority on immigration or money — rules change and the official offices own them. The bot
   should answer with what it has **and always tell the student to confirm with the authority**.
2. **Router over-trigger.** "Who can **impeach** a GSA **officer**" is hijacked by the
   `officers_in_org` skill (bare "officer" matches), returning the officer roster instead of the
   constitution's impeachment rule.

## Design evolution (why this is small)

We first designed a cross-encoder *answerability gate* (decline+route when retrieval is weak),
then a senior review showed the CE score isn't cross-query calibrated, pushing it toward a
margin signal + calibration. In discussion we concluded that for the actual need — **high-stakes
topics** — a confidence score is the wrong tool: you want to **always** defer those to the
authority regardless of confidence. So the gate is dropped in favor of a small curated
**heads-up table**. "Topics we don't cover yet" handling is also dropped (YAGNI). What remains is
deliberately tiny and deterministic.

## Component A — High-stakes heads-up

A small **topic table** (data, in one module): each entry = `(name, patterns, office, headsup)`.

| name | patterns (case-insensitive, word-ish) | office (headsup target) |
|---|---|---|
| immigration | visa, i-20, i20, cpt, opt, sevis, f-1, f1, work authorization, immigration status | Office of Global Initiatives (OGI) |
| billing | tuition, bill, billing, payment, bursar, refund, financial hold, late fee, fees | Office of the Bursar / Student Accounts |
| funding | assistantship, stipend, fellowship, teaching assistant, research assistant, TA position, RA position, tuition waiver | Office of Graduate Studies / your department |

**Behavior:** we still **answer normally** (retrieve + generate). After producing the answer in
the RAG path, if the *question* matched a high-stakes topic, **append a one-line heads-up** for
the first matched topic:

> ⚠️ *This is based on the GSA's knowledge — please confirm with the {office}, since these rules
> can change and they're the official authority.*

- Match is on the **question text** (not the answer), keyword/phrase based, same spirit as
  `intent_detector` patterns. Patterns are reasonably specific to limit false matches; a spurious
  "confirm with the Bursar" note is harmless (errs toward caution), so precision isn't critical.
- Applied **once**, first matching topic wins (order: immigration, billing, funding).
- Appended whether the answer came from the LLM or the raw-chunk fallback (any real answer in the
  RAG branch). Not appended to structured-router answers (those are GSA-internal facts:
  officers/areas/etc.) or to the "no information found" deflection.
- Lives in a small helper, e.g. `bot/core/headsup.py` (`match_topic(question) -> Topic | None`,
  `headsup_line(topic) -> str`), called from `message_handler`. Single responsibility, unit-testable.

## Component B — Router precision (`v2/core/retrieval/router.py`)

The `officers_in_org` route fires whenever an officer term appears anywhere + an org resolves.
Fix with a **positive identity requirement** (not a verb denylist, which leaks "who can
*nominate*…", "how is an officer *chosen*…"): route to `officers_in_org` only when the question
matches an identity ask —
- `who (is|are|'s) [the] … <officer-title>` (President / VP … / officers / e-board), or
- `(list|name|show) [the] … officers`.

Everything else falls through to RAG (the router's existing safe default), so any process
phrasing ("who can impeach a GSA officer", "what are the duties of the VP", "how do I become an
officer", "who is eligible to be an officer") is answered from the constitution via RAG. Must
keep the existing `test_router_officers.py` positives passing and cover contractions ("who's the
VP of Finance") and bare "list the GSA officers".

## Error handling

- No new failure modes. The heads-up is a pure suffix; if `match_topic` returns None, nothing
  changes. The router change only ever makes routing *more* conservative (RAG default).
- No kill-switch / setting (YAGNI — a one-line disclaimer on matched topics needs no toggle).

## Testing (deterministic)

**Component A — `bot/tests/test_headsup.py`:**
- match: "how do I apply for CPT" → immigration/OGI; "how do I pay my tuition" → billing/Bursar;
  "how do I apply for a TA position" → funding/Grad Studies.
- no match: "who are the GSA officers", "what is the travel award", "when is the next event" → None.
- `headsup_line(immigration)` contains "Office of Global Initiatives".
- handler-level: a high-stakes question's response **ends with** the heads-up; a normal GSA
  question's response does **not** contain a heads-up.

**Component B — `v2/tests/test_router_precision.py`:** parametrized
- routes → `officers_in_org`: "who are the GSA officers", "who is the GSA president",
  "who's the VP of Finance", "list the GSA officers".
- falls through (None): "who can impeach a GSA officer", "what are the duties of the VP of
  Finance", "how do I become a GSA officer", "who is eligible to be an officer".
- existing `test_router_officers.py` still green.

**Acceptance bar:** both test files green; end-to-end smoke — a visa/CPT/tuition question now
ends with the heads-up; "who can impeach…" now answers from the constitution.

## Files

- Create `bot/core/headsup.py` — topic table + `match_topic` / `headsup_line`.
- Modify `bot/core/message_handler.py` — append heads-up after generation in the RAG branch.
- Modify `v2/core/retrieval/router.py` — positive-identity officer rule.
- Create `bot/tests/test_headsup.py`, `v2/tests/test_router_precision.py`.

## Out of scope (separate increments)
- Cross-encoder answerability gate / "topics we don't cover yet" decline-and-route (dropped).
- Generation-quality misses (right chunk retrieved, LLM flubbed).
- Smart per-topic office *routing/answers* (the cat-M office-routing pilot) — this heads-up table
  is a small seed of that office map.
