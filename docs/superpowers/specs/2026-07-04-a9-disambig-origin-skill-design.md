# A9 — person_disambig resumes the ORIGINALLY-asked skill (micro-spec)

**Date:** 2026-07-04
**Status:** DRAFT → Fable design-review → build (TDD) → Fable diff → ship
**Split out of:** the accuracy quick-wins batch (`2026-07-04-accuracy-quick-wins-design.md`), QW-A9 — Fable
found it under-scoped for a quick win (7+ route-creation sites across two files).

## Problem
When a person query is ambiguous (multiple people match), the router returns a `person_disambig` Route.
On resume (the user picks a candidate), `structured_answer.resumable_action` hardcodes
`Route("entity_card", {entity_id})` for EVERY option (`structured_answer.py:535-537`) — so the originally
asked question is discarded. "What's **Wang**'s h-index?" → disambig → user picks "Guiling Wang" → gets a
**bio card**, not the h-index. Worse: a metric answer would have been DETERMINISTIC (rule #3), but the card
path is composed. Live-visible: `FOLLOWUP_RESUME_ENABLED=1`.

## Fix — carry the originating skill as `origin` on the disambig Route
A `person_disambig` Route already carries `{"candidates": [...]}` in its args. Add an optional
`"origin": {"skill": <str>, "args": <dict-without-entity_id/name>}` recorded by the PRODUCER (which knows
what skill it would have run), and rebuilt on resume.

### Producer sites (attach `origin`)
Both KG producers flow their `Route.args` through the live UnifiedRouter verbatim
(`unified_router.resolve_kg`: `RouteDecision(..., args=dict(rt.args))`), so an `origin` in the disambig
args survives to `_register_and_record` → `resumable_action`.

- **`v2/core/retrieval/router.py`** — at each caller that returns a `_resolve_person`/`_resolve_surname`
  disambig Route, attach the origin for the skill that caller would run on a resolved person:
  - papers (`~:517`) → `papers_of_person` `{mode, n}`
  - trend (`~:537`) → `citation_trend_of_person` `{mode, year}`
  - metric (`~:621`) → `metric_of_person` `{field_key, metric_key}` (confirm exact args at build)
  - link (`~:633`) → `link_of_person` `{field_key}`
  - research (`~:734`) → `research_of_person` `{}`
  - named-multi disambig (`:753`) → `_person_skill(q)` `{}`  (NOT blanket entity_card — matches its
    resolved-single sibling at :749 which runs `_person_skill(q)`) — **Fable req #2**
  - **surname-only branch (`:763-765`) → `_person_skill(q)` `{}` — Fable req #1 (SPEC ORIGINALLY MISSED
    THIS; it's the "Wang's email"/two-Wangs case: no full name → `named` empty → :751/:753 skip → this
    branch. High-value.)** Sibling resolved-single at :767 runs `_person_skill(q)`.
  - the shared resolver returns at `:472`/`:484` stay skill-agnostic — origin attached at their CALLERS only.
  - **Fable req #3 — trend site (`:537`): HOIST the `mode`/`year` computation (:541-542, q-only:
    `_peak_cue`/`_growth_cue`/`_year_in` already in scope :532-535) ABOVE the disambig return at :538 so
    origin.args can capture them. papers (`:517`): `_paper_mode(q)`/`_parse_topn(q)` are q-only → call at
    the disambig site, no hoist needed.**
  - **Third KG path noted (Fable): `unified_router.py:121 fast_path` also does `dict(rt.args)` → covered
    for free (same route() producer).**
- **`v2/core/retrieval/slot_extractor.py`** — the 4 disambig sites, where the `skill` variable is in scope:
  - `:394` (WS2 skills — incl. `research_of_person`, `entity_card`) → `skill` `{}`  (Fable nit: research
    resolves HERE, not :413)
  - `:413` (WS3 person-attribute skills → contact_of_person / title_of_person) → `skill` `{}`
  - `:446` (metric) → `metric_of_person` `{field_key, metric_key}`
  - `:457` (link) → `link_of_person` `{field_key}`

DRY helper (one place, both files import or each defines a local twin):
```python
def _with_origin(route: Route, skill: str, args: dict) -> Route:
    """Tag a person_disambig Route with the skill that produced it so the resume runs the ASKED
    question, not a generic bio card. No-op if `route` isn't a person_disambig (defensive)."""
    if isinstance(route, Route) and route.skill == "person_disambig":
        route.args.setdefault("origin", {"skill": skill, "args": dict(args or {})})
    return route
```

### Resume (rebuild the asked skill)
`v2/core/retrieval/structured_answer.py:535-537`:
```python
if skill == "person_disambig":
    cands = rt.args.get("candidates", [])
    origin = rt.args.get("origin")
    if origin:
        return [(c["name"], Route(origin["skill"],
                                  {**origin["args"], "entity_id": c["entity_id"], "name": c["name"]}))
                for c in cands] or None
    return [(c["name"], Route("entity_card", {"entity_id": c["entity_id"]})) for c in cands] or None
```
Fallback to `entity_card` when no origin (a bare "who is Wang" disambig SHOULD resume as a card).

## Invariants / safety
- **Display unaffected:** `format_answer(person_disambig)` ignores `origin` (only reads `candidates`) — the
  "which one did you mean?" text is byte-identical.
- **Resolver stays skill-agnostic:** `_resolve_person`/`_resolve_surname` never learn the skill; origin is
  attached by the caller only. No shared-state coupling.
- **`setdefault`** so a caller can't double-tag; first (most specific) producer wins.
- **`_structured_from_route` on resume** runs `origin.skill` with `{**origin.args, entity_id, name}` — the
  resolved person's id/name override any (absent) placeholders. Metric/link resumes are then DETERMINISTIC
  again (rule #3), not composed.
- Gated by `FOLLOWUP_RESUME_ENABLED` (the whole resume path); flag-off = today's behavior (never reached).

## Tests (TDD)
Unit (`v2/tests/` or `bot/tests/`):
- `resumable_action` on a `person_disambig` with `origin={skill:"metric_of_person", args:{field_key,
  metric_key}}` → each option is `Route("metric_of_person", {..., entity_id, name})`, NOT entity_card.
- Same for contact_of_person, title_of_person, research_of_person, papers_of_person, link_of_person.
- No `origin` → options are `entity_card` (fallback regression).
- Router: an ambiguous "Wang h-index" → the returned `person_disambig` Route's args carry
  `origin.skill == "metric_of_person"`. One router-level test per producer family (or a representative
  subset + a slot_extractor twin).
- `format_answer(person_disambig)` output unchanged with/without `origin` (display invariant).

## Goals checklist (shipped/deferred)
- Origin attached at all router producer sites — IN SCOPE
- Origin attached at all 4 slot_extractor sites — IN SCOPE
- resumable_action rebuild + entity_card fallback — IN SCOPE
- Display invariant preserved — IN SCOPE
- Per-origin tests + fallback + display test — IN SCOPE
- (No change to the disambig DISPLAY or to org_disambig — out of scope, unaffected.)
