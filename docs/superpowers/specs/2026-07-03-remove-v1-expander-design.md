# Thread B — remove the v1 LLM query-expander (lean delta-spec)

**Date:** 2026-07-03
**Author:** Kavosh maintenance (Mohammad Dindoost, owner) — plan ruled by Fable (binding).
**Status:** Fable plan sign-off → measured → TDD build → owner merge/restart GO.
**Scope:** `bot/core/message_handler.py` (delete the expander branch) + `bot/services/ollama_client.py`
(delete the method + two prompt constants) + tests + `eval/questions.txt`.
**Workstream:** short-query correctness (last thread). A✅ D+E✅ F✅ → **B (here)**.

---

## 1. Problem

The v1 LLM short-query expander (`OllamaClient.expand_query`, driven by `_EXPAND_SYSTEM` /
`_EXPAND_EXAMPLES`) rewrites **every** short (≤3-word) non-officer query into a question framed
around "NJIT GSA (Graduate Student Association) services, events, funding, or workshops." On the v1
GSA-centric corpus that was a safe wrap; on the now **university-wide** corpus it is a GSA thumb —
it mis-frames non-GSA queries. It is the same GSA-bias class the workstream rejected (the alias
table, and the reason F's branch-2 hint is static). It also hardcodes 6 GSA officer first names.

**Fresh live measurement (2026-07-03, real pipeline, post-Qwen/Granite rebuild)** — BEFORE = expand
then retrieve; AFTER = retrieve base_q directly. 12 of 14 short queries were GSA-framed by the
expander; AFTER is dramatically better for non-GSA queries and no worse for GSA/ambiguous ones:

| query | BEFORE (expander) | AFTER (base_q) |
|---|---|---|
| computer science phd | GSA bylaws / MMI / travel ✗ | Ph.D. in Computer Science ✓ |
| game development lab | generic Student Life ✗ | Game Dev Specialization / Minor ✓ |
| machine learning | GSA bylaws / MMI ✗ | ML faculty (Wang, Nguyen) ✓ |
| transcripts | GSA travel / bylaws ✗ | Transcript Requests / Registrar ✓ |
| financial aid | GSA financial support ✗ | Financial Aid / Student Financial Aid ✓ |
| library hours | GSA office/contact ✗ | NJIT Library ✓ |
| parking | GSA orientation/bylaws ✗ | Campus Parking Map ✓ |
| deadline | GSA bylaws ✗ | Dates and Deadlines ✓ |
| mmi / workshop / travel award | GSA/MMI | MMI pages / GSA packet still surfaces ✓ |
| money / funding / fun | GSA-framed | ambiguous-but-not-wrong (GSA-framing WAS the bias) |

Bar (Fable): "removal must not make any case worse." Met — removal fixes the majority and the only
"different" cases are genuinely-ambiguous bare terms where GSA-framing was the harm.

## 2. Change

Delete, in full (Fable condition #1 — the GSA-framing prompt must not merely be uncalled, it must
not EXIST, else it is a latent re-introduction hazard):

- `message_handler.py`: the `elif self.ollama and len(words) <= 3 and intent not in (FOOD,SOCIAL):
  expanded = await self.ollama.expand_query(base_q); if expanded and expanded.lower()!=base_q.lower():
  search_query = expanded` branch (+ the now-unused `words = base_q.split()`). A short non-officer
  query now retrieves on `base_q` verbatim. Replaced by a NOTE comment recording the removal and the
  SE4 reversal.
- `ollama_client.py`: the `expand_query` method + `_EXPAND_SYSTEM` + `_EXPAND_EXAMPLES`.

Evidence (Fable condition #2 — no other live importer): repo-wide grep (excluding `.claude/worktrees/*`)
shows the ONLY live call site is `message_handler.py`; the constants are used only inside the method;
the 4 test files merely mock `ollama.expand_query`. **Flagged separate cleanup (NOT B):**
`bot/services/search.py:_expand_query` is a DIFFERENT symbol (a v1 search-term helper returning
`list[str]`); `SearchService` is still imported in `bot/main.py` — likely v1-dead post-cut, its own
cleanup task, untouched here.

**SE4 reversal (Fable condition #4):** this reverses the 2026-06-22 [SE4] "WRAP don't replace"
contextual-rewrite decision. Correct now because SE4 wrapped to preserve context on a GSA-centric
corpus; the corpus is NJIT-wide, so the GSA wrapper became the bias it was meant to avoid. Noted in
the commit and the code comment.

## 3. Not in scope — the committed NEXT thread

`is_officer_query` + `_OFFICER_FIRST_NAMES` (`message_handler.py`) is the SAME v1 GSA-framing pattern
(6 hardcoded GSA first names incl. the owner's; rewrites → "Who is {Name} at GSA NJIT?"). Fable ruled
it a **committed successor thread, not folded into B**, with a measure-first entry criterion: does the
deterministic path (D+E+F + person resolution) resolve each bare FIRST name (`mohammad`, `fernando`,
`mohith`, `durvish`, `nistha`, `ritwik`) to the correct person? `persons_by_lastname` resolves
SURNAMES, so first-name resolution is genuinely open. If yes → delete the block; if no → make
first-name resolution deterministic, then delete. Either way the hardcoded-names block goes.

## 4. Testing (TDD)

- `test_expander_removed.py`: `expand_query` method gone; `_EXPAND_SYSTEM`/`_EXPAND_EXAMPLES` gone;
  no residual GSA-framing prompt text in the module.
- `test_message_handler.py::test_short_query_not_gsa_reframed`: with a mock ollama whose
  `expand_query` returns a GSA-reframe, a short query ("machine learning") is still retrieved
  VERBATIM and `expand_query` is never called (RED before removal, GREEN after).
- Eval regressions (`eval/questions.txt`): `computer science phd`, `game development lab`,
  `transcripts`, `library hours`, `parking` — short non-GSA queries that must retrieve un-GSA-framed.
- The 4 mock-only test files stay green (the mock attribute is simply never read).

## 5. Goals checklist

| Goal | Status |
|---|---|
| Remove the GSA-framing short-query expander (call + definitions) | **ship** |
| Short non-GSA queries retrieve verbatim (no GSA thumb) | **ship** (measured) |
| No regression on GSA/ambiguous short queries | **ship** (measured — no case worse) |
| `is_officer_query` (6 hardcoded names) | **deferred — committed next thread** (measure-first) |
| `bot/services/search.py:_expand_query` (v1 helper) | **flagged separate cleanup** — not B |
