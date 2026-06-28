# Context-Budget Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The generation prompt sent to Ollama can never silently overflow `num_ctx`; the H-1B cap-gap class of long-page queries answers correctly instead of degenerating to "The".

**Architecture:** Inside `bot/services/ollama_client.py`, raise `num_ctx` to 16384 and add an assemble-measure-shrink budget guard: build the real prompt, estimate its tokens (tiktoken × safety factor, byte-count fallback), and drop lowest-ranked pages — prefix-truncating only the last surviving page — until the rendered prompt fits a cushioned budget. `compose_from_rows` falls back to deterministic facts rather than rephrase a truncated list.

**Tech Stack:** Python 3.11, pytest, tiktoken (already a project dependency via `bot/services/chunker.py`), Ollama HTTP API (mocked in tests).

## Global Constraints

- Spec of record: `docs/superpowers/specs/2026-06-28-context-budget-guard-design.md` (rev-2, both expert reviews + Codex re-review folded).
- Branch: `feat/context-budget-guard` off `main` @ `69fcfd6`. Worktree: `.claude/worktrees/context-budget-guard`.
- LLM-agnostic: budget derived from `num_ctx`/`num_predict`; tiktoken is a provider-isolated *counting heuristic*, never the generation model's tokenizer. No model-specific magic constant.
- Never-withhold: included/truncated content is verbatim; the answer's source link is preserved in the reply (existing `_source_note_for`); NO bare dropped-URL block in the model prompt.
- Truncated `RetrievedChunk` copies MUST use `copy.copy` (preserves dynamic non-field attrs `item_id`/`source_url`/`verified`), NEVER `dataclasses.replace` (drops them).
- Chunk input order is rank order — NEVER re-sort.
- All HTTP calls mocked in tests (match existing `bot/tests/test_ollama.py` style: `_mock_session_with_response`, `make_chunk`).
- No Claude/AI attribution in commits.
- Run the existing suite green before each commit: `python3 -m pytest bot/tests/test_ollama.py -q`.

---

### Task 1: Constants, token estimator, `num_ctx` 16384 + env override

**Files:**
- Modify: `bot/services/ollama_client.py` (module top imports + constants; `OllamaClient.__init__`)
- Test: `bot/tests/test_ollama.py`

**Interfaces:**
- Produces: module-level `_estimate_tokens(text: str) -> int`; constants `TOKEN_SAFETY_FACTOR=1.2`, `CONTEXT_CUSHION_TOKENS=1024`, `MAX_HISTORY_TURNS=6`, `MIN_DOC_TOKENS=128`, `_DEFAULT_NUM_CTX=16384`, `TRUNCATION_NOTE`; `OllamaClient.num_ctx` now defaults to 16384 (env `OLLAMA_NUM_CTX` override, constructor arg still wins).

- [ ] **Step 1: Write the failing tests**

```python
# add to bot/tests/test_ollama.py
from bot.services import ollama_client as oc


class TestEstimateTokens:
    def test_overcounts_vs_raw_tiktoken(self):
        # estimate must be >= the raw tiktoken count (the safety factor) for adversarial inputs
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        for s in [
            "https://www.njit.edu/global/h1b-cap-gap?year=2026&term=fall",
            "def f(x):\n  return x*x  # minified",
            "学生签证 OPT STEM 延期 申请",
            "AAAA1234-BBBB5678-CCCC9012",
            "the cap gap period bridges F-1 status to H-1B",
        ]:
            assert oc._estimate_tokens(s) >= len(enc.encode(s))

    def test_empty_is_zero(self):
        assert oc._estimate_tokens("") == 0

    def test_fallback_is_pessimistic_when_tiktoken_unavailable(self, monkeypatch):
        # force the fallback path; byte count is always >= true token count
        monkeypatch.setattr(oc, "_TIKTOKEN_ENC", None)
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        for s in ["https://x.njit.edu/a?b=c", "学生签证延期", "plain english text"]:
            assert oc._estimate_tokens(s) >= len(enc.encode(s))
            assert oc._estimate_tokens(s) == len(s.encode("utf-8"))


class TestNumCtxConfig:
    def test_default_is_16384(self):
        assert OllamaClient(base_url="http://x", model="m").num_ctx == 16384

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_NUM_CTX", "12000")
        assert OllamaClient(base_url="http://x", model="m").num_ctx == 12000

    def test_constructor_arg_wins(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_NUM_CTX", "12000")
        assert OllamaClient(base_url="http://x", model="m", num_ctx=9000).num_ctx == 9000
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest bot/tests/test_ollama.py::TestEstimateTokens bot/tests/test_ollama.py::TestNumCtxConfig -q`
Expected: FAIL (`_estimate_tokens` undefined / `num_ctx` default 8192).

- [ ] **Step 3: Implement constants + estimator + num_ctx**

In `bot/services/ollama_client.py` add to the imports block:

```python
import copy
import math
import os
```

After `logger = logging.getLogger(__name__)` add:

```python
try:
    import tiktoken
    _TIKTOKEN_ENC = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover - tiktoken is a hard dep; this is defensive
    _TIKTOKEN_ENC = None

# Generation context-budget guard (see specs/2026-06-28-context-budget-guard-design.md)
_DEFAULT_NUM_CTX = 16384
TOKEN_SAFETY_FACTOR = 1.2      # tiktoken (cl100k) vs llama3.1 divergence cushion
CONTEXT_CUSHION_TOKENS = 1024  # fixed headroom kept below num_ctx
MAX_HISTORY_TURNS = 6          # newest-first cap on conversation turns fed to the prompt
MIN_DOC_TOKENS = 128           # floor below which we'd rather send no doc than a useless sliver
TRUNCATION_NOTE = (
    "\n\n[Document truncated to fit the context budget; later sections are not shown — "
    "open the Source link above for the full page.]"
)


def _estimate_tokens(text: str) -> int:
    """Conservative token estimate for budgeting. tiktoken count x safety factor;
    pessimistic byte-count fallback (bytes >= true BPE token count, never under-counts)."""
    if not text:
        return 0
    if _TIKTOKEN_ENC is not None:
        try:
            return math.ceil(len(_TIKTOKEN_ENC.encode(text)) * TOKEN_SAFETY_FACTOR)
        except Exception:  # pragma: no cover - defensive
            pass
    return len(text.encode("utf-8"))
```

Change `__init__` signature `num_ctx: int = 8192,` → `num_ctx: Optional[int] = None,` and the assignment `self.num_ctx = num_ctx` →

```python
        self.num_ctx = (
            num_ctx if num_ctx is not None
            else int(os.getenv("OLLAMA_NUM_CTX", str(_DEFAULT_NUM_CTX)))
        )
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest bot/tests/test_ollama.py::TestEstimateTokens bot/tests/test_ollama.py::TestNumCtxConfig -q`
Expected: PASS.

- [ ] **Step 5: Verify no caller hardcodes the old window**

Run: `grep -rn "num_ctx" bot/ v2/ --include=*.py | grep -v test`
Expected: only `ollama_client.py` sets it; callers don't pass `num_ctx=8192`. If any does, note it (leave as-is unless it forces 8192).

- [ ] **Step 6: Commit**

```bash
git add bot/services/ollama_client.py bot/tests/test_ollama.py
git commit -m "feat(ollama): token estimator + num_ctx 16384/env override"
```

---

### Task 2: `_fit_chunks` measure-shrink + prefix-truncation helper

**Files:**
- Modify: `bot/services/ollama_client.py` (extract `_ANSWER_INSTRUCTIONS`; add `_assemble_user`, `_truncate_chunk_to_fit`, `_fit_chunks`)
- Test: `bot/tests/test_ollama.py`

**Interfaces:**
- Consumes: `_estimate_tokens`, constants (Task 1); existing `self._build_context_block(list)`.
- Produces:
  - `OllamaClient._assemble_user(self, context_block: str, question: str) -> str`
  - `OllamaClient._truncate_chunk_to_fit(self, chunk, system_prompt: str, question: str, num_predict: int) -> Optional[RetrievedChunk]`
  - `OllamaClient._fit_chunks(self, chunks: list, system_prompt: str, question: str, num_predict: int) -> list` — preserves input order, drops lowest-ranked whole pages until the rendered `system+user` fits `num_ctx - num_predict - CONTEXT_CUSHION_TOKENS`; if one page remains and still overflows, returns `[prefix-truncated copy]`; returns `[]` only in the degenerate case.

- [ ] **Step 1: Write the failing tests**

```python
class TestFitChunks:
    def _client(self, num_ctx):
        return OllamaClient(base_url="http://x", model="m", num_ctx=num_ctx)

    def test_under_budget_is_identity(self):
        c = self._client(16384)
        chunks = [make_chunk("short a"), make_chunk("short b")]
        fitted = c._fit_chunks(chunks, "sys", "q", num_predict=512)
        assert fitted == chunks  # same objects, untouched

    def test_drops_lowest_ranked_until_fit(self):
        c = self._client(2000)  # tiny window forces dropping
        big = "word " * 2000
        chunks = [make_chunk("rank1 " + big), make_chunk("rank2 " + big), make_chunk("rank3 " + big)]
        fitted = c._fit_chunks(chunks, "sys", "q", num_predict=256)
        assert len(fitted) < 3
        assert fitted[0] is chunks[0]  # rank-1 kept, input order preserved
        # rendered prompt is within budget
        user = c._assemble_user(c._build_context_block(fitted), "q")
        assert oc._estimate_tokens("sys") + oc._estimate_tokens(user) + 256 <= 2000

    def test_single_page_overflow_is_prefix_truncated(self):
        c = self._client(1200)
        body = "FIRST SENTENCE is the answer. " + ("filler tail " * 2000)
        chunks = [make_chunk(body)]
        fitted = c._fit_chunks(chunks, "sys", "q", num_predict=128)
        assert len(fitted) == 1
        assert fitted[0] is not chunks[0]               # a copy, not the original
        assert chunks[0].text == body                   # original unmutated
        assert "FIRST SENTENCE is the answer." in fitted[0].text
        assert oc.TRUNCATION_NOTE.strip()[:20] in fitted[0].text
        user = c._assemble_user(c._build_context_block(fitted), "q")
        assert oc._estimate_tokens("sys") + oc._estimate_tokens(user) + 128 <= 1200

    def test_truncated_copy_preserves_provenance(self):
        c = self._client(1200)
        ch = make_chunk("answer. " + ("x " * 4000))
        ch.item_id = 27226           # dynamic non-field attrs (as the runtime shim sets)
        ch.source_url = "https://www.njit.edu/global/h1b"
        ch.verified = True
        fitted = c._fit_chunks([ch], "sys", "q", num_predict=128)
        assert getattr(fitted[0], "item_id") == 27226
        assert getattr(fitted[0], "source_url") == "https://www.njit.edu/global/h1b"
        assert getattr(fitted[0], "verified") is True

    def test_empty_input_returns_empty(self):
        assert self._client(16384)._fit_chunks([], "sys", "q", 512) == []

    def test_degenerate_budget_returns_empty(self):
        c = self._client(300)  # system+framing+num_predict+cushion already blow the window
        fitted = c._fit_chunks([make_chunk("x " * 5000)], "huge " * 200, "q", num_predict=128)
        assert fitted == []

    def test_no_whitespace_hard_cut(self):
        c = self._client(1100)
        chunks = [make_chunk("A" * 40000)]  # no whitespace at all
        fitted = c._fit_chunks(chunks, "sys", "q", num_predict=128)
        assert len(fitted) == 1
        user = c._assemble_user(c._build_context_block(fitted), "q")
        assert oc._estimate_tokens("sys") + oc._estimate_tokens(user) + 128 <= 1100
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest bot/tests/test_ollama.py::TestFitChunks -q`
Expected: FAIL (`_fit_chunks`/`_assemble_user` undefined).

- [ ] **Step 3: Extract the instructions constant and add `_assemble_user`**

In `_build_full_prompt`, the user prompt currently inlines an "Instructions:" string. Move that exact text to a module constant near the other constants:

```python
_ANSWER_INSTRUCTIONS = (
    "Instructions: Answer the student's question using ONLY the documents above. "
    "Cite which document you used. If the documents don't contain the answer, say so "
    "and direct them to a GSA officer. If the question names a specific person or "
    "organization, answer ONLY from documents about that exact person/organization — "
    "if none of the documents are about them, say you couldn't find that information "
    "for them and stop; do not report a different person's details."
)
```

Add the method (place near `_build_context_block`):

```python
    def _assemble_user(self, context_block: str, question: str) -> str:
        return f"{context_block}\n\nStudent question: {question}\n\n{_ANSWER_INSTRUCTIONS}"
```

- [ ] **Step 4: Add `_truncate_chunk_to_fit` and `_fit_chunks`**

```python
    def _truncate_chunk_to_fit(self, chunk, system_prompt, question, num_predict):
        """Return a copy.copy of `chunk` whose body is the largest verbatim prefix (whitespace-snapped,
        hard-cut fallback) such that the full rendered prompt fits the budget, with TRUNCATION_NOTE
        appended. Return None if even MIN_DOC_TOKENS of body won't fit."""
        budget = self.num_ctx - num_predict - CONTEXT_CUSHION_TOKENS
        sys_tokens = _estimate_tokens(system_prompt)

        def rendered_tokens(text: str) -> int:
            tmp = copy.copy(chunk)
            tmp.text = text
            user = self._assemble_user(self._build_context_block([tmp]), question)
            return sys_tokens + _estimate_tokens(user)

        body = chunk.text
        # binary-search the largest prefix length whose rendered prompt fits
        lo, hi, best = 0, len(body), 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if rendered_tokens(body[:mid] + TRUNCATION_NOTE) <= budget:
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
        if best <= 0 or _estimate_tokens(body[:best]) < MIN_DOC_TOKENS:
            return None
        # whitespace-snap to avoid a mid-word cut; hard-cut if no good boundary in the back half
        snap = max(body.rfind(" ", 0, best), body.rfind("\n", 0, best))
        if snap < best // 2:
            snap = best
        truncated = copy.copy(chunk)
        truncated.text = body[:snap].rstrip() + TRUNCATION_NOTE
        return truncated

    def _fit_chunks(self, chunks, system_prompt, question, num_predict):
        """Drop lowest-ranked whole pages (input order = rank order, never re-sorted) until the rendered
        system+user prompt fits num_ctx - num_predict - CUSHION; if one page remains and still overflows,
        prefix-truncate it; return [] only in the degenerate case (caller treats as a generation miss)."""
        if not chunks:
            return []
        budget = self.num_ctx - num_predict - CONTEXT_CUSHION_TOKENS
        sys_tokens = _estimate_tokens(system_prompt)

        def fits(items) -> bool:
            user = self._assemble_user(self._build_context_block(items), question)
            return sys_tokens + _estimate_tokens(user) <= budget

        included = list(chunks)
        while len(included) > 1 and not fits(included):
            included.pop()  # drop the lowest-ranked page
        if fits(included):
            if len(included) < len(chunks):
                logger.info("context budget: kept %d/%d pages", len(included), len(chunks))
            return included
        truncated = self._truncate_chunk_to_fit(included[0], system_prompt, question, num_predict)
        if truncated is None:
            logger.warning("context budget: no doc fits; returning empty fitted context")
            return []
        logger.info("context budget: prefix-truncated rank-1 page to fit")
        return [truncated]
```

- [ ] **Step 5: Run to verify pass**

Run: `python3 -m pytest bot/tests/test_ollama.py::TestFitChunks -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bot/services/ollama_client.py bot/tests/test_ollama.py
git commit -m "feat(ollama): _fit_chunks measure-shrink + prefix-truncation"
```

---

### Task 3: Wire the guard into `generate_answer` (history bound + empty-fitted → None) + binding regression

**Files:**
- Modify: `bot/services/ollama_client.py` (`_build_full_prompt` → split system build; `generate_answer` fit + empty→None)
- Test: `bot/tests/test_ollama.py`

**Interfaces:**
- Consumes: `_fit_chunks`, `_assemble_user`, constants.
- Produces: `OllamaClient._build_system_prompt(self, conversation_history) -> str` (bounded history); `generate_answer` returns `None` when the fitted context is empty.

- [ ] **Step 1: Write the failing tests**

```python
class TestGuardWiring:
    def _client(self, num_ctx=16384):
        return OllamaClient(base_url="http://x", model="m", num_ctx=num_ctx)

    @pytest.mark.asyncio
    async def test_assembled_prompt_within_budget_on_huge_bundle(self):
        c = self._client(16384)
        c._session = _mock_session_with_response(200, {"response": "Cap gap is the period ..."})
        long = "The H-1B cap-gap period bridges F-1 OPT to H-1B. " + ("policy detail " * 1500)
        chunks = [make_chunk(long) for _ in range(5)]
        await c.generate_answer("what is the H-1B cap gap period for F-1 students", chunks)
        payload = c._session.post.call_args[1]["json"]
        total = oc._estimate_tokens(payload["system"]) + oc._estimate_tokens(payload["prompt"]) + 512
        assert total <= 16384
        assert payload["options"]["num_ctx"] == 16384

    @pytest.mark.asyncio
    async def test_cap_gap_first_sentence_survives(self):
        c = self._client(16384)
        c._session = _mock_session_with_response(200, {"response": "ok"})
        chunks = [make_chunk("The cap-gap period extends F-1 status. " + ("x " * 1000))] + \
                 [make_chunk("y " * 2000) for _ in range(4)]
        await c.generate_answer("cap gap period", chunks)
        assert "The cap-gap period extends F-1 status." in c._session.post.call_args[1]["json"]["prompt"]

    @pytest.mark.asyncio
    async def test_history_bounded_to_max_turns(self):
        c = self._client(16384)
        c._session = _mock_session_with_response(200, {"response": "ok"})
        history = [{"role": "user", "content": f"turn{i}"} for i in range(20)]
        await c.generate_answer("q", [make_chunk("a")], conversation_history=history)
        sys = c._session.post.call_args[1]["json"]["system"]
        assert "turn19" in sys and "turn0" not in sys  # only the last MAX_HISTORY_TURNS kept

    @pytest.mark.asyncio
    async def test_empty_fitted_returns_none(self):
        c = self._client(300)  # degenerate window
        c._session = _mock_session_with_response(200, {"response": "should not be used"})
        result = await c.generate_answer("q " * 50, [make_chunk("z " * 5000)],
                                         conversation_history=[{"role": "user", "content": "h " * 300}])
        assert result is None

    @pytest.mark.asyncio
    async def test_no_additional_sources_block(self):
        c = self._client(2000)
        c._session = _mock_session_with_response(200, {"response": "ok"})
        await c.generate_answer("q", [make_chunk("a " * 2000) for _ in range(4)])
        assert "ADDITIONAL SOURCES" not in c._session.post.call_args[1]["json"]["prompt"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest bot/tests/test_ollama.py::TestGuardWiring -q`
Expected: FAIL (history unbounded / no fit applied / empty-fitted not None).

- [ ] **Step 3: Split system-prompt build (bounded history)**

Replace the history-building portion of `_build_full_prompt` with a dedicated method, and have `_build_full_prompt` keep building only the system prompt (the user prompt now comes from `_assemble_user`). Add:

```python
    def _build_system_prompt(self, conversation_history=None) -> str:
        system_prompt = BASE_SYSTEM_PROMPT
        if conversation_history:
            recent = conversation_history[-MAX_HISTORY_TURNS:]
            system_prompt += "\n\n=== CONVERSATION HISTORY ===\n"
            for turn in recent:
                prefix = "Student" if turn["role"] == "user" else "GSA Gateway"
                system_prompt += f"{prefix}: {turn['content'][:400]}\n"
            system_prompt += (
                "=== END OF CONVERSATION HISTORY ===\n"
                "Use the conversation history above ONLY to resolve references in follow-up "
                "questions (like 'step 2', 'that amount', 'the officer you mentioned'). "
                "The documents provided below are the CURRENT, AUTHORITATIVE source: always "
                "answer from them, even if earlier in this conversation you said you could "
                "not find something. Re-check the documents now and use them if they contain "
                "the answer — never repeat a previous 'I couldn't find it' when the answer is "
                "present in the documents below."
            )
        return system_prompt
```

Remove the now-duplicated history/system code and the inline instructions text from `_build_full_prompt` (the instructions live in `_ANSWER_INSTRUCTIONS`, Task 2). `_build_full_prompt` may be deleted; `generate_answer` builds the prompt directly (Step 4).

- [ ] **Step 4: Wire the fit into `generate_answer`**

Replace the body of `generate_answer` up to building `payload` with:

```python
        if not chunks:
            return None
        system_prompt = self._build_system_prompt(conversation_history)
        fitted = self._fit_chunks(chunks, system_prompt, question, num_predict=512)
        if not fitted:
            logger.warning("Ollama generate: no chunk fits context budget; returning None")
            return None
        user_prompt = self._assemble_user(self._build_context_block(fitted), question)
```

(The rest — `payload` with `options.num_ctx=self.num_ctx`, the POST, response handling — is unchanged.)

- [ ] **Step 5: Run the new tests + full file suite**

Run: `python3 -m pytest bot/tests/test_ollama.py -q`
Expected: PASS (new `TestGuardWiring` + all pre-existing tests, including `test_returns_none_when_no_chunks` and `test_conversation_history_in_system_prompt`).

- [ ] **Step 6: Commit**

```bash
git add bot/services/ollama_client.py bot/tests/test_ollama.py
git commit -m "feat(ollama): wire budget guard into generate_answer (bounded history, empty->None)"
```

---

### Task 4: `compose_from_rows` over-budget → deterministic fallback (None)

**Files:**
- Modify: `bot/services/ollama_client.py` (`compose_from_rows`)
- Test: `bot/tests/test_ollama.py`

**Interfaces:**
- Consumes: `_estimate_tokens`, `CONTEXT_CUSHION_TOKENS`, `self.num_ctx`.
- Produces: `compose_from_rows` returns `None` when the assembled prompt would exceed `num_ctx - CUSHION` (caller already falls back to deterministic facts text). Facts are NEVER truncated then rephrased.

- [ ] **Step 1: Write the failing tests**

```python
class TestComposeBudget:
    def _client(self, num_ctx):
        return OllamaClient(base_url="http://x", model="m", num_ctx=num_ctx)

    @pytest.mark.asyncio
    async def test_over_budget_facts_return_none_without_calling_ollama(self):
        c = self._client(2000)
        c._session = _mock_session_with_response(200, {"response": "should not be used"})
        result = await c.compose_from_rows("list everyone", "name, " * 5000)
        assert result is None
        c._session.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_under_budget_facts_compose_normally(self):
        c = self._client(16384)
        c._session = _mock_session_with_response(200, {"response": "Here are the officers: ..."})
        result = await c.compose_from_rows("who are the officers", "President: A\nVP: B")
        assert result == "Here are the officers: ..."
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest bot/tests/test_ollama.py::TestComposeBudget -q`
Expected: FAIL (over-budget facts still call Ollama).

- [ ] **Step 3: Add the budget check in `compose_from_rows`**

After `user_prompt = (...)` is built and before `payload = {...}`:

```python
        if (_estimate_tokens(system_prompt) + _estimate_tokens(user_prompt) + 900
                > self.num_ctx - CONTEXT_CUSHION_TOKENS):
            logger.warning("compose_from_rows: facts exceed context budget; "
                           "falling back to deterministic facts")
            return None
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest bot/tests/test_ollama.py::TestComposeBudget -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/services/ollama_client.py bot/tests/test_ollama.py
git commit -m "feat(ollama): compose_from_rows falls back to deterministic facts when over budget"
```

---

### Task 5: Add the cap-gap query to the eval set

**Files:**
- Modify: `eval/questions.txt`

**Interfaces:** none (data file; one question per line, `# category` headers).

- [ ] **Step 1: Inspect the file format**

Run: `grep -n "immigration\|OGI\|global\|F-1\|visa\|# " eval/questions.txt | head`
Expected: see the `#`-prefixed category headers and existing question lines.

- [ ] **Step 2: Add the cap-gap question under the most fitting category**

Append the line (under an immigration/OGI/global category header if one exists, else a sensible existing category):

```
what is the H-1B cap gap period for F-1 students
```

- [ ] **Step 3: Verify it's present exactly once**

Run: `grep -c "H-1B cap gap period for F-1" eval/questions.txt`
Expected: `1`

- [ ] **Step 4: Commit**

```bash
git add eval/questions.txt
git commit -m "test(eval): add H-1B cap-gap query (num_ctx-overflow regression)"
```

---

### Task 6: Manual live verification (required evidence — not CI)

**Files:** none (verification only). Do this with Ollama running, before requesting the merge.

- [ ] **Step 1: Full suite green**

Run: `python3 -m pytest bot/tests/test_ollama.py -q && python3 -m pytest bot/tests -q`
Expected: PASS (no regressions across the bot test suite).

- [ ] **Step 2: Live cap-gap check on a DB copy (no prod write)**

Run (Ollama up):
```bash
cp gsa_gateway.db /tmp/cbg_verify.db
DATABASE_PATH=/tmp/cbg_verify.db V2_RETRIEVER_ENABLED=true LIVE_ENABLED=0 OLLAMA_ENABLED=true \
  bash scripts/ask.sh "what is the H-1B cap gap period for F-1 students" --answer
```
Expected: a real cap-gap answer (a sentence about the cap-gap period bridging F-1 to H-1B), **never just "The"**. Capture the output as evidence.

- [ ] **Step 3: Spot-check no short-item regression**

Run a couple of normal queries the same way (e.g. `"who are the GSA officers"`, `"what is the travel award"`) and confirm answers are unchanged/sane (the guard is a no-op when the bundle fits).

- [ ] **Step 4: Report evidence to the owner**

Paste the cap-gap output + the suite result. This is the owner sign-off gate before merge to main + restart (spec §13).

---

## Self-Review

**Spec coverage:** G1 (Task 2/3 measure-shrink), G2 (Task 3 binding regression + Task 6 live), G3 (Task 1 num_ctx), G4 (Task 1 estimator), G5 (Task 2 prefix+marker; links via existing reply note — unchanged), G6 (Task 3 generate_answer + Task 4 compose), G7/G8/G9/G10 deferred (no task — correct). Reject criteria: prompt-within-budget (Task 3 test), no ADDITIONAL SOURCES (Task 3 test), compose not truncated (Task 4), no mutation/provenance loss (Task 2 tests), no re-sort (Task 2 `test_drops_lowest_ranked_until_fit` asserts `fitted[0] is chunks[0]`).

**Placeholder scan:** none — every code/step is concrete.

**Type consistency:** `_estimate_tokens`/`_assemble_user`/`_fit_chunks`/`_truncate_chunk_to_fit`/`_build_system_prompt` names and signatures match across Tasks 1–4. `copy.copy` used for truncated chunks (preserves dynamic attrs) per Global Constraints; `_DEFAULT_NUM_CTX=16384`, `CONTEXT_CUSHION_TOKENS=1024`, `num_predict` 512 (generate) / 900 (compose) consistent.
