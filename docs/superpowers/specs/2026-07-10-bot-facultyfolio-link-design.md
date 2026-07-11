# Bot → single FacultyFolio link on identity answers

**Date:** 2026-07-10
**Status:** Design (awaiting expert review + owner approval)
**Type:** Delta-spec on the shipped external-profiles design
(`docs/superpowers/specs/2026-06-19-person-external-profiles-design.md`).

## Goal

When the bot answers an identity question ("who is X") for a faculty member who **has a
published FacultyFolio page**, replace the scattered external-profile links (Google Scholar,
LinkedIn, ORCID, GitHub, Website) with a **single FacultyFolio link** plus a short note that
all their information lives on that page. People **without** a FacultyFolio page keep today's
behavior unchanged — the change is gradual and self-flipping as more people get pages.

## Background / current behavior

- On "who is X", the router picks the `entity_card` skill. `structured_answer.build_answer`
  computes `links = profile_fields.render_links(_person_attrs(conn, entity_id))`
  (`v2/core/retrieval/structured_answer.py:54`).
- `render_links(attrs)` (`v2/core/people/profile_fields.py`) iterates the `PROFILE_FIELDS`
  registry and returns a one-line Markdown list of every profile link the person has, e.g.
  `🎓 [Google Scholar](…) · 💼 [LinkedIn](…) · 🌐 [Website](…)`, or `None`.
- `deterministic_suffix(result)` appends that line to the FINAL answer **verbatim**, after LLM
  compose (`structured_answer.py:643`). The LLM never sees or rewrites it → no hallucinated URLs.
- The DB already carries the folio field: `attrs.profiles.facultyfolio = {"url": "..."}` on
  every published Person node (119 stamped live, all YWCC; `scripts/_stamp_facultyfolio_urls.py`,
  re-run after each Folio build). An unregistered `facultyfolio` key is simply ignored today.

## Owner decisions

1. **Replace, not add** — when a folio page exists, show ONLY the folio link (the page already
   aggregates Scholar/LinkedIn/etc.), not the folio link alongside the others.
2. **Say it explicitly** — the line must tell the user that all their info is on that page.
   Chosen wording: `📄 [X's FacultyFolio page](url) — all their links, publications & citation
   stats in one place` (X = the person's name).
3. **Gradual** — no folio page → current external-links behavior, unchanged, until everyone
   has a page.

## Design

### Change 1 — `render_links` + folio helper in `v2/core/people/profile_fields.py`

Add a module-level folio `Field` (NOT in `PROFILE_FIELDS`) and a small URL helper, then a single
special-case at the top of `render_links` that also adapts the note to whether the person has a
Scholar link (honest-partial: 43/119 folio people have no Scholar → their page has no
publications/citations section, so we must not claim those):

```python
_FACULTYFOLIO = Field("facultyfolio", "FacultyFolio", "📄")  # only to reuse _field_url's read

def facultyfolio_url(attrs: dict | None) -> str | None:
    """The person's FacultyFolio page URL (attrs.profiles.facultyfolio.url) or None."""
    return _field_url(attrs or {}, _FACULTYFOLIO)

def render_links(attrs: dict | None, name: str | None = None) -> str | None:
    attrs = attrs or {}
    folio_url = facultyfolio_url(attrs)
    if folio_url:
        label = f"{name}'s FacultyFolio page" if name else "FacultyFolio page"
        has_scholar = bool(_dig(attrs, "profiles.scholar.url"))
        tail = ("all their links, publications & citation stats in one place"
                if has_scholar else "all their profile links in one place")
        return f"📄 [{label}]({folio_url}) — {tail}"
    # else: today's registry loop, unchanged
    ...
```

`_FACULTYFOLIO` is used ONLY to reuse `_field_url`'s `attrs.profiles.facultyfolio.url` read. It is
**not** appended to `PROFILE_FIELDS` (that would make it show up in the normal link loop /
`match_link_field`).

### Change 2 — call site in `v2/core/retrieval/structured_answer.py` (`entity_card` branch)

Two edits:

1. **Pass the person's canonical name.** Use `entity._person_display_name(conn, a["entity_id"])`
   (returns `normalize_person_name(node.name)` — the same string as `entity_card`'s first line).
   Do NOT use `a.get("name")` (the raw router query token, often a bare surname) or parse the
   `card` text block. `render_links` falls back to the generic "FacultyFolio page" label if the
   name is missing.
2. **Suppress the `scholar_push` paper teasers for folio people** so the "one place" promise holds.
   Gate `_push_paper_lines` on the folio check (reusing `profile_fields.facultyfolio_url`):

```python
if skill == "entity_card":
    attrs = _person_attrs(conn, a["entity_id"])
    has_folio = profile_fields.facultyfolio_url(attrs) is not None
    return {"skill": skill, "name": a.get("name"),
            "card": entity.entity_card(conn, a["entity_id"]),
            "links": profile_fields.render_links(
                attrs, name=entity._person_display_name(conn, a["entity_id"])),
            "scholar_push": [] if has_folio else _push_paper_lines(conn, a["entity_id"])}
```

Nothing else changes. `deterministic_suffix` continues to append `result["links"]` (and the now-
empty `scholar_push`) verbatim.

## Scope / deliberate boundaries (YAGNI)

- **Identity links line only.** The "X research" answer surfaces Scholar **metrics**
  (citations/h-index) via `render_metrics` — those are numbers, not links; left untouched.
- **Targeted link queries unchanged.** "What's X's LinkedIn?" routes through `match_link_field`
  (a separate path) and still returns the specific link asked for, not the folio.
- **No registry membership.** `facultyfolio` is deliberately NOT a `PROFILE_FIELDS` row, so it
  never appears in the normal multi-link line and is not askable as "X's facultyfolio" (out of
  scope; can be added later if wanted).
- **No DB/schema/crawler change.** Data is already stamped; the field auto-updates on each Folio
  rebuild.

## Testing (TDD)

Unit tests on `render_links`:
1. Folio URL + Scholar + name → single `📄 X's FacultyFolio page … publications & citation stats
   in one place` note, and NONE of the other links appear even though scholar/linkedin/website exist.
2. Folio URL + NO Scholar + name → `📄 X's FacultyFolio page … all their profile links in one
   place` (no publications/citations claim).
3. Folio URL present, name `None` → generic "FacultyFolio page" label.
4. No folio URL, has other links → unchanged multi-link line (regression guard).
5. No links at all → `None` (unchanged).
6. `facultyfolio_url` helper: returns the URL when present, `None` otherwise.

Plus `structured_answer` integration checks:
- `entity_card` path threads the CANONICAL name (a surname-only query still yields the full
  normalized name in the label).
- `scholar_push` is empty for a folio-having person (teasers suppressed) and non-empty for a
  Scholar-having non-folio person (regression guard).
- `deterministic_suffix` emits only the folio line (no paper teasers) for a folio person.

Add verification questions to `eval/questions.txt` (a published person → folio link; a
non-published person → external links).

## Goals checklist (shipped / deferred)

- [ ] Replace external links with single folio link for folio-having people — **shipped**
- [ ] Explicit "all info here" note in the folio line — **shipped**
- [ ] Honest-partial: no publications/citations claim for the 43 folio people without Scholar — **shipped (adaptive wording)**
- [ ] Suppress paper teasers (`scholar_push`) for folio people so "one place" holds — **shipped**
- [ ] Canonical display name in the label (not the router token) — **shipped**
- [ ] Non-published people unchanged (gradual) — **shipped**
- [ ] "X research" metrics untouched — **shipped (unchanged by design)**
- [ ] "X's facultyfolio" as a targeted link query — **deferred (out of scope, YAGNI)**

## Rollback

Revert the two edits; `facultyfolio` becomes an ignored key again. No data change to undo.
DB-only field already present; this is code-only → needs a bot restart to take effect.
