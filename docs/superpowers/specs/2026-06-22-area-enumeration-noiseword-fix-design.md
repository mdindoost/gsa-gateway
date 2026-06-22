# Area-enumeration noise-word fix (accuracy backlog #6A, 2026-06-22)

**Status:** DESIGN reviewed 2026-06-22 (senior-eng SOUND-TO-BUILD + RAG/anti-fab SAFE-TO-BUILD; corrections folded —
see §7). Awaiting owner approval → TDD build. Under the EXPERT-REVIEW HARD GATE (retrieval/answer change).
**Related:** `docs/superpowers/findings/2026-06-22-accuracy-observability-and-feedback-backlog.md` (Bucket B).

## 1. Root cause (measured, not hypothesized)
The router ROUTES correctly: "who works on graph research" → `people_by_research_area(area=…)`. The bug is the
**extracted area string carries the redundant facet word "research"**, which over-constrains the FTS phrase match:

| area passed to the skill | people found |
|---|---|
| `"graph research"` ← what the router extracts from the 👎 question | **0** ❌ → empty → RAG → 1 wrong name |
| `"graph"` | **10** ✅ (the correct roster) |
| `"machine learning"` | 42 ✅ |
| `"graph neural networks"` | 3 ✅ |

`_research_entities` (skills.py) matches `area` as FTS phrase(s) (`_fts_query` → OR of `expand_area(area)` quoted
phrases). No faculty's research text contains the literal phrase "graph research", so it returns 0.

**Corrected failure chain (traced live; the earlier "→ RAG → wrong name" was inaccurate):** empty result →
`format_answer` returns the STRUCTURED deflection `'I couldn't find anyone working on "graph research".'`, which
`_try_structured` returns as the terminal answer. **RAG does NOT run today; no name is hallucinated** — the
user-facing bug is an honest-but-unhelpful deflection on a question we *can* answer (the 10-person roster).
(The historical 👎 that showed one wrong name — "Gerbessiotis", tagged *incomplete* — predates the v2.1 routing.)
FTS phrase match is word-boundary, so the over-match fear (graphics/graphene) does NOT apply — `"graph"` → 10
correct people, zero false positives (live-verified).

## 2. Two candidate fixes
- **D1 — strip in the router (`_extract_area`):** drop a trailing redundant facet word ("research"/"research
  area(s)") so "graph research" → "graph". One line. **Downside:** a *legitimate* field like **"operations
  research"** would become "operations" → FTS "operations" over-matches (operations *management*, network
  *operations* …) → **wrong people added to an enumeration** (a quality/anti-fab regression for a "complete +
  correct" skill).
- **D2 — fallback-on-empty in the skill (`_research_entities`) [RECOMMENDED]:** run the FTS with the area as
  given; **only if it returns 0 AND the area ends in a redundant facet word**, retry once with the stripped area.
  - "graph research" → full = 0 → retry "graph" → 10 ✅
  - "operations research" → full match succeeds → **kept as-is**, no over-match ✅
  Robust + anti-fab-safe (never broadens a field that already resolves), localized to one function, and both
  `people_by_research_area` + `count_people_by_research_area` (they share `_research_entities`) stay consistent.

## 3. Recommended design (D2)
In `_research_entities(conn, area, org_id)`: compute the result set for `area`. If it is **empty** and
`re.search(r"\s+research(?:\s+areas?)?$", area, re.I)` matches, recompute once with the stripped area; return
that. Bounded to a single retry; no change when the area already resolves. (Strip applies only to the trailing
facet word — interior "research" is untouched.)

## 4. Anti-fabrication / honest-partial
- Still complete + deterministic — the retry is the same FTS skill on a cleaner term, never a top-K sample.
- A genuinely unknown area (e.g. "graph algorithms", a fragmentation case — backlog #6B) still returns empty →
  degrades to RAG honestly. This fix does NOT paper over the fragmentation TODO; it only removes the noise word.
- No over-match for legitimate "X research" fields (the D2 retry fires only when the full term found nothing).

## 5. Test plan (TDD) — assert the STRUCTURED path (not RAG)
- `route("who are the people working on graph research")` → `people_by_research_area`; the skill returns the
  **10-person graph roster** (not 0). End-to-end via `format_answer`/`_try_structured`: **before** = the deflection
  `'I couldn't find anyone working on "graph research".'`; **after** = `'…N faculty work on … graph …'` (the roster).
  Do NOT assert a RAG path — it doesn't execute.
- `count_people_by_research_area("graph research") == count(...,"graph")` (10) — list and count agree.
- **"operations research" NOT broadened** [key edge case]: a person with area "operations research" resolves on
  the full phrase (e.g. 3), the retry does NOT fire, and the result is NOT the broadened "operations" set (14).
- **Degenerate terms** [SE]: `area="research"` and `area="research areas"` → regex does NOT match (needs a leading
  token) → no retry, no empty-FTS-term, no crash; result stays whatever the full term gave.
- **Org-scoped retry** [SE]: "graph research" scoped to an org → retry preserves `org_id` (same roster within the org).
- A term with no match and no facet suffix (e.g. "quantum teleportation") still returns 0 → honest deflection.
- Regression: existing area-skill tests unchanged.

## 6. Goals checklist
- [ ] D2 fallback-on-empty in `_research_entities`: a SINGLE `if` (no loop/recursion), rebuilding `_fts_query(stripped)`
      (so the stripped term still gets `expand_area` synonym expansion); module-level compiled regex
- [ ] "graph research" → 10-person roster; structured before(deflection)→after(roster); list==count
- [ ] "operations research" NOT over-matched (retry doesn't fire on a resolving field)
- [ ] degenerate terms ("research" / "research areas") → no retry / no empty FTS term / no crash
- [ ] org-scoped retry preserves `org_id`
- [ ] honest-partial preserved (truly-unknown area → empty → structured deflection)
- [ ] no regression in existing area tests

## 7. Design-review record (2026-06-22)
Dual review — **senior-eng SOUND-TO-BUILD, RAG/anti-fab SAFE-TO-BUILD.** Key confirmations + folded fixes:
- **Over-match fear DISPROVEN (live-DB):** FTS is word-boundary phrase match → `"graph"` returns 10 correct, ZERO
  graphics/graphene/cryptography false positives. D2 trades a deflection for a correct roster, not wrong names.
- **D1 confirmed unsafe:** unconditional strip turns "operations research"(3)→"operations"(14, over-matched). D2's
  only-on-empty guard is the anti-fab-safe choice.
- **`_research_entities` is the shared chokepoint** → list==count by construction; only the 2 intended skills are affected.
- **Regex is anchored to trailing-facet-only** and structurally cannot emit an empty FTS term (needs a leading token).
- **Spec correction (folded):** the failure is a structured DEFLECTION, not "RAG → wrong name"; tests assert the
  structured before/after. **Test additions (folded):** degenerate-term + org-scoped tests.
- **Build notes:** single `if` (no loop), rebuild `_fts_query` on retry, module-level regex.
- **#6B fragmentation stays honestly empty** — this fix is strictly the trailing noise-word.

*Next: owner approval → TDD build in a worktree off main → diff → sign-off → merge + restart.*
