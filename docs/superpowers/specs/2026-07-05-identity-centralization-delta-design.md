# Identity Centralization — Delta Spec (Kavosh self-facts, single source of truth)

**Supersedes:** the "Kavosh Identity Node + Version Lineage" build spec, whose core assumptions the
code disproves (see Phase 0). **Dropped from that spec:** the `Assistant` KG node, version
nodes/edges, the `about_self` router skill, and the entire hard-exclusion regime — all unnecessary
because (1) graph nodes are never embedded (only `knowledge_items` are), and (2) every faculty
skill + `fuzzy_people` already filters `WHERE type='Person'`. Self-facts are **authored constants**
with no external source to verify against, so the KG-grounding argument doesn't transfer — config,
not graph.

**Goal (unchanged):** one deterministic, version-proof source of truth for "who/what am I," rendered
in the bot layer where `INTENT_IDENTITY` already lives. Shipping the next version = append ONE array
element. Kill the "Kavosh v2.1 + granite4" drift by removing all 6 hardcoded copies.

---

## Scope (four points)
1. **Centralize** all identity/version facts into `bot/core/identity.py`; every render reads from it.
2. **Broaden `INTENT_IDENTITY`** to cover the missing intents (who made/runs you, what do you run on,
   who came before you, do you make things up) and state the **honesty value** explicitly.
3. **Disambiguation guard test:** "who is Koutis" → `entity_card`, NOT identity.
4. **Fix stale facts** as part of centralizing (the drift bug).

**Not touched:** faithfulness/gating behavior (the honesty line is a static claim, not a gate change).
Voice lives in the identity render ONLY; factual answers stay neutral.

---

## The one schema fork (flagging per your instruction)

**Config module vs `settings` JSON row.** I recommend a **Python config module** (`bot/core/identity.py`):
- Identity is code-coupled — the render templates reference specific fields; keeping facts and render
  in the same version-controlled place stops them drifting apart.
- A version bump is a deliberate release event (new model + restart anyway), not a runtime knob — so
  "dashboard-editable without restart" (the settings-row advantage) buys nothing here.
- The diff is reviewable; it's testable; it stays LLM-agnostic.
The `settings` table is for operational knobs (thresholds, flags), not the assistant's identity.
**If you'd rather it be dashboard-editable at runtime, say so and I'll put the JSON in a `settings`
row instead** — same shape, different home. Your call at eyeball time.

---

## Centralized config shape — `bot/core/identity.py`

```python
"""Single source of truth for the assistant's identity + version lineage.

Deterministic self-facts — authored constants (no external source to verify against). The identity
persists across versions; VERSIONS is the growing lineage. SHIP THE NEXT VERSION = append one dict
to VERSIONS and flip the prior current->retired with an active_to date. Nothing else changes.
"""

IDENTITY = {
    "name":          "GSA Gateway",
    "purpose":       "NJIT's Graduate Student Association assistant, and a guide to the wider "
                     "NJIT community",
    "creator_name":  "Mohammad Dindoost",
    "creator_email": "md724@njit.edu",
    "operator":      "the NJIT Graduate Student Association",
    "scope_covers":  "official GSA documents and NJIT's knowledge graph of faculty, research, and "
                     "departments across every college",
    "scope_excludes":"the open web, and general topics unrelated to NJIT",
    "infrastructure":"a local language model on NJIT infrastructure — not a cloud service",
    "honesty":       "I'd rather tell you “I don't know” than guess. My answers come from "
                     "documents and the knowledge graph, and I abstain when I don't have the facts.",
    "repo":          "https://github.com/mdindoost/gsa-gateway",
    "voice":         "warm, precise, a little scholarly",   # colors the identity render ONLY
}

# Version lineage — OLDEST FIRST. The single entry with status="current" is live.
# model=None on the current entry => render the LIVE model (from ollama.model), so the model can
# NEVER drift in the config. Retired entries may record the model they ran on (historical fact).
VERSIONS = [
    {"name": "Binesh", "meaning": "insight",                "persian": "بینش",
     "status": "retired", "active_from": None,        "active_to": "2026-06-15", "model": None},
    {"name": "Kavosh", "meaning": "exploration, discovery", "persian": "کاوش",
     "status": "current", "active_from": "2026-06-15", "active_to": None,        "model": None},
]

def current() -> dict:
    """The live version (the one with status='current')."""
    return next(v for v in reversed(VERSIONS) if v["status"] == "current")

def lineage() -> list[dict]:
    """Retired predecessors, oldest -> newest, for 'who came before you'."""
    return [v for v in VERSIONS if v["status"] == "retired"]

def persona_line(model: str | None = None) -> str:
    """One-line persona for the LLM system prompt (replaces the hardcoded L98 string)."""
    v = current()
    return f"You are {IDENTITY['name']} (current version: {v['name']}), " \
           f"{IDENTITY['purpose']}."
```

**"Ship next version = append one element" — worked example (future "Simorgh"):**
```python
# flip Kavosh -> retired (add active_to), append the new current. That's the whole change.
{"name": "Kavosh",  "status": "retired", "active_from": "2026-06-15", "active_to": "2026-09-01", ...},
{"name": "Simorgh", "meaning": "...", "persian": "...", "status": "current",
 "active_from": "2026-09-01", "active_to": None, "model": None},
```
No render edits, no string hunt — every consumer reads `current()`/`lineage()`.

---

## Open question for you (the numeric "v2.1")

The lineage models **codenames** (Binesh -> Kavosh). The current output says "Kavosh **v2.1**", but a
numeric release keeps moving (Build B, v2.5 in flight) — that number IS a perpetual drift source.
Two options:
- **(A, recommended)** render the **codename only** — "Kavosh" — and drop the user-facing number.
  Codename = stable identity; permanently kills the drift you're annoyed by.
- **(B)** keep a `release` field ("v2.1") on the current entry, shown as "Kavosh v2.1", bumped in place.

I lean A. Tell me A or B at eyeball time.

---

## Extended `INTENT_IDENTITY`

### New trigger patterns (added to `IDENTITY_PATTERNS`, intent_detector.py)
All require assistant-directed second person, so "who is <name>" can't match:
```
who (made|built|created|develop(ed)?) you        # who made you
who (runs|operates|owns|maintains) you            # who runs you
what (do|are) you (run(ning)? on|built on|powered by)   # what do you run on
who came before you | your (history|lineage|predecessor) | were you called
do you (make (things|stuff) up|hallucinate|lie|ever guess) | (what are|do you have) your? limits
```
`who is <X>` matches none of these (no "you/your") -> stays `entity_card`. **Guard test locks it.**

### Handler: one comprehensive render + a few focused short-circuits
Keep ONE intent + ONE handler (no new intent taxonomy). A light keyword check picks a focused answer
for narrow asks; otherwise the full render. All read from `identity.py` + live `ollama.model`.

**Full render (default — "who/what are you"), with honesty stated explicitly:**
> I'm **GSA Gateway**, NJIT's Graduate Student Association assistant — and a guide to the wider NJIT
> community. You're talking to my current version, **Kavosh** (کاوش — *exploration, discovery*),
> successor to **Binesh** (*insight, بینش*), which retired June 15, 2026.
>
> I run on **{live model}** — a local language model on NJIT infrastructure, not a cloud service.
> Unlike ChatGPT, I'm purpose-built for NJIT: my answers come from official **GSA** documents *and*
> NJIT's **knowledge graph** of faculty, research, and departments across **every college** — not the
> open web, and not general topics unrelated to NJIT.
>
> **I'd rather tell you "I don't know" than guess** — I answer from documents and abstain when I don't
> have the facts.
>
> What I can help you explore:
> - 🔬 NJIT faculty across every college — who works on a topic, their research areas & citations
> - 🏫 Departments, programs & who's who (deans, chairs, directors)
> - 🧭 Campus resources & offices
> - 🎓 GSA events, the MMI Workshop series, travel awards & funding
> - 👥 GSA officers, club/RGO rules & the constitution
>
> md724@njit.edu · 🛠️ Open source — [GitHub](https://github.com/mdindoost/gsa-gateway)

**Focused short-circuits (narrow asks get a short answer, not the wall):**
- *who made / runs you* → "I was created by **Mohammad Dindoost** (md724@njit.edu) and operated by
  **the NJIT Graduate Student Association**. 🛠️ [GitHub]."
- *what do you run on / are you ChatGPT / cloud* → "I run on **{live model}**, a local language model
  on NJIT infrastructure — not a cloud service, and not ChatGPT. My answers come from NJIT documents
  and the knowledge graph, not the open web."
- *who came before you / lineage* → "My current version is **Kavosh** (*exploration*). Before me came
  **Binesh** (*insight*), retired June 15, 2026." (walks `lineage()` — grows automatically.)
- *do you make things up / limits* → the **honesty** line verbatim from config.

Model name is always the LIVE `ollama.model`; the model-less fallback keeps a short version-only render.

---

## Files touched
- **new** `bot/core/identity.py` — the single source of truth (config + helpers).
- `bot/services/intent_detector.py` — add the new `IDENTITY_PATTERNS`.
- `bot/core/message_handler.py` — identity handler reads `identity.py` (full + focused renders);
  system prompt L98 → `identity.persona_line(model)`; greeting/farewell (L453/461/479) → read
  `current()` so "Kavosh" isn't hardcoded. **All 6 literals removed.**
- **new** `bot/tests/test_identity.py` — renders read config; disambiguation guard
  ("who is Koutis" → entity_card, "who made you" → identity); "ship next version = append one" unit
  (append a fake version → `current()` flips, renders update, no other change); model-drift guard
  (render shows live model, not a stored string).

## Verification
- All listed self-intents render correct facts from the node — incl. the 4 newly-covered ones.
- "who is Koutis" / "faculty in CS" / "how many faculty" — unchanged (identity is bot-layer; no KG
  node exists, so counts/skills are provably untouched — nothing to regress).
- Grep proves 0 remaining hardcoded "Kavosh v2.1" / "Binesh" string literals outside `identity.py`.
- Router bakeoff: `about_self` is additive at the intent layer; no family/hardneg regression expected
  (patterns are tight, second-person-gated).

## Build order
1. `identity.py` (config + helpers) — show you the final structure.
2. Extend `IDENTITY_PATTERNS`.
3. Rewrite the identity handler (full + focused) + repoint the system prompt & greeting to config.
4. Tests (incl. guard + append-one + drift). 5. Grep-clean the literals. 6. Manual render check + bakeoff.
```
