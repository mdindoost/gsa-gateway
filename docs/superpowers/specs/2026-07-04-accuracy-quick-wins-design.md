# Accuracy Quick-Wins Batch — Design

**Date:** 2026-07-04
**Author:** Kavosh maintainer session (Opus) — from the Fable pipeline accuracy audit (`scratchpad/fable-accuracy-review/03_ROADMAP.md`)
**Status:** DRAFT → Fable review → owner approval → TDD build
**Scope:** the "quick win" tier of the accuracy roadmap. Deeper items (A1 live-tier gate, A3
antecedent guard, A15 topic→people routing) are explicitly OUT of this batch — separate designs.

## Goal
Ship the surgical, one-file, low-risk accuracy fixes that each close a concrete defect proven in the
audit or in live transcripts, WITHOUT touching the routing front door or the gate architecture. Every
fix here is reversible and independently testable. Grouped into two waves so the owner can approve/ship
incrementally.

## Non-goals
- No routing/classifier changes (A15 — separate design).
- No live-tier gating (A1 — separate design).
- No change to the gate's *architecture* (A2 is a bug-fix WITHIN the existing gate, not a redesign).

---

## WAVE 1 — trivial / surgical (no plumbing)

### QW-A2 — Gate-2 transport failure must KEEP the answer (never-withhold)
**Problem.** `message_handler._faithfulness_gate:699-704`: `raw = await self.ollama.generate(...) or ""`.
`OllamaClient.generate` returns `None` on EVERY transport failure (timeout `ollama_client.py:385`,
HTTP≠200 `:377`, `ClientConnectorError` `:388`, generic `:389`). The `or ""` collapses `None` into the
same empty string as a model that emitted garbage. `answer_gate.parse_gate2("")` → `Gate2Verdict(
"FULLY_SUPPORTED", parsed=False)`; `faithfulness.decide_after_gate2(..., parsed=False)` → `abstain`
(`:235`). So a **Gate-2 checker OUTAGE silently discards an already-composed, grounded answer** — while a
gate *exception* KEEPS it (`message_handler.py:938-940`, "gate-error-keep"). Same infra fault, opposite
outcome. Violates owner rule #2 (never-withhold) + #7 (gate silently lowering accuracy on its own failure).

**Why the current behavior exists (must preserve).** `faithfulness.py:226-232` deliberately abstains on
parse-fail because the WS4 eval showed Granite emits a NON-EMPTY unparseable `FULLY_SUPPORTED` for
out-of-domain questions ("capital of France") — and "Gate-2 runs at temp 0.0, so a parse failure is
DETERMINISTIC out-of-domain garbage." **That premise holds for a model RESPONSE that won't parse; it is
FALSE for a transport failure (no response at all).** The France case had a non-empty string; a timeout
has `None`.

**Fix.** In `_faithfulness_gate`, capture the raw result BEFORE coercing and branch on `None`:
```python
raw = await self.ollama.generate(prompt=usr_p, system=sys_p,
                                 options={"temperature": 0.0, "num_predict": 256}, fmt="json")
if raw is None:                         # transport failure (timeout/HTTP/conn) — checker UNREACHABLE
    return True, "gate2-transport-keep" # keep, exactly like the exception path (never-withhold)
v = parse_gate2(raw)                    # non-None: real response (incl. the France garbage) → unchanged
...
```
Non-`None` (non-empty garbage, the France case) keeps TODAY's behavior → France protection intact.

**FABLE CORRECTION (must absorb):** `generate()` coerces an empty model response to `None` at
`ollama_client.py:503` (`.strip() or None`), so `raw is None` ALSO catches "model returned literally
nothing," indistinguishable from a transport failure at this seam. That is acceptable (an empty
`format=json` response at temp 0.0 is a malfunction, and never-withhold says keep on checker malfunction) —
but the code comment MUST say "transport failure OR empty model response" and the test must be labeled that
way. `""` is NOT a reachable branch here; do not write a test pretending it is.

**Tests (TDD).**
- `generate` returns `None` (labeled "transport OR model-empty") → gate returns `(True,
  "gate2-transport-keep")`; composed answer survives.
- `generate` returns the France-style non-empty unparseable JSON → still abstains (regression guard).
- `generate` returns a valid `NOT_IN_CONTEXT` → still abstains. Valid grounded `FULLY_SUPPORTED` → answers.

**Risk.** Minimal — strictly widens "keep" on a fault that today wrongly withholds; no should-abstain case
relied on transport-None (the France case is response-garbage, not None).

### QW-A16 — strip malformed `[: Dept]` citation artifacts leaking to users
**Problem.** The generation prompt labels chunks `[doc_id N: FriendlyName]` (`ollama_client.py:242`).
`granite4:tiny-h` frequently emits a MALFORMED citation — "According to document **[: Mathematical
Sciences]**" — copying the bracket but dropping `doc_id N`. `_strip_doc_citations` (`message_handler.py:
98-100`) only matches the literal `doc_id` token, so the malformed bracket sails through to the user
(seen live 2026-07-04 in three separate "graph"/"neuroscience" answers).

**FABLE CORRECTION (my original regexes ship defective — the live artifact has BOLD markers between
"document" and the bracket: `According to document **[: Mathematical Sciences]**`).** My regex 1 couldn't
match through `**` and regex 2 left `According to document ****,` on screen. Corrected, ORDER MATTERS —
strip the bracket WITH flanking emphasis first, then the now-dangling connector:
```python
# 1) the citation bracket, with any flanking markdown emphasis:
t = re.sub(r"[*_]{0,3}\[\s*(?:doc_?id\s*\d*)?\s*:\s*[^\]]*\][*_]{0,3}", "", t)
# 2) the now bracket-less meta connector (mirrors the existing 'according to doc_id' sub at :98):
t = re.sub(r"(?i)\baccording to (?:the )?document\b\s*[:,-]?\s*", "", t)
```
Regex 1 is TIGHT by design — it permits only whitespace or `doc_id N` before the internal colon, so
`[Note: …]`, `[Source: url]`, `[10:30]` are all untouched. Keep the existing well-formed `doc_id N` subs
(source-note harvesting at `_source_note_for:111` keys on `doc_id N` and runs on the PRE-strip text, so it
is unaffected).

**Tests (TDD).**
- The EXACT live string "According to document **[: Mathematical Sciences]**, Prof X …" → "Prof X …" (no
  stray `****` or connector).
- "[doc_id 5: Computer Science]" and bare "[: Biological Sciences]" → removed.
- A benign bracket adjacent to bold, e.g. "see **the guide** [Note: draft]" → the `[Note: …]` and bold are
  **untouched** (proves regex 1's flanking-emphasis consumption doesn't eat unrelated emphasis).
- "[10:30]" and "[Source: https://…]" → untouched.
- Well-formed "According to doc_id 5 (Computer Science):" → still stripped (regression).

**Risk.** Low — the whitespace/doc_id-only-before-colon shape is specific to the artifact; the benign-bracket
tests prove no over-strip.

### QW-A14 — honest relevance label for keyword-only hits
**Problem.** `retriever_shim.py:151`: `rel = c.similarity if c.similarity is not None else 0.7` — a
FABRICATED 0.7 for keyword-only (FTS) hits with no vector similarity, rendered into the prompt as
"[Relevance: 70%]" (`ollama_client.py:253`), nudging the small model to over-trust keyword-coincidence chunks.

**FABLE CORRECTIONS:** (1) the imputed number does NOT feed the miss-signal — `top_relevance` keys on
`ce_score` (`retriever_shim.py:110-128`). It feeds the LOGGED confidence (`message_handler.py:1002-1006`),
so leaving the number untouched is still right, but the rationale is "don't disturb logged confidence," not
"miss-signal." (2) The shim ALSO coerces `similarity = c.similarity or 0.0` at `retriever_shim.py:157`, so a
keyword-only chunk reaches the prompt builder with `similarity == 0.0`, **not `None`**. `V1Chunk` does carry
`similarity` to the builder (`retriever_shim.py:39`).

**Fix.** In `ollama_client.py` (~`:253`), render the label from the chunk's `similarity`; a falsy value
(0.0) = keyword-only:
```python
if getattr(chunk, "similarity", 0.0):        # truthiness deliberate: shim coerces keyword hits to 0.0
    lines.append(f"[Relevance: {chunk.similarity:.0%}]")
else:
    lines.append("[Match: keyword only]")
```
Add the inline comment so no one "fixes" it to `is not None` (which would break it, since keyword hits are
`0.0` not `None`).

**Tests.** A chunk constructed the way the shim builds it (`similarity=0.0`) renders "keyword only", not
"70%"; a vector hit (`similarity=0.46`) renders "46%".

**Risk.** Low — display-only; logged confidence + miss-signal both unchanged.

### QW-A10 — canned help/deflection text advertises retired slash commands
**Problem.** `message_handler.py:387-400` (help) lists `/events`, `/contact [role]`, `/resources`;
`:43-50` (`_KB_MISS_RESPONSE`) and `:723-729` (`_useful_abstain`) say "Use /contact …". Only `/qrcode`
exists in v2 (all lookup commands cut in the v1→v2 migration). Every deflection instructs users to use
dead commands.

**Fix.** Rewrite the three canned strings to plain-language guidance (no slash commands except the real
`/qrcode`). E.g. help → "Just ask me naturally — e.g. 'who is the GSA treasurer', 'CS faculty who work on
AI', 'travel award deadline'. (The one command I have is **/qrcode**.)" Deflections → drop the "/contact"
line, keep the office/email guidance. (Fable: verified grep — no other user-facing string advertises the
dead commands; the `telegram_connector.py:146` hit is a comment.)

**Tests.** Assert none of the three canned strings contain `/events`, `/contact`, or `/resources`; assert
the help text mentions `/qrcode`.

**Risk.** None (static copy). Preserves the office/email/verbatim guidance already there.

---

## WAVE 2 — light plumbing (still one-concern each)

### QW-A4a — post-compose survival check (compose must not DROP facts)
**Problem.** `compose_from_rows` truncates at `num_predict=900` with no output verification; uncapped
roster skills (`faculty_in_department`, `people_in_org`, `faculty_areas_in_department`) render 100+ names
into Facts → compose emits "X has 137 faculty: …" and hits the ceiling mid-list — the COUNT survives, the
tail NAMES vanish, and `_compose_structured` (`message_handler.py:508-521`) accepts any non-empty result.
Violates rule #4 (MUST NOT drop). (Emails/phone digit-fidelity is the sibling concern — see A4b, deferred.)

**Fix.** In `_compose_structured`, after a successful compose, run a deterministic survival check: every
name-token and every email/`\d{3,}` digit-run present in `facts` must appear in `composed`; on ANY miss,
fall back to `facts` verbatim (the complete deterministic answer). Cheap, exact, no LLM.
```python
if composed and _covers(facts, composed):   # names + emails + digit-runs from facts all present
    out = composed
else:
    out = facts                              # compose dropped content → serve complete Facts verbatim
```
**FABLE RULING on the coverage heuristic (adopt as specified):**
1. **Scope the check to LIST-shaped facts only** — facts with ≥4 `"; "`-separated items OR matching a roster
   lead-in (`has \d+ (faculty|people|officer|department)`). Short prose facts (`entity_card` cards) **skip
   the check entirely** — this is what protects the "Hi there!" greeting (owner rule [[feedback_keep_friendly_greeting]]);
   running it on cards would collapse every friendly card to terse verbatim on any dropped digit.
2. **Run the checks on `composed` BEFORE the suffix append** (`message_handler.py:515-516` — the deterministic
   suffix is not part of `facts` and must not pollute the comparison). Checks: (a) every email regex hit in
   facts present in composed; (b) every `\d{3,}` run present; (c) **every list item's surname token** (last
   alphabetic token of the name segment; casefold, strip markdown `*_`, NFKD-strip diacritics BOTH sides)
   present in composed. Require **ALL** items, not "≥N" — temp-0 compose never legitimately paraphrases a
   NAME away (prompt forbids drops, `ollama_client.py:399-413`), so any missing surname IS the truncation
   defect. Reordering passes (presence, not order).
3. Any miss → `out = facts`; then append suffix as normal. Err-verbatim is rule-#2-safe by construction.

**Tests.** Facts with 40 names, compose returns 20 → Facts. Facts with an email/phone, compose drops it →
Facts. Compose that reorders but keeps all names → composed used (no false-trigger). A short `entity_card`
card WITH email+phone, compose rephrases → composed used (check skipped; greeting preserved).

**Risk.** Medium — but the list-only scoping confines the false-trigger surface to long rosters, where
verbatim facts is the DESIRED fallback anyway.

### QW-A8 — log a `question_id` for structured/KG answers (feedback + analytics)
**Problem.** KG answers return a bare `MessageResponse(text=…)` (`message_handler.py:322-324` and the
v2.1 `_answer_decision` KG branch `:624-630`) with NO `db.log_question` and NO `question_id` → connectors
gate the 👍/👎/🔄 keyboard on `question_id` (`telegram_connector.py:241-253`, `chat.py:135-144`), so the
MOST-trusted tier is unmeasurable and gets no thumbs-down signal (exactly where A3/A5/A15 wrong-entity
errors land). Violates the "buttons on every answer" rule (universal since 2026-06-29).

**FABLE CORRECTIONS:**
1. **`_register_and_record` CANNOT log** — it has neither `guild_id` nor `platform` (`message_handler.py:
   519-541`) and `db.log_question` requires both (`database.py:216-225`). Do NOT replumb it. Instead log at
   the two response-construction points where `req` IS in scope: `handle()` structured return (`:318-320`)
   and `_answer_decision` KG branch (`:624-626`). `_try_structured` returns `(text, rt.skill)`. This keeps
   `_register_and_record` as purely the flag-gated follow-up chokepoint — "split logging out of the flag"
   falls out for free.
2. **NO connector change needed** — both connectors already gate the keyboard purely on `question_id`
   (`chat.py:135-146`, `telegram_connector.py:241-253`). Lower risk than I stated.
3. **Return-type change is safe at all 3 `_try_structured` sites** — `:278` and `:588` only test `is None`;
   only `:318` unpacks.
4. **Declare the A12 interaction (owner rule: review-against-plan):** a `question_id` gives KG answers the
   🔄 button for the first time, and 🔄 re-runs pure RAG at temp 0.7 BYPASSING the router (`:638-646`) — a
   correct deterministic KG answer can be "retried" into a worse semantic one. **Decision: ACCEPT + DOCUMENT**
   (buttons-on-every-answer is an owner rule; retry is user-initiated, a legitimate second opinion). Add to
   the A12 ledger + a test PINNING current retry behavior so the choice is recorded. (Fixing retry to route
   through the full stack is A12's job, deferred.)

**Fix (REFINED at build — lower blast radius than the `(text, skill)` return-type change; flagged to Fable).**
The spec's `(text, skill)` return-type change would break 3+ existing tests that mock `_try_structured` as a
STRING (`test_answer_gate_wiring:211`, `test_handler_shadow_agreement:20,27`) — Fable verified the prod call
sites but not the test doubles. Instead, NO return-type change:
- **Primary path `_answer_decision` (KG branch)** already has `decision.skill` in scope → log
  `matched_topic=f"kg:{decision.skill}"` (granular) + attach `question_id`. This is the LIVE path under
  ROUTER_V21=1, so KG answers get the specific-skill tag.
- **Legacy `_try_structured` path** (handle() `:341`, reached only when the v2.1 router returns None/COMMAND)
  logs a coarse `matched_topic="kg"` + attaches `question_id`. `_try_structured` still returns a string.
Both `confidence=100.0`, `guild_id`/`platform` from `req`. Zero test breakage; same user-facing win.

**Tests.** A KG answer produces a `question_id` + a `log_question` row tagged `kg:<skill>`; the connector
renders the feedback keyboard. `person_disambig` answers also log (`kg:person_disambig`) + get buttons —
compatible with the pending-action text-reply. A pin-test on 🔄 re-running RAG (records the A12 choice).

**Risk.** Medium (return-type change at the 3 `_try_structured` sites — all in-repo, tested). No connector
change, no answer-content change.

### QW-A6 — faithfulness gate must see the SAME context generation saw
**Problem.** The gate builds passages from `chunks[:5]` truncated to 1,200 chars (`message_handler.py:695`),
while `generate_answer` fits up to the model budget via `_fit_chunks(..., num_predict=512)` (`ollama_client.
py:348`). A typed value (count/rate/money/date) grounded past char 1,200 — common on the deep-fallback tier,
which serves whole parent pages — fails `answer_has_grounded_type` → false-abstain on a grounded verbatim
figure (rule #2). This is the office-tier-rollback cardinal sin in miniature.

**FABLE — RISKIEST FIX IN THE BATCH; the naive version INVERTS its own goal.** The Gate-2 `generate()` call
passes options WITHOUT `num_ctx` (`message_handler.py:695-698`), so it runs at the Ollama SERVER default
(~2048/4096; the client's 16384 only applies when explicitly sent, `ollama_client.py:204-218`, and
`granite4:tiny-h` has no Modelfile num_ctx). Ollama silently truncates the FRONT of an overflowing prompt —
the system prompt (its instructions) goes first. Feed the full ~13k fitted context into `gate2_prompt`
without `num_ctx` → Gate-2 loses its instructions → non-JSON → `parsed=False` → **abstain**
(`faithfulness.py:233-237`). Result: MORE false-abstains, fleet-wide, silently — the exact failure the fix
targets. (Today's 5×1,200 call is already borderline at a 2048 default — a latent bug to close regardless.)

**Fix (required shape):**
1. **Deterministic checks get the FULL fitted text** — `assess_pre_gate2`/`answer_has_grounded_type` +
   `robust_grounded` are pure Python; this ALONE closes the stated test (typed value past char 1,200), zero
   prompt risk.
2. **Gate-2 LLM gets a BUDGETED window AND `num_ctx: self.num_ctx`** passed in its options. (Answer-adjacent
   windowing is optional polish, not required this batch.)
3. **Plumbing:** expose a public `prefit(...)` on `OllamaClient` (build system prompt from history +
   `_fit_chunks`); `_rag_pipeline` computes `fitted` ONCE and passes it to BOTH `generate_answer` (which
   re-verifies idempotently — one cheap token-count pass) and the gate. NO return-type change, NO stashed
   mutable state (a `self.last_fitted` would RACE across concurrent users). Single call site confirmed
   (`message_handler.py:908`, shared by retry).

**Tests.** Grounded "$150" at char ~3,000 → gate KEEPS (was false-abstain). Ungrounded typed answer → still
abstains (regression). **A gate-prompt-SIZE assertion** so the token budget can't silently regress.

**Risk.** Medium-High — must pass `num_ctx` or it inverts. The prompt-size test is the guard.

### QW-A9 — `person_disambig` resume must run the ORIGINALLY-asked skill — **SPLIT OUT (own micro-spec)**
**Fable found this materially under-scoped for a quick win.** `person_disambig` Routes are created at
**SEVEN** sites across **two** files: `router.py:472` (inside shared `_resolve_surname`, reached from
`:517,:537,:621,:635,:736,:763`), `router.py:484`, `router.py:753`, **and `slot_extractor.py:394,:413,:446,
:457`** — the slot-extractor sites being exactly the contact/title asks where origin matters MOST. As I
originally scoped it ("router.py + structured_answer.py"), it would ship a HALF-fix that still resumes
contact/title disambigs as bio cards — the defect surviving its own fix. Clean mechanism (each branch
enriches the disambig Route with `args["origin"]={"skill","args"}`; `resumable_action` rebuilds
`Route(origin.skill, {**origin.args, "entity_id":…})`, fallback `entity_card`) is mechanical but ~10 touch
points + per-origin tests (metric/contact/title/research/papers/link). **→ Removed from this batch; gets its
own micro-spec + Fable review.** (Not lost — tracked in the deferred list.)

---

## Sequencing & delivery
- **Wave 1 (A2, A16, A14, A10)** — ship together; tiny diffs, near-zero risk, each a clean win. A2 clears a
  never-withhold violation; A16 removes the visible junk from the 2026-07-04 transcripts.
- **Wave 2 (A8, A4a, A6)** — ship after Wave 1, each as a SEPARATE diff (Fable order: A8 first to start
  measuring the KG tier before other behavior changes → A4a → A6). **A9 is SPLIT OUT** to its own micro-spec
  (7 route sites). Each carries a return-type/plumbing change with its own tests + Fable diff-review.
- Each wave: TDD (tests first) → show diff → owner sign-off → commit (explicit paths) → `restart.sh` (code
  change). DB untouched by all of these (no embed/backup needed).

## Grow-correctness-suite (owner rule)
**Unit tests (NOT eval.sh — Fable: eval can't mock a transport fault):** the A2 transport-keep case, the
A4a large-roster drop case, the A6 deep-tier typed-value-past-1200 case — all live in pytest.
**`eval/questions.txt` additions (real-pipeline observable):** a `[: Dept]`-artifact-shaped answer check,
and — flagged for the SEPARATE A15 work, not this batch — "faculty working neuroscience" / "whose research
is in graph". (The A9 "Wang h-index" disambig case moves to the A9 micro-spec.)

## Goals checklist (shipped/deferred — owner rule "review against plan")
- QW-A2 transport-keep — IN BATCH (Wave 1)
- QW-A16 citation strip — IN BATCH (Wave 1)
- QW-A14 honest relevance label — IN BATCH (Wave 1)
- QW-A10 canned-text refresh — IN BATCH (Wave 1)
- QW-A4a compose survival check — IN BATCH (Wave 2); **A4b** (contact/title → deterministic digits) DEFERRED
  (product-shape change: contact answers go terse verbatim — needs owner call).
- QW-A8 KG question_id — IN BATCH (Wave 2)
- QW-A6 gate-sees-fitted-context — IN BATCH (Wave 2)
- QW-A9 disambig originating-skill — **SPLIT OUT of this batch** (Fable: 7 route-creation sites across
  router.py AND slot_extractor.py — under-scoped as a quick win) → its own micro-spec.
- **A11** (top_relevance keys on injected profile card) — DEFERRED, separate (was silently missing — Fable).
- **A13** (slot-extractor role/area grounding guard) — DEFERRED, separate (was silently missing — Fable).
- A1 / A3 / A5 / A7 / A12 / A15 / A15b — DEFERRED to separate designs (loudly, not silently).
