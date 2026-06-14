# Research-Pane Extraction Fix (clean `research_areas` at the source) — Design Spec

**Principle:** a `research_areas` card that needs downstream cleanup means the
**extraction step made a mistake**. The fix belongs at extraction — store clean, pure,
delimited data at the source — not in a downstream normalizer (which would be patch-over-
patch). This spec fixes `njit_adapter.py` so the field is correct when written.

---

## 1. The defect (root cause, confirmed)

`v2/core/ingestion/njit_adapter.py:189-193`:

```python
research = _clean(pane("research"))                       # whole pane flattened to one line
research_areas: list[str] = []
ri = re.match(r"(?i)\s*research interests\s+(.*)", research)
if ri:
    research_areas = [a.strip() for a in re.split(r"\s*;\s*", ri.group(1)) if a.strip()]
```

Two compounding bugs:
1. **Greedy capture over flattened text.** `_clean` collapses the entire research pane —
   including the "In Progress" grants section and project blurbs — into one string, and
   `(.*)` grabs everything after the "Research Interests" label to end-of-pane.
2. **Wrong delimiter.** It splits only on `;`, but most NJIT authors delimit interests with
   **commas**. When there's no `;`, nothing splits and the whole blob becomes one "area".

**Evidence (live DOM, 2026-06-14).** The research pane is a flat sequence of label/content
leaf-`<div>`s:

```
<div>Research Interests</div>                         ← label
<div>Data mining, machine learning, …, data science</div>   ← THE interests (own div, comma-sep)
<div>In Progress</div>                                ← next label (grants)
<div>Multi-core algorithms for financial applications …</div>   ← grant text (NOT interests)
```

Live DB: all 37 active `research_areas` cards come from `people.njit.edu/profile/*` (this
one path). 5 are clean (author used `;` → old code split correctly); **32 are dirty**
single blobs (author used `,` → no split + grabbed the grants section). Longest: Jason Wang
3314 chars, Gerbessiotis 707 (bleeds into "In Progress"). The *about* pane is already parsed
structurally (`_about_sections` walks leaf-divs, stops at the next known label); the research
pane simply never got the same treatment — that asymmetry is the bug.

## 2. The fix — structural extraction (mirror `_about_sections`)

Read the pane's **structure** instead of regex over flattened text:

1. Walk the research pane's leaf-divs in order (`_leaf_divs`, already used elsewhere).
2. Find the div whose cleaned text begins (case-insensitive) with `research interests`.
3. The interests string is:
   - the **remainder of that div** after the `Research Interests` prefix, if non-empty
     (label-and-list-in-one-div layout); else
   - the text of the **next leaf-div** (the observed layout: list in its own div).
   Take exactly that one content unit — do **not** continue into following divs, so the
   "In Progress" grants section and project blurbs are excluded by construction.
4. Split the interests string on **either delimiter** — `[;,]` — strip each, drop empties.
5. No "Research Interests" label, or no research pane at all → `research_areas = []`
   (honest empty; the secondary fields/statement still carry the prose).

Result for Jason Wang: `["Data mining", "machine learning", "deep learning",
"computer vision", "explainable AI", "responsible AI", "generative AI", "trustworthy AI",
"data science"]` — clean, pure, delimited.

## 3. Scope boundary — `research_statement` unchanged

`research_statement = research` (the full flattened pane) is intentionally left as-is: it is
the descriptive blob used by semantic RAG, where the extra prose is fine. The defect is
specifically the **`research_areas`** field (the discrete facet / displayed list), so the
fix is narrowly there. (Both are FTS-searched, so area-matching recall is not reduced.)

## 4. Cleaning the existing dirty data (gated re-ingest)

The fix corrects **future** writes; the 32 dirty cards already in the DB are from the
2026-06-12 crawl. They get cleaned by re-ingesting through the corrected adapter, under the
existing gated workflow (per the v2 gated-workflow rule):
1. **Auto-backup** (the `--commit` path already takes an un-skippable backup).
2. **Dry-run** first; eyeball the would-be `research_areas` for the known-dirty profiles
   (wangj, alexg, sa3339, fh224, tn294) → confirm clean.
3. Commit, then **rebuild the vector index** so retrieval sees the cleaned cards.
No new manual step is introduced — this is the normal refresh path, re-run.

## 5. Testing (TDD)

Unit tests on the parser with `BeautifulSoup` fixtures mirroring the real DOM (no network):
- **Comma list + grants** (Jason-Wang-shaped): label div, comma-separated list div, then an
  "In Progress" div + grant div → `research_areas` is exactly the 9 comma items; asserts the
  grant text is **excluded**.
- **Semicolon list** (Schieber-shaped): splits on `;` → multi-element, unchanged-good.
- **Label-and-list in one div**: `<div>Research Interests Foo, Bar</div>` → `["Foo","Bar"]`.
- **No research pane** / **no "Research Interests" label** → `[]` (no crash).
- **Mixed delimiters / trailing empties** → stripped, no empty entries.

Then the existing njit_adapter test suite must stay green; full suite green.

## 6. Validation (live, after re-ingest)

- The 5 known-dirty profiles' cards are now short, clean comma/semicolon lists.
- `people_by_research_area`/the P2 expansion map still returns the same-or-better sets
  (cleaning removes noise words from the facet but the prose remains in `research_statement`
  for FTS, so recall is preserved).

## 7. Scope / non-goals

**In:** structural `research_areas` extraction in `njit_adapter.py` (one-div-after-label,
split on `[;,]`); TDD; gated re-ingest of NJIT faculty to clean existing cards; index
rebuild.

**Out:** the research-area *facet* skill (P2.5 — consumes this now-clean field); changes to
`research_statement`; other adapters/sources (none feed `research_areas` — confirmed); the
P2 expansion map (already shipped); data-coverage enrichment (separate track).

## 8. Risks

- **Layout variants** beyond the observed two (own-div / same-div). Mitigation: the fallback
  for "no label found" is empty (never a wrong blob), and the dry-run eyeball catches any
  profile the parser misreads before commit.
- **Over-trimming** (a real multi-div interests list). Not observed in 37 profiles (the list
  is always one div); if it ever appears, the symptom is a *short* card (missing tail), which
  the dry-run surfaces — degrades to less, never to wrong/noisy.
