# Person-Facet Questions — Delta Design

- **Date:** 2026-07-08
- **Branch:** `feat/processing-debt-pilot`
- **Status:** DRAFT — focused Fable review (delta only), then build. Owner-directed pivot.
- **Parent spec:** `2026-07-07-person-entity-mentions-tagging-design.md` (the tagger + `entity_mentions`
  table + gate are UNCHANGED and already built/reviewed/green — this delta changes only SERVING).

## 1. What changed and why (owner decision)

The parent spec surfaced owned prose by **appending it to the person card** ("who is X"). Live
testing showed the real cost: the Oria bio is 1,389 chars, overflows Discord's 2,000 cap, and
~half **duplicates** the card. Owner's better idea:

> "Keep current answers as-is. Add MORE answers — 'Oria news', 'Oria awards' — rather than
> stuffing everything into 'who is Oria'."

So we **do NOT touch the card.** The `entity_mentions` tags instead power **new person-facet
questions**, each its own clean answer with room for the full verbatim prose. This is the
LOCATE-vs-ANSWER idea: a new question *locates* the right owned prose deterministically.

**Supersedes:** the card-addendum serving (`build_person_addendum`/`render_addendum` +
`message_handler` addendum wiring). `PERSON_ADDENDUM_ENABLED` defaults **OFF** (card unchanged);
that path is retained inert and may be removed in cleanup. The DATA queries it introduced are
reused by the facet skills.

## 2. Scope — 4 new facets (owner-confirmed 1–4; 5–6 deferred)

| # | Question | Data source | New skill |
|---|----------|-------------|-----------|
| 1 | "X news" / "news about X" | `entity_mentions` ⋈ items where `type IN (news,event_info)` | `news_of_person` |
| 2 | "X awards" / "what has X won" | id-linked `award` rows (dropped entirely today) | `awards_of_person` |
| 3 | "tell me more about X" / "X bio" | curated `faq` bio via `entity_mentions` (title-match), else crawler `about` | `bio_of_person` |
| 4 | "what is X involved in" / "X's workshops/service" | roll-up: all `entity_mentions` items + `service` rows | `involvement_of_person` |

**Deferred (loud):** teaching/courses + education direct-answers (data is in the card already);
topic/office/college tagging; students/staff (parent spec phases).

## 3. Routing (v2/core/retrieval/router.py)

Add four cue regexes and dispatch in `_person_skill(q)` (the existing contact-vs-title-vs-card
chooser, router.py:613) BEFORE the card default. Precedence (most specific first):
`awards → news → involvement → bio → contact → title → entity_card`.

```python
_AWARDS_CUE      = re.compile(r"\b(award|awards|honou?rs?|prize|prizes|recognition|won|received)\b", re.I)
_NEWS_CUE        = re.compile(r"\b(news|latest|recent|announcement|announced|in the news|headline)\b", re.I)
_INVOLVEMENT_CUE = re.compile(r"\b(involved|involvement|workshop|workshops|committee|committees|service|organi[sz]e|organizing)\b", re.I)
_BIO_CUE         = re.compile(r"\b(bio|biography|background|tell me (more|about)|more about|about)\b", re.I)
```

`_person_skill` gains the four branches. The person-branch TRIGGERS at router.py:911/925 already
fire on `_PERSON_ATTR`/`_INFO_CUE`; add the four cues to those trigger sets so "Oria news",
"Oria awards", "Oria involvement", "tell me more about Oria" enter the person branch and resolve
the name via the existing `_resolve_person`/`_resolve_surname`. **No change to name resolution.**
`_with_origin` tagging extended so a disambig resume re-runs the originally-asked facet.

Guard: `_BIO_CUE` contains bare "about", which is broad — it is LAST in precedence and only fires
inside the already-person-gated branch (a resolved name present), so "about GSA events" (no person)
never routes here.

## 4. Skills (v2/core/retrieval/entity.py) — all DETERMINISTIC & VERBATIM

Each returns a dict; empty → `format_answer` returns "" → falls through to RAG (honest, never
fabricate). All four go in `_DETERMINISTIC_SKILLS` (no LLM rewording — prose/lists are verbatim).

- `awards_of_person(conn, entity_id) -> {name, awards: [str]}` — id-linked `award` titles, drop
  bare-year rows, desc-year (reuse the parent spec's award query).
- `news_of_person(conn, entity_id, limit=5) -> {name, items: [{title, url, date}]}` — `entity_mentions`
  ⋈ items `type IN (news,event_info)`, live `is_active=1`, newest first, cap `limit`.
- `bio_of_person(conn, entity_id) -> {name, text, url}` — the single best curated bio: prefer a
  `faq` tagged by `match_basis='title'`; else a crawler `about` row. Verbatim.
- `involvement_of_person(conn, entity_id) -> {name, items:[{title,url,kind}]}` — union of all
  tagged items (faq/news/event) + `service` rows, deduped, each with a source link.

## 5. Rendering (structured_answer.format_answer)

Verbatim, no greeting (deterministic). Honest-empty when nothing on file. Examples:
- awards: `"{name} — awards & honors: {'; '.join(titles)}."` / empty → `"I don't have awards on file for {name}."`
- news: `"{name} in the news: {'; '.join(f'{t} ({url})')}."` / empty → `"I don't have news about {name} on file."`
- bio: the verbatim `text` (+ source link if present) / empty → `""` (RAG).
- involvement: `"{name} is involved in: {'; '.join(...)}"` / empty → honest line.

Length: standalone answers; still platform-capped for lists (cap items, "+N more"); the single
bio block fits both platforms as its own message (~1.4k). Prose never truncated mid-text.

## 6. Non-goals / invariants held
- Card + all existing person skills UNCHANGED (owner hard requirement).
- `entity_mentions` tagger/table/gate UNCHANGED (already built + reviewed).
- Anti-fabrication: skills read owned rows only; deterministic verbatim; empty→honest/ RAG.
- RAG untouched (coverage floor).
- `PERSON_MENTIONS_ENABLED` no longer needed for the card; the facet skills gate on data presence
  (a new flag `PERSON_FACETS_ENABLED` default ON is optional — facets are additive & honest-partial,
  low risk; decide with Fable).

## 7. Tests (TDD)
- Router: "Oria news"→news_of_person, "Oria awards"→awards_of_person, "tell me more about Oria"→
  bio_of_person, "what is Oria involved in"→involvement_of_person; "who is Oria"→entity_card
  (UNCHANGED); "about GSA events" (no person)→ not a facet.
- Skills: awards drop bare-year; news newest-capped + is_active filter; bio prefers title-match faq;
  involvement dedups; each honest-empty.
- Regression: card answer byte-identical; existing person skills unchanged.
- Gold/eval: add the 4 facet questions to eval/questions.txt.

## 8. Goals checklist (filled at build end)
- [x] news_of_person · [x] awards_of_person · [x] bio_of_person · [x] involvement_of_person
      (entity.py, commit e138d5f; 6 skill tests + 13 serving tests green).
- [x] router cues + _person_skill dispatch (router.py: `_AWARDS/_NEWS/_INVOLVEMENT/_BIO_CUE`,
      `_FACET_CUE` trigger, `_facets_on()` gate, precedence awards→news→involvement→bio; 32 router
      tests green incl. F1 hard gate + F2 shadowing).
- [x] deterministic verbatim render (all 4 in `_DETERMINISTIC_SKILLS`, structured_answer commit e23e65a).
- [x] card unchanged (`who is`/`tell me about X` → entity_card, pinned by test).
- [x] `PERSON_FACETS_ENABLED` default ON kill switch (F4); `PERSON_ADDENDUM_ENABLED` flipped to
      default OFF (pivot retires the card-addendum path).
- [x] eval/questions.txt: 4 facet questions added under "person facets".
- [ ] DEFERRED (loud): teaching/education facets; students/staff phases; PHYSICAL removal of the
      inert card-addendum code (`build_person_addendum`/`render_addendum` + message_handler wiring) —
      left inert this release, deleted in cleanup.

### Deviation from §3/§9 (recorded)
`_BIO_CUE` **drops bare "about" and bare "tell me about"** (spec §3 listed them). Reason: those
queries are person-gated AND resolve a name, so precedence-last does NOT save them — "tell me about
Oria" is the canonical CARD query and MUST stay `entity_card` (owner hard requirement: "keep current
answers as is"). Bio now fires only on `bio|biography|background|tell me more about|more about`. This
STRENGTHENS the card-unchanged invariant; pinned by `test_tell_me_about_still_entity_card`.

---

## 9. REVISION — BINDING (folds Fable SHIP-WITH-CHANGES, 2026-07-08)

- **F1 (hard gate):** TDD MUST pin that `_extract_area` mines NONE of the cue words
  (news, awards, honors, prize, recognition, involvement, workshop, committee, service,
  organize, bio, biography, background) as a research area — else the query routes to a
  research skill BEFORE facet dispatch. First assertion written.
- **F2:** negative routing tests that `recent`/`latest` stay SHADOWED by the more-specific
  paper/research branches ("Oria's latest paper"→papers; "Oria recent research"→research);
  news wins only when nothing more specific matched.
- **F3:** deterministic/verbatim, NO warm compose (all 4 in `_DETERMINISTIC_SKILLS`). A static,
  non-LLM lead-in ("Here's more about {name}:") is allowed (constant, not composition).
- **F4:** gate `PERSON_FACETS_ENABLED` default **ON** (it changes routing → needs a kill switch;
  off = prior card/RAG behavior). One bool.
- **F5 build details:**
  - news ordering: `ORDER BY COALESCE(date, created_at) DESC, k.id DESC` (total order on NULL dates).
  - ALL facet joins filter `k.is_active=1` AND `n.is_active=1`; carry the parent rowid-drift guard
    (join validates via `stable_key`; re-confirm the person's surname whole-word in the served item).
  - `involvement_of_person` dedup key = `COALESCE(natural_key,'id:'||id)` (an item that's tagged
    AND a service row must not double).
  - `bio_of_person` tie-break on >1 title-match faq: lowest `id` (stable).
  - Empty-handling ASYMMETRY (pin each in tests): `bio` empty→`""`→RAG; `awards`/`news`/
    `involvement` empty→honest "none on file" line (does NOT reach RAG). Accepted caveat: a
    mis-resolved person gets an honest "no awards on file" instead of RAG — rare + honest.
  - `bio` served as its OWN standalone answer (~1.4k), never concatenated to the card.
