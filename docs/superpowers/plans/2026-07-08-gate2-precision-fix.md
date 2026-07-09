# Gate-2 Precision Fix (Positive-Span Reframe) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Flip the WS4 Gate-2 answerability check from a negative-global criterion ("is the complete answer to the whole question present?") to a positive-span criterion ("is there a grounded quote responsive to the question's primary ask?"), recovering the 81% false-abstain rate measured in the `gate2:not-in-context` bucket without raising false-answer.

**Architecture:** A prompt + label-semantics change to `_GATE2_SYSTEM` (the only Gate-2 prompt), plus a compose-prompt guard so a newly-surfaced compound question does not echo an ungrounded time qualifier. The decision functions (`decide_after_gate2`), the grounding guard (`robust_grounded`), the typed-value path (`assess_pre_gate2`), and parse-fail→abstain are unchanged. Correctness is proven by a frozen 47-case regression fixture (full-gate replay) plus the WS4 both-directions eval as a merge-blocking gate.

**Tech Stack:** Python 3.11, pytest, Ollama (`granite4:tiny-h` gen/verify at temp 0.0), SQLite.

## Global Constraints

- Ships behind the EXISTING `ANSWER_GATE_ENABLED` flag (already `=1` live) — **no new flag**. Merge = live; the diff goes to the owner before merge + restart.
- LLM-agnostic (hard line): the *prompt* must be model-robust; the *threshold numbers* (34/38, ≤15%) are model-specific and re-baselined on any LLM swap.
- Never fabricate an unheld attribute (honest-partial); never withhold real NJIT content. The compose guard (Task 3) is what keeps the compound-partial behavior on the right side of both.
- No commit attribution trailers; stage explicit paths only, never `git add -A` (untracked `.env*` backups hold live secrets).
- Gate-2 runs with `fmt="json"` — any chain-of-thought step MUST be a JSON field or it is silently dropped.
- Keep the literal substrings `"NOT_IN_CONTEXT"` and `"quote"` in `_GATE2_SYSTEM` so `test_gate2_prompt_includes_question_and_context` stays green.
- Spec: `docs/superpowers/specs/2026-07-08-gate2-precision-fix-design.md` (rev 3).

---

## File Structure

- `v2/core/retrieval/answer_gate.py` — `_GATE2_SYSTEM` (~:122). The one prompt. Reframed. Module docstring (~:11-15) updated to describe positive-span.
- `v2/core/retrieval/faithfulness.py` — `decide_after_gate2` (~:221) docstring only; NEW optional helper `answer_uses_quote()` IF the coupling check is adopted (Task 5).
- `bot/services/ollama_client.py` — `BASE_SYSTEM_PROMPT` (~:95-163): NEW rule 14 (time/schedule-qualifier guard) for the prose RAG compose path (`generate_answer`).
- `v2/tests/test_answer_gate.py` — extend: prompt-shape + parse-with-primary_ask tests.
- `v2/tests/test_faithfulness.py` (create if absent) — Layer-1 decision unit tests.
- `bot/tests/test_compose_guard.py` (create) — asserts rule 14 present in `BASE_SYSTEM_PROMPT`.
- `eval/processing_debt/build_gate2_fixture.py` (create) — re-capture → validate vs diagnostic → freeze.
- `eval/processing_debt/out/gate2_fixture_frozen.jsonl` (generated, committed) — the frozen replay fixture.
- `eval/processing_debt/measure_answer_quote_coupling.py` (create) — coupling distribution + adopt/decline recommendation.
- `v2/tests/test_gate2_regression.py` (create) — Layer-2 full-gate replay over the frozen fixture; `@pytest.mark.integration`.

---

### Task 0: Branch

- [ ] **Step 1: Create the build branch off main**

```bash
cd /home/md724/gsa-gateway
git fetch origin
git checkout -b feat/gate2-precision-fix origin/main
```

Expected: `Switched to a new branch 'feat/gate2-precision-fix'`. (The spec/fixture commits live on `feat/processing-debt-pilot`; the CODE build is isolated here off `main`.)

- [ ] **Step 2: Copy the spec + labeled fixture onto this branch so the build has them**

```bash
git checkout feat/processing-debt-pilot -- \
  docs/superpowers/specs/2026-07-08-gate2-precision-fix-design.md \
  eval/processing_debt/out/gate2_fixture_labeled.jsonl \
  eval/processing_debt/out/gate2_fixture_labeled.md \
  eval/processing_debt/out/prose_gate_diag.jsonl \
  docs/superpowers/plans/2026-07-08-gate2-precision-fix.md
git commit -q -m "docs(gate2): bring precision-fix spec, plan, and fixtures onto build branch"
```

Expected: one commit adding the spec, plan, labeled fixture, and the diagnostic record (the drift oracle for Task 4).

---

### Task 1: Reframe `_GATE2_SYSTEM` to positive-span

**Files:**
- Modify: `v2/core/retrieval/answer_gate.py:122-129` (`_GATE2_SYSTEM`), `:10-15` (module docstring).
- Test: `v2/tests/test_answer_gate.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_GATE2_SYSTEM` (str) — now instructs the model to emit `{"primary_ask","supporting_quote","label","missing_piece"}` with `primary_ask` first; `label ∈ {FULLY_SUPPORTED, PARTIALLY_SUPPORTED, NOT_IN_CONTEXT}` decided against the PRIMARY ask. `gate2_prompt()` and `parse_gate2()` signatures unchanged.

- [ ] **Step 1: Write the failing prompt-shape test**

Add to `v2/tests/test_answer_gate.py`:

```python
def test_gate2_prompt_is_positive_span_primary_ask():
    sys_p, user_p = gate2_prompt("who teaches cs 634 next semester", ["CS 634 is taught by Prof. X."])
    # positive-span framing: asks for the PRIMARY need and a primary-first JSON schema
    assert "primary_ask" in sys_p
    assert sys_p.index("primary_ask") < sys_p.index("supporting_quote")  # primary_ask emitted FIRST
    assert "PARTIALLY_SUPPORTED" in sys_p and "NOT_IN_CONTEXT" in sys_p
    assert "quote" in sys_p  # keeps the existing substring contract
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest v2/tests/test_answer_gate.py::test_gate2_prompt_is_positive_span_primary_ask -v`
Expected: FAIL (`assert "primary_ask" in sys_p` — the current prompt has no such field).

- [ ] **Step 3: Reframe the prompt**

Replace `_GATE2_SYSTEM` (answer_gate.py:122-129) with:

```python
_GATE2_SYSTEM = (
    "You are a strict grounding checker. A question has a PRIMARY information need and may add "
    "secondary details. Decide whether the CONTEXT answers the question's PRIMARY need. Steps: "
    "(1) state the question's primary_ask in one short clause; (2) copy one or more verbatim "
    "supporting quotes from the context that directly answer that primary_ask — if no sentence "
    "answers it, leave the quote empty; a topic merely being mentioned is NOT support; (3) assign "
    "a label: FULLY_SUPPORTED (the primary_ask is fully answered), PARTIALLY_SUPPORTED (the "
    "primary_ask is answered but a secondary detail is missing or unconfirmed), or NOT_IN_CONTEXT "
    "(no quote answers the primary_ask). Respond with ONLY a JSON object, primary_ask FIRST: "
    '{"primary_ask": "...", "supporting_quote": "...", "label": "...", "missing_piece": "..."}'
)
```

- [ ] **Step 4: Update the module docstring to match**

In `answer_gate.py:11-15`, replace the Gate-2 bullet's "require a verbatim supporting quote BEFORE a label" sentence's framing note. Change the phrase `evidence-first GRADED: require a verbatim supporting quote BEFORE a label` region to add one sentence at the end of that bullet (after line 15, before the closing of the bullet):

```
           The label is decided against the question's PRIMARY ask (positive-span): FULLY/PARTIALLY_
           SUPPORTED when a grounded quote answers the primary need, NOT_IN_CONTEXT only when none does
           (2026-07-08 precision fix — was negative-global, which over-abstained on compound/partial Qs).
```

- [ ] **Step 5: Run the new test + the existing prompt test + the size-bound test**

Run: `python3 -m pytest v2/tests/test_answer_gate.py -k "gate2_prompt" -v && python3 -m pytest bot/tests/test_quick_wins_w2_a6.py::test_a6_gate2_prompt_size_bounded -v`
Expected: all PASS (new test green; `test_gate2_prompt_includes_question_and_context` still green because "quote" + question + context remain; size bound unaffected — passage window is unchanged).

- [ ] **Step 6: Verify `parse_gate2` tolerates the new `primary_ask` field**

Add to `v2/tests/test_answer_gate.py`:

```python
def test_parse_gate2_ignores_primary_ask_field():
    raw = ('{"primary_ask": "who teaches cs 634", "supporting_quote": "CS 634 is taught by Prof. X.", '
           '"label": "PARTIALLY_SUPPORTED", "missing_piece": "which semester"}')
    v = parse_gate2(raw)
    assert v.label == "PARTIALLY_SUPPORTED"
    assert "Prof. X" in v.quote
    assert v.parsed is True
```

Run: `python3 -m pytest v2/tests/test_answer_gate.py::test_parse_gate2_ignores_primary_ask_field -v`
Expected: PASS (parse_gate2 reads label/supporting_quote/missing_piece; extra keys ignored).

- [ ] **Step 7: Commit**

```bash
git add v2/core/retrieval/answer_gate.py v2/tests/test_answer_gate.py
git commit -m "feat(gate2): reframe answerability check to positive-span (primary-ask)"
```

---

### Task 2: `decide_after_gate2` — Layer-1 decision unit tests + docstring

No logic changes. `PARTIALLY_SUPPORTED` already routes to answer; this task locks that contract with tests and documents the new meaning.

**Files:**
- Modify: `v2/core/retrieval/faithfulness.py:221-237` (docstring only).
- Test: `v2/tests/test_faithfulness.py` (create if absent).

**Interfaces:**
- Consumes: `decide_after_gate2(gate2_label, gate2_quote, passages, parsed=True) -> (outcome, reason)`, `robust_grounded`.
- Produces: nothing new.

- [ ] **Step 1: Write the failing Layer-1 tests**

Create `v2/tests/test_faithfulness.py` (or append if it exists):

```python
from v2.core.retrieval.faithfulness import decide_after_gate2

_CTX = ["To drop a class, submit the withdrawal form to the Registrar before the deadline."]

def test_partial_support_with_grounded_quote_answers():
    # compound: primary answered (how to drop), secondary (exact deadline) missing -> ANSWER
    out, reason = decide_after_gate2("PARTIALLY_SUPPORTED", "submit the withdrawal form to the Registrar", _CTX)
    assert out == "answer"

def test_full_support_with_grounded_quote_answers():
    out, _ = decide_after_gate2("FULLY_SUPPORTED", "submit the withdrawal form to the Registrar", _CTX)
    assert out == "answer"

def test_not_in_context_abstains():
    out, reason = decide_after_gate2("NOT_IN_CONTEXT", "", _CTX)
    assert out == "abstain" and reason == "gate2:not-in-context"

def test_supported_but_ungrounded_quote_abstains():
    out, reason = decide_after_gate2("FULLY_SUPPORTED", "tuition is due in the patent office", _CTX)
    assert out == "abstain" and reason == "gate2:unsupported"

def test_parse_fail_abstains_even_if_supported():
    out, reason = decide_after_gate2("FULLY_SUPPORTED", "submit the withdrawal form", _CTX, parsed=False)
    assert out == "abstain" and reason == "gate2:unsupported"
```

- [ ] **Step 2: Run to verify status**

Run: `python3 -m pytest v2/tests/test_faithfulness.py -v`
Expected: all PASS immediately (this is a characterization/lock test — `decide_after_gate2` already behaves this way). If any FAIL, STOP: the reframe assumption ("only the LLM label was wrong, decision logic is correct") is violated — report before continuing.

- [ ] **Step 3: Update the docstring**

In `faithfulness.py`, append to the `decide_after_gate2` docstring (after the existing NOTE block, before the code at ~:233) one line:

```python
    # Positive-span reframe (2026-07-08): PARTIALLY_SUPPORTED means "the question's PRIMARY ask is
    # answered though a secondary detail is missing" — so compound questions surface here instead of
    # dying as NOT_IN_CONTEXT. The abstain/answer wiring below is unchanged; only the LLM's label
    # criterion (in _GATE2_SYSTEM) moved.
```

- [ ] **Step 4: Re-run**

Run: `python3 -m pytest v2/tests/test_faithfulness.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add v2/core/retrieval/faithfulness.py v2/tests/test_faithfulness.py
git commit -m "test(gate2): lock PARTIALLY_SUPPORTED->answer contract; document positive-span"
```

---

### Task 3: Compose time/schedule-qualifier guard (rule 14)

Fable's mitigation: the prose compose prompt must not echo an ungrounded time qualifier the reframe newly surfaces (e.g. "next semester").

**Files:**
- Modify: `bot/services/ollama_client.py:160-163` (end of `BASE_SYSTEM_PROMPT`, add rule 14).
- Test: `bot/tests/test_compose_guard.py` (create).

**Interfaces:**
- Consumes: `BASE_SYSTEM_PROMPT` (str) — used by `generate_answer` (the gated prose RAG path, `message_handler.py:1173`).
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Create `bot/tests/test_compose_guard.py`:

```python
from bot.services.ollama_client import BASE_SYSTEM_PROMPT

def test_base_prompt_has_time_qualifier_guard():
    p = BASE_SYSTEM_PROMPT.lower()
    assert "time or schedule qualifier" in p
    assert "next semester" in p
    # must instruct NOT to assert an unconfirmed qualifier
    assert "do not assert" in p
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest bot/tests/test_compose_guard.py -v`
Expected: FAIL (`assert "time or schedule qualifier" in p`).

- [ ] **Step 3: Add rule 14 to `BASE_SYSTEM_PROMPT`**

In `bot/services/ollama_client.py`, change the end of rule 13 (line 162, which currently ends `"...or use 'they/them'."` and closes the string with `)` on line 163) to continue with rule 14. Replace:

```python
    "13. Never use gendered pronouns (he/him/his/she/her/hers) for a person unless their gender "
    "is explicitly stated in the context — the documents do not record gender, so assuming one is "
    "fabrication. Refer to a person by name or use 'they/them'."
)
```

with:

```python
    "13. Never use gendered pronouns (he/him/his/she/her/hers) for a person unless their gender "
    "is explicitly stated in the context — the documents do not record gender, so assuming one is "
    "fabrication. Refer to a person by name or use 'they/them'.\n"
    "14. If the student's question contains a time or schedule qualifier (for example 'next "
    "semester', 'this fall', 'in spring 2026') that the provided documents do NOT confirm, do NOT "
    "assert that qualifier as fact. Answer what the documents DO state and add that per-semester "
    "scheduling is not in our data. Example: asked who teaches CS 634 next semester when the "
    "documents give the instructor but not the semester — name the instructor and say you don't "
    "have next-semester scheduling."
)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest bot/tests/test_compose_guard.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/services/ollama_client.py bot/tests/test_compose_guard.py
git commit -m "feat(compose): guard against echoing an ungrounded time/schedule qualifier (rule 14)"
```

---

### Task 4: Build the frozen regression fixture

Re-capture full passages + untruncated answers for the 47 labeled cases, validate each against the diagnostic record (drift oracle), and freeze a committed replay fixture.

**Files:**
- Create: `eval/processing_debt/build_gate2_fixture.py`
- Read: `eval/processing_debt/out/gate2_fixture_labeled.jsonl`, `eval/processing_debt/out/prose_gate_diag.jsonl`
- Create (generated, committed): `eval/processing_debt/out/gate2_fixture_frozen.jsonl`

**Interfaces:**
- Consumes: the same assistant wiring the diagnostic used (`bot.core.assistant.build_assistant`, `R.retrieve`, `ollama.generate_answer`). Frozen record schema: `{"i","q","expected","passages":[str],"answer":str,"rank1":str,"rel":float,"drift":bool}`.
- Produces: the frozen fixture consumed by Task 5 and Task 6.

- [ ] **Step 1: Write the fixture builder**

Create `eval/processing_debt/build_gate2_fixture.py`:

```python
"""Freeze the 47-case Gate-2 regression fixture: re-capture full passages + untruncated answers,
flag drift vs the diagnostic record, and write a committed replay fixture. Run as a module:
    LIVE_ENABLED=0 python3 -m eval.processing_debt.build_gate2_fixture
"""
from __future__ import annotations
import asyncio, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from bot.config import config, LIVE_THRESHOLD
import bot.config as botcfg
from bot.services.database import Database
from bot.services.knowledge_base import KnowledgeBase
from bot.services.moderation import RateLimiter

LABELED = REPO / "eval/processing_debt/out/gate2_fixture_labeled.jsonl"
DIAG = REPO / "eval/processing_debt/out/prose_gate_diag.jsonl"
OUT = REPO / "eval/processing_debt/out/gate2_fixture_frozen.jsonl"


async def main() -> int:
    labeled = [json.loads(l) for l in open(LABELED)]
    diag = {r["i"]: r for r in (json.loads(l) for l in open(DIAG))}

    botcfg.ANSWER_GATE_ENABLED = True
    db = Database(config.database_path); db.connect(); db.init_tables(); db.migrate_rag_columns()
    kb = KnowledgeBase(data_dir=config.data_dir); kb.load()
    rl = RateLimiter(max_calls=100000, period_seconds=1)
    from bot.core.assistant import build_assistant
    asst = await build_assistant(config, db, kb, rl)
    R = asst.retriever
    ollama = asst.ollama

    out = open(OUT, "w", encoding="utf-8")
    ndrift = 0
    for r in labeled:
        i, q = r["i"], r["q"]
        chunks = await R.retrieve(q)              # default corpus, same as the gated path
        passages = [(getattr(c, "text", "") or "") for c in (chunks or [])[:8]]
        rel = R.top_relevance(q, chunks, skip_unscored=True) if chunks else None
        rank1 = ""
        if chunks:
            c0 = chunks[0]
            rank1 = (getattr(c0, "source", None) or getattr(c0, "title", None)
                     or str(getattr(c0, "metadata", ""))[:60])[:70]
        answer = await ollama.generate_answer(q, chunks[:8]) if chunks else ""
        # drift oracle: did rank-1 identity change vs the diagnostic?
        old = diag.get(i, {})
        drift = bool(old) and (rank1 != old.get("rank1", rank1))
        if drift:
            ndrift += 1
        out.write(json.dumps({"i": i, "q": q, "expected": r["expected"],
                              "passages": passages, "answer": answer or "",
                              "rank1": rank1, "rel": round(rel, 3) if rel is not None else None,
                              "drift": drift}, ensure_ascii=False) + "\n")
        print(f"[{i}] drift={drift} expected={r['expected']} rel={rel} {q[:44]}")
    out.close()
    print(f"\nwrote {OUT} ; drifted cases (re-adjudicate by hand): {ndrift}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

> NOTE on `generate_answer` args: it is `async def generate_answer(self, question, chunks, conversation_history=None, ...)`. Confirm the positional signature in `bot/services/ollama_client.py:357` before running; adjust the call if the real signature differs.

- [ ] **Step 2: Run the builder**

Run: `cd /home/md724/gsa-gateway && LIVE_ENABLED=0 python3 -m eval.processing_debt.build_gate2_fixture`
Expected: 47 lines printed with `drift=True/False`; `gate2_fixture_frozen.jsonl` written; a final drift count.

- [ ] **Step 3: Hand-re-adjudicate drifted + boundary cases**

Open `gate2_fixture_frozen.jsonl`. For every `drift=True` case AND for #4, #43, #45 specifically: read the captured `passages` + `answer` and confirm `expected` still matches the label rationale in `gate2_fixture_labeled.md`. If a case now has a genuinely responsive span (e.g. #4's "before working off-campus…"), flip its `expected` to `keep` and record the flip in a comment block at the top of the file:

```bash
python3 - <<'PY'
# after manual review, apply any label flips here, e.g.:
import json
P="eval/processing_debt/out/gate2_fixture_frozen.jsonl"
rows=[json.loads(l) for l in open(P)]
flips={}   # e.g. {4: "keep"}  -- fill from manual review
for r in rows:
    if r["i"] in flips: r["expected"]=flips[r["i"]]
open(P,"w").write("\n".join(json.dumps(r,ensure_ascii=False) for r in rows)+"\n")
print("flips applied:", flips)
PY
```

If any flip is applied, note it in the PR's goals checklist (boundary re-adjudication).

- [ ] **Step 4: Commit the frozen fixture + builder**

```bash
git add eval/processing_debt/build_gate2_fixture.py eval/processing_debt/out/gate2_fixture_frozen.jsonl
git commit -m "test(gate2): freeze 47-case regression fixture (passages+answers, drift-validated)"
```

---

### Task 5: Answer↔quote coupling — measure, then adopt or decline

`robust_grounded` verifies the checker's quote, not the composed answer. Measure whether requiring the served answer to actually use the responsive span cleanly separates good answers from grounded-but-irrelevant paste. Adopt only if the data supports a non-arbitrary threshold.

**Files:**
- Create: `eval/processing_debt/measure_answer_quote_coupling.py`
- Modify (ONLY IF adopted): `v2/core/retrieval/faithfulness.py` (add `answer_uses_quote`), `bot/core/message_handler.py:~1210` (call it after a gate "answer" verdict), plus a unit test.

**Interfaces:**
- Consumes: frozen fixture, `gate2_prompt`, `parse_gate2`, `robust_grounded`, `_norm`.
- Produces (if adopted): `answer_uses_quote(answer: str, quote: str, min_overlap: float) -> bool` in faithfulness.py.

- [ ] **Step 1: Write the measurement script**

Create `eval/processing_debt/measure_answer_quote_coupling.py`:

```python
"""Measure token-set overlap(checker_quote, composed_answer) over the 38 KEEP cases vs a synthetic
grounded-but-irrelevant probe. Prints the distribution + an adopt/decline recommendation.
    LIVE_ENABLED=0 python3 -m eval.processing_debt.measure_answer_quote_coupling
"""
from __future__ import annotations
import asyncio, json, sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(REPO))
from bot.config import config
import bot.config as botcfg
from v2.core.retrieval.answer_gate import gate2_prompt, parse_gate2
from v2.core.retrieval.faithfulness import _norm, robust_grounded

FROZEN = REPO / "eval/processing_debt/out/gate2_fixture_frozen.jsonl"

def overlap(quote: str, answer: str) -> float:
    q = set(_norm(quote).split()); a = set(_norm(answer).split())
    return (len(q & a) / len(q)) if q else 0.0

async def main() -> int:
    botcfg.ANSWER_GATE_ENABLED = True
    rows = [json.loads(l) for l in open(FROZEN) if json.loads(l)["expected"] == "keep"]
    from bot.services.database import Database
    from bot.services.knowledge_base import KnowledgeBase
    from bot.services.moderation import RateLimiter
    db = Database(config.database_path); db.connect(); db.init_tables(); db.migrate_rag_columns()
    kb = KnowledgeBase(data_dir=config.data_dir); kb.load()
    asst = await __import__("bot.core.assistant", fromlist=["build_assistant"]).build_assistant(
        config, db, kb, RateLimiter(max_calls=100000, period_seconds=1))
    ollama = asst.ollama
    scores = []
    for r in rows:
        sys_p, usr_p = gate2_prompt(r["q"], [p[:1200] for p in r["passages"][:5]])
        raw = await ollama.generate(prompt=usr_p, system=sys_p,
                                    options={"temperature": 0.0, "num_predict": 256,
                                             "num_ctx": getattr(ollama, "num_ctx", 8192)}, fmt="json")
        v = parse_gate2(raw or "")
        if v.quote and robust_grounded(v.quote, r["passages"]):
            scores.append(overlap(v.quote, r["answer"]))
    scores.sort()
    n = len(scores)
    print(f"KEEP coupling overlap over {n} grounded cases:")
    if n:
        print(f"  min={scores[0]:.2f} p10={scores[max(0,n//10)]:.2f} median={scores[n//2]:.2f} max={scores[-1]:.2f}")
    print("\nRECOMMENDATION: adopt a coupling floor at ~p10 ONLY IF min is comfortably > 0 and the "
          "distribution is tight (few < 0.3). If several genuine keeps score low, DECLINE the check "
          "(Layer-3 remains the guard) and record it as an accepted-and-measured gap.")
    return 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 2: Run the measurement**

Run: `cd /home/md724/gsa-gateway && LIVE_ENABLED=0 python3 -m eval.processing_debt.measure_answer_quote_coupling`
Expected: a distribution line + recommendation. RECORD the numbers in the PR.

- [ ] **Step 3: Decide — adopt or decline**

- **DECLINE** (expected default if any genuine keep scores < 0.3): add nothing to the decision path; write a one-line note in the spec's goals checklist that the coupling check was measured and declined, Layer-3 is the guard. Skip to Task 6.
- **ADOPT** (only if the 38 cluster high with a clear floor): implement below.

- [ ] **Step 4 (ADOPT only): Write the failing helper test**

Add to `v2/tests/test_faithfulness.py`:

```python
from v2.core.retrieval.faithfulness import answer_uses_quote

def test_answer_uses_quote_true_when_answer_echoes_span():
    assert answer_uses_quote("The withdrawal form goes to the Registrar.",
                             "submit the withdrawal form to the Registrar", 0.4) is True

def test_answer_uses_quote_false_when_answer_ignores_span():
    assert answer_uses_quote("Patents are owned by the university.",
                             "submit the withdrawal form to the Registrar", 0.4) is False
```

- [ ] **Step 5 (ADOPT only): Implement `answer_uses_quote`**

Add to `faithfulness.py` (near `robust_grounded`):

```python
def answer_uses_quote(answer: str, quote: str, min_overlap: float) -> bool:
    """True iff the composed ANSWER actually uses the checker's grounded quote (token-set overlap).
    Closes the grounded-but-irrelevant-paste channel: the gate must serve an answer that responds to
    the span it verified. Threshold DERIVED from the 38-keep distribution (measure_answer_quote_coupling)."""
    q = set(_norm(quote).split())
    if not q:
        return True   # no quote to couple against -> don't add a new abstain path here
    a = set(_norm(answer).split())
    return (len(q & a) / len(q)) >= min_overlap
```

- [ ] **Step 6 (ADOPT only): Wire it into the gate**

In `bot/core/message_handler.py` `_faithfulness_gate`, the function ends at `:899` with
`return outcome == "answer", reason`, and `outcome`/`reason` come from `decide_after_gate2(...)` just
above it; `answer`, `v.quote`, and `full_passages` are all in scope. Insert the coupling check
immediately BEFORE that return (using the measured floor `_COUPLE_MIN`):

```python
            if outcome == "answer" and not faith.answer_uses_quote(answer, v.quote, _COUPLE_MIN):
                outcome, reason = "abstain", "gate2:answer-quote-decoupled"
        return outcome == "answer", reason
```

Define `_COUPLE_MIN = <measured p10>` as a module constant near the other gate constants, with a comment citing the measurement run.

- [ ] **Step 7 (ADOPT only): Run tests + commit**

Run: `python3 -m pytest v2/tests/test_faithfulness.py -v`
Expected: PASS.

```bash
git add v2/core/retrieval/faithfulness.py v2/tests/test_faithfulness.py bot/core/message_handler.py eval/processing_debt/measure_answer_quote_coupling.py
git commit -m "feat(gate2): answer<->quote coupling check (threshold from measured keep distribution)"
```

- [ ] **Step 8 (DECLINE path): Commit the measurement only**

```bash
git add eval/processing_debt/measure_answer_quote_coupling.py
git commit -m "test(gate2): measure answer<->quote coupling; declined (Layer-3 is the guard)"
```

---

### Task 6: Layer-2 regression test (full-gate replay)

Replay the FULL `_faithfulness_gate` over the frozen fixture. Hard: 9/9 guardrail abstain + ≥34/38 keep + ≥2 synthetic abstain.

**Files:**
- Create: `v2/tests/test_gate2_regression.py`
- Read: `eval/processing_debt/out/gate2_fixture_frozen.jsonl`

**Interfaces:**
- Consumes: `bot.core.assistant.build_assistant`, `handler._faithfulness_gate(question, answer, chunks) -> (keep: bool, reason: str)`. Chunks are wrapped so `getattr(c, "text", "")` returns a passage.

- [ ] **Step 1: Write the regression test**

Create `v2/tests/test_gate2_regression.py`:

```python
"""Layer-2 Gate-2 regression: replay the full faithfulness gate over the FROZEN fixture.
Integration/slow, model-pinned (granite4:tiny-h @ temp 0). Excluded from default CI.
Run: python3 -m pytest v2/tests/test_gate2_regression.py -v -m integration
"""
import json, types, asyncio
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parents[2]
FROZEN = REPO / "eval/processing_debt/out/gate2_fixture_frozen.jsonl"

pytestmark = pytest.mark.integration


def _chunk(text):
    return types.SimpleNamespace(text=text)


async def _build_handler():
    import bot.config as botcfg
    from bot.config import config
    from bot.services.database import Database
    from bot.services.knowledge_base import KnowledgeBase
    from bot.services.moderation import RateLimiter
    from bot.core.assistant import build_assistant
    botcfg.ANSWER_GATE_ENABLED = True
    db = Database(config.database_path); db.connect(); db.init_tables(); db.migrate_rag_columns()
    kb = KnowledgeBase(data_dir=config.data_dir); kb.load()
    asst = await build_assistant(config, db, kb, RateLimiter(max_calls=100000, period_seconds=1))
    return asst.message_handler


def _load():
    return [json.loads(l) for l in open(FROZEN)]


def test_gate2_regression_frozen_fixture():
    rows = _load()
    handler = asyncio.get_event_loop().run_until_complete(_build_handler())

    async def gate(r):
        chunks = [_chunk(p) for p in r["passages"]]
        keep, why = await handler._faithfulness_gate(r["q"], r["answer"], chunks)
        return keep, why

    results = asyncio.get_event_loop().run_until_complete(
        asyncio.gather(*[gate(r) for r in rows]))

    guardrails = [(r, res) for r, res in zip(rows, results) if r["expected"] == "abstain"]
    keeps = [(r, res) for r, res in zip(rows, results) if r["expected"] == "keep"]

    # HARD: every guardrail must still abstain
    leaked = [r["q"] for (r, (keep, _)) in guardrails if keep]
    assert not leaked, f"guardrail regressions (should abstain, kept): {leaked}"

    # >= 34/38 keeps must now answer
    kept = sum(1 for (_, (keep, _)) in keeps if keep)
    assert kept >= 34, f"only {kept}/{len(keeps)} keeps surfaced (need >=34)"


@pytest.mark.parametrize("q,answer,passages", [
    ("what is the opt policy", "Patents are owned by the university.",
     ["The university owns patents produced by employees."]),                 # fabricated vs on-topic-ish paste
    ("when is spring break", "Spring break is the third week of March.",
     ["The library is open until midnight during finals."]),                  # fabricated + off-topic ctx
])
def test_gate2_synthetic_must_abstain(q, answer, passages):
    handler = asyncio.get_event_loop().run_until_complete(_build_handler())
    chunks = [_chunk(p) for p in passages]
    keep, why = asyncio.get_event_loop().run_until_complete(
        handler._faithfulness_gate(q, answer, chunks))
    assert keep is False, f"synthetic fabrication should abstain, kept ({why})"
```

- [ ] **Step 2: Register the `integration` marker (avoid pytest warnings)**

Check `pyproject.toml`/`pytest.ini`/`setup.cfg` for a `markers` section; if `integration` is absent, add:

```ini
[pytest]
markers =
    integration: slow, model-dependent tests excluded from default CI
```

(If a config already defines markers, append the one line rather than creating a new file.)

- [ ] **Step 3: Run the regression suite**

Run: `cd /home/md724/gsa-gateway && python3 -m pytest v2/tests/test_gate2_regression.py -v -m integration`
Expected: PASS — no guardrail leak, ≥34/38 keeps surfaced, both synthetic cases abstain. If keeps < 34, iterate on `_GATE2_SYSTEM` wording (Task 1) — but do NOT weaken the guardrail/synthetic assertions to pass.

- [ ] **Step 4: Commit**

```bash
git add v2/tests/test_gate2_regression.py pyproject.toml
git commit -m "test(gate2): Layer-2 frozen-fixture regression (9/9 guardrail, >=34/38 keep, synthetic abstain)"
```

---

### Task 7: Layer-3 merge-blocking eval + #0/#54 mitigation check + full-suite green

**Files:**
- Read/run: the WS4 both-directions harness (`scratchpad/watch_traffic.py` per memory, or the WS4 eval referenced in `docs/superpowers/specs/2026-07-02-ws4-abstention-*`). Confirm the exact runner before this step.
- Run: full unit suite.

- [ ] **Step 1: Re-run the WS4 both-directions eval (MERGE GATE)**

Locate the WS4 eval runner (grep the WS4 spec + `eval/` for the false-answer/false-abstain harness). Run it with the gate ON on this branch.
Expected: **false-answer ≤ 15%** (must not exceed the WS4 baseline); false-abstain lower than the 20% WS4 baseline. RECORD both numbers in the PR. If false-answer > 15%, the reframe is too loose — STOP and revisit (consider adopting the Task-5 coupling check if it was declined).

- [ ] **Step 2: Confirm the #0/#54 mitigation behaves**

Run: `bash scripts/ask.sh "who teaching cs 634 next semester" --answer`
Expected: the answer NAMES the instructor and does NOT assert "next semester" as fact (it notes per-semester scheduling isn't available). Paste the answer into the PR.

- [ ] **Step 3: Run the full fast unit suite (no regressions)**

Run: `cd /home/md724/gsa-gateway && python3 -m pytest v2/tests/test_answer_gate.py v2/tests/test_faithfulness.py bot/tests/test_compose_guard.py bot/tests/test_quick_wins_w2_a6.py -q`
Expected: all PASS (integration tests are excluded by default without `-m integration`).

- [ ] **Step 4: Commit any doc/threshold notes + push**

```bash
git add -u
git commit -m "docs(gate2): record Layer-3 false-answer/false-abstain numbers + #0/#54 mitigation output"
git push -u origin feat/gate2-precision-fix
```

- [ ] **Step 5: STOP — owner diff gate**

Do NOT merge or restart. Present to the owner: the diff, the Layer-2 numbers (guardrail 9/9, keeps N/38), the Layer-3 false-answer/false-abstain deltas, the coupling adopt/decline decision, and the #0/#54 answer. Merge + restart only on the owner's explicit sign-off (merge = live because `ANSWER_GATE_ENABLED=1`).

---

## Post-merge (owner-gated, not part of TDD)

- Squash-merge `feat/gate2-precision-fix` → `main` (keeps branch commits off main).
- `bash scripts/restart.sh` (code change → restart required).
- Add the fixture questions to `eval/questions.txt` (grow-correctness-suite hard line).
- Update `docs/research/oracle-processing-debt/PROJECT_MEMORY.md` resume block.

## Self-Review

**Spec coverage:** Prompt reframe → Task 1. PARTIALLY→answer + docstring → Task 2. Compose guard (Fable) → Task 3. Frozen fixture + drift validation + #4/#43/#45 re-adjudication → Task 4. Answer↔quote coupling measure-first → Task 5. Layer-2 9/9 + ≥34/38 + synthetic → Task 6. Layer-3 merge gate + model-calibration (integration-marked, pinned) + #0/#54 log → Task 7. Shared-prompt blast radius: the reframe is one shared prompt (spec-documented); no code change needed, and Task 7's live check exercises the KB path. Deferred items (NO_RECALL/M2, COMPOSE_REFUSE, structured ⅓, query-decomposition) are out of scope by design. **Gap check: none.**

**Placeholder scan:** no TBD/TODO; every code step shows code; the one conditional (Task 5 adopt/decline) has both branches fully written.

**Type consistency:** `_faithfulness_gate(question, answer, chunks) -> (bool, str)` used consistently in Tasks 4/6; `decide_after_gate2(label, quote, passages, parsed)` matches faithfulness.py; `answer_uses_quote(answer, quote, min_overlap) -> bool` defined and consumed in Task 5 only; frozen-record schema identical across Tasks 4/5/6.
