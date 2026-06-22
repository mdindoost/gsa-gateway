# Contextual Query Rewrite — fix conversation follow-up accuracy (2026-06-22)

**Status:** DESIGN reviewed 2026-06-22 (two reviews, both BUILD-WITH-FIXES; all fixes folded — see §9). Awaiting
owner approval → TDD build. Accuracy-backlog item #2 (the #1 evidenced bug). Under the EXPERT-REVIEW HARD GATE.
**Related:** `docs/superpowers/findings/2026-06-22-accuracy-observability-and-feedback-backlog.md` (Bucket A);
the router v2.1 Phase-2 "context-rewrite for hardneg follow-ups" lever.

## 1. Problem (root cause, read from code)
The bot tracks conversation history (`conversation_manager.add_turn` / `get_history`, ≤5 turns) but **history
only reaches answer GENERATION, never RETRIEVAL or ROUTING.** In `message_handler._rag_pipeline`, the retrieval
`search_query` (line ~564) is the **raw message**, an officer-name expansion, or a **history-LESS** short-query
`expand_query` (≤3 words). The v2.1 UnifiedRouter also sees the raw message. So follow-ups fail **at retrieval**:
- "what about for BME?" → searches the literal string → wrong chunks (👎 off_topic)
- "what is his position" → "his" never resolved before search (👎 off_topic)
Evidence: 7+ of the 46 👎 are this class (Bucket A), all with high confidence (the answer was confidently wrong).

## 2. Goal / non-goals
**Goal:** resolve a follow-up message into a **standalone query** using recent history, BEFORE routing+retrieval,
so both the structured router and the RAG retriever see the resolved query. **Non-goals:** changing how history
reaches compose (unchanged); multi-turn memory beyond the existing ≤5 turns; rewriting clearly-standalone questions.

## 3. Design (review-hardened)
A **contextual-rewrite step at the TOP of `handle()`**, before any routing/retrieval consumer sees the query:
1. **Gate (deterministic, no LLM).** Attempt only when BOTH: (a) recent history exists, AND (b) the message has a
   **referential signal** — a BARE pronoun (his/her/its/their/that/those with no adjacent proper noun) or an
   elliptical opener ("what about…", "and …", "why not…", "the official one"). **[RA5/SE4]** Gate on a referential
   signal, NOT mere shortness (a complete terse question like "office hours" must not fire). Possessives naming a
   real entity ("NJIT's parking", "GSA's officers") are NOT bare pronouns → skip. Zero LLM cost on standalone Qs.
2. **Rewrite (one LLM call, temp 0.0).** `rewrite_with_context(history, message)` → one standalone question. Prompt
   rules: resolve references using ONLY named entities that appear literally in the history; **do NOT change the
   question TYPE/intent** (resolve "his position"→"Mark Cartwright's position", never →"tell me about Cartwright");
   **if ≥2 entities could be the referent, return the message UNCHANGED (ambiguity → passthrough, never guess)**. [RA2]
3. **Deterministic entity-membership verification (THE load-bearing guard) [RA1].** After the rewrite, extract the
   entity/proper-noun tokens the rewrite ADDED (present in the rewrite but not in the original message). If any added
   token does NOT appear literally in the history text → **DISCARD the rewrite, passthrough the original.** This is
   the analog of the live-fallback's "spans must appear literally on the page" rule — it converts a hallucinated
   antecedent into a safe passthrough. Also reject if the rewrite drops the original interrogative or balloons in
   length (intent-change guard).
4. **The resolved query feeds ALL routing/retrieval consumers [SE2]:** `UnifiedRouter.decide()`, `_legacy_family`
   (shadow), legacy `_try_structured`, AND the RAG retriever. **`_rag_pipeline` gets a NEW separate search-query
   parameter [SE1] — do NOT overload its `clean_text` arg**, which must stay the ORIGINAL for compose, logging
   (`questions.question_text`), history (`add_turn`), and officer-name matching.
5. **Compose sees BOTH [RA3].** Keep `question=original` for display fidelity, AND add one line to the compose
   prompt: `"(resolved for retrieval: <resolved>)"` — so the question the model answers matches the chunks it was
   given (closes the split-brain risk where compose resolves a pronoun differently than retrieval did).
6. **Skip in free mode [SE3].** Read mode early; do not rewrite when `mode == "free"` (direct LLM chat, no retrieval).
7. **Fallback = today's behavior.** Empty/failed/ambiguous/discarded/unchanged rewrite → raw query. Never worse than
   now. Generalizes the history-less `expand_query` (WRAP, don't replace — officer-name expansion + the ≤3-word
   `expand_query` stay, composing after the rewrite). [SE4]
8. **Log the `original → resolved` (+ gated/passthrough/discarded) pair [RA5]** so the 👎 audit loop (accuracy
   backlog #1/#3) can see rewrite quality — otherwise a bad rewrite is invisible (`question_text` keeps the original).

## 4. Placement (verified by review)
Inject the gate+rewrite right after the empty-text guard and BEFORE the explicit-live block at the top of `handle()`,
producing `resolved_text`. Thread `resolved_text` into `decide()`, `_legacy_family` (shadow), `_try_structured`, and
`_rag_pipeline`'s new search param; keep `clean_text` (original) everywhere user-facing. History is read-only and at
that seam does NOT yet include the current turn (it's appended only after answering at ~L705/L524/L585 — verified, no
double-count). NOTE (acknowledged, not silently dropped): `_answer_decision`'s KG→empty RAG fallback won't carry the
resolved query — acceptable (KG-empty is rare); flag it.

## 5. Anti-fabrication / safety
- It's a QUERY transform → cannot inject a fabricated FACT into the answer (compose still runs only over retrieved
  chunks/structured facts with their anti-fab clauses). Confirmed by both reviews.
- The ONE new risk is a **confident WRONG rewrite** (resolving to the wrong entity) — which downstream honest-partial
  can't catch (it fires on absence, not wrong-presence). **Mitigated by §3.3 entity-membership verification +
  §3.2 ambiguity-passthrough**, NOT by "unresolved→passthrough" alone. This is the required, load-bearing guard.
- Temp 0.0; ≤5 turns; one LLM call only when the gate fires; passthrough on any doubt; original wording shown/logged.

## 6. Risks
- **Confident wrong rewrite** (the bug relocated) → §3.3 entity-membership check + ambiguity-passthrough. PRIMARY risk.
- **Over-trigger** on a standalone question → conservative referential gate + entity-check makes a misfire a near-no-op.
- **Split-brain compose** (model resolves pronoun differently than retrieval) → §3.5 "resolved for retrieval" line.
- **Latency** — one extra LLM call on follow-ups only.

## 7. Test plan (TDD) — multi-turn fixtures, not bare eval lines [RA4]
Follow-up tests MUST seed `conversation_manager` with the prior turn (a bare line in `eval/questions.txt` has no
history → silently exercises passthrough and proves nothing). Cases:
- **Gate:** referential signal (bare pronoun / elliptical opener) + history → attempt; "who is the GSA president"
  (standalone) + history → gate does NOT fire, zero LLM call, route identical to baseline; "office hours" → no fire.
- **Resolve:** history(Cartwright) + "what is his position" → resolved contains "Cartwright", routes to entity/role
  skill (not RAG-on-"his"). history(CS dept) + "what about for BME?" → standalone BME dept query, same skill family.
- **Ambiguity passthrough:** history with TWO people + "why didn't you list him" → passthrough, NOT a confident pick.
- **Entity-membership discard:** stub returns a rewrite naming an entity ABSENT from history → guard discards →
  raw query used.
- **Compose consistency:** resolved query reaches compose as the "(resolved for retrieval: …)" line.
- **Free mode:** mode=="free" → no rewrite.
- Use a **stub LLM** for route-stability (not the live model). **Regression:** baseline suite byte-identical with
  the gate off-path.

## 8. Goals checklist (verify at build)
- [ ] Deterministic **referential** gate (bare pronoun/elliptical opener; not mere shortness; no LLM on standalone) — [SE4/RA5]
- [ ] `rewrite_with_context` (temp 0.0, ≤5 turns, resolve-only-from-history, no intent change, ambiguity→passthrough) — [RA2]
- [ ] **Entity-membership verification**: added entity tokens must appear in history else discard → passthrough — [RA1, REQUIRED]
- [ ] Resolved query feeds `decide()` + `_legacy_family` + `_try_structured` + retriever; **`_rag_pipeline` gets a NEW
      search-query param** (clean_text stays original for compose/log/history/officer-match) — [SE1, SE2]
- [ ] Compose gets a "(resolved for retrieval: …)" line; original stays the displayed/logged question — [RA3]
- [ ] Skip rewrite when `mode == "free"` — [SE3]
- [ ] Log `original → resolved` (+ outcome) for the 👎 audit loop — [RA5]
- [ ] Fallback never-worse-than-today; `expand_query` + officer-expansion WRAPPED, not replaced — [SE4]
- [ ] **Multi-turn test fixtures** (history-seeded): resolve, ambiguity-passthrough, entity-discard, free-mode, compose,
      standalone-no-op; stub-LLM for route stability; zero regression on standalone — [RA4]

## 9. Design-review record (2026-06-22)
Two background expert reviews — **both BUILD-WITH-FIXES** (architecture/placement SOUND; the rewrite is a query
transform, not a fabricated-fact path; honest-partial + empty→RAG guards hold). All fixes folded above, tagged
`[SE#]`/`[RA#]`:
- **SE1** `_rag_pipeline` needs a SEPARATE search param (don't overload `clean_text` — it's reused for compose/log/
  history/officer-match). **SE2** thread resolved query into decide()/_legacy_family/_try_structured/retriever.
  **SE3** skip free mode. **SE4** gate on referential signal (not bare "that/those"); WRAP expand_query.
- **RA1 (load-bearing)** deterministic entity-membership check: added entities must be in history else discard →
  passthrough (the guard against a *confident wrong rewrite* — "passthrough if unresolvable" alone is insufficient).
  **RA2** ambiguity→passthrough + no intent change. **RA3** feed resolved query to compose as an added line (split-brain
  fix). **RA4** eval needs multi-turn fixtures, not bare lines. **RA5** log original→resolved; referential gate; temp 0.0.

*Next: owner approval → TDD build in a worktree off main → diff → sign-off → merge + restart.*
