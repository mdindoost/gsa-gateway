# Query-Correction Salvage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a query misses retrieval, deterministically expand acronyms (always) and, only on a confirmed miss, run a constrained LLM rewrite guarded against name/clause corruption, then re-route + re-retrieve before the live web fallback — so a misspelled/abbreviated structured query (`heir of cs dep`) reaches the KG instead of the web.

**Architecture:** Two pieces at two altitudes. (1) A deterministic curated acronym dictionary runs at the top of `handle()` and **augments** the query (keeps the bare acronym + appends the expansion), feeding the router and retriever. (2) On a confirmed miss (router `None` AND `primary_miss`), AFTER the office/deep tiers did not adopt, one constrained LLM rewrite runs, is reverted by a KG **name-guard** and a **structure-guard** if it corrupts a name or drops a clause, then drives a KG re-route and a RAG re-retrieve before live. The original query is preserved for display/log/compose.

**Tech Stack:** Python 3.11, sqlite3, the existing `OllamaClient` (`llama3.1:8b` via `/api/generate`), pytest.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-06-29-query-correction-salvage-design.md` (rev 3) — this plan implements it; read it first.
- **Flag:** all new behavior gated by `QUERY_CORRECT_ENABLED` (default OFF; kill = `0` + restart).
- **Hard line — no LLM in the router:** the deterministic router is unchanged; correction only rewrites the query *string* it is given.
- **Hard line — never-withhold / verbatim:** correction changes only the retrieval query; answers stay verbatim from KG/RAG/live; a failed/garbled correction degrades to live exactly as today.
- **Hard line — original preserved:** `clean_text` stays the original for display/log/history/compose; only `retrieval_q` (= `q1`, or `q2` on a rescue) drives routing/retrieval/gate.
- **O1 = AUGMENT, never expand-in-place** (keep the bare high-IDF acronym).
- **D-ORD = (b):** the LLM rewrite fires AFTER office/deep did not adopt.
- **Buttons (owner reversed 2026-06-29):** the KG-rescue answer logs a `question_id` and therefore shows feedback buttons like every answer.
- **LLM-agnostic:** the rewrite uses the existing `OllamaClient`; no model-specific constant baked in.
- **Evidence-before-claim:** every task ends with passing tests; final task requires a shown live smoke (`ask.sh`) + `eval.sh` no-regression.

---

### Task 1: Config flag `QUERY_CORRECT_ENABLED`

**Files:**
- Modify: `bot/config.py` (the env-parsing block + the config dataclass/object)

**Interfaces:**
- Produces: `botcfg.QUERY_CORRECT_ENABLED: bool` (read as `import bot.config as botcfg`, matching `botcfg.ANSWER_GATE_ENABLED` / `botcfg.LIVE_ENABLED` usage in `message_handler.py`).

- [ ] **Step 1: Find the existing flag pattern.** Run: `grep -n "ANSWER_GATE_ENABLED\|LIVE_ENABLED" bot/config.py`. Note the exact module-level pattern (these are read as `botcfg.X` in `message_handler.py:220,749`).

- [ ] **Step 2: Write the failing test**

```python
# v2/tests/test_query_correct_config.py
import importlib, os
def test_query_correct_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("QUERY_CORRECT_ENABLED", raising=False)
    import bot.config as botcfg; importlib.reload(botcfg)
    assert botcfg.QUERY_CORRECT_ENABLED is False
def test_query_correct_flag_on(monkeypatch):
    monkeypatch.setenv("QUERY_CORRECT_ENABLED", "1")
    import bot.config as botcfg; importlib.reload(botcfg)
    assert botcfg.QUERY_CORRECT_ENABLED is True
```

- [ ] **Step 3: Run test to verify it fails.** Run: `python -m pytest v2/tests/test_query_correct_config.py -v` — Expected: FAIL (`AttributeError: QUERY_CORRECT_ENABLED`).

- [ ] **Step 4: Add the flag** next to `ANSWER_GATE_ENABLED`, mirroring its exact form. If the pattern is module-level:

```python
QUERY_CORRECT_ENABLED = os.getenv("QUERY_CORRECT_ENABLED", "0") == "1"
```

(If `bot/config.py` uses a different truthiness helper for the neighboring flags, use that helper verbatim instead.)

- [ ] **Step 5: Run test to verify it passes.** Run: `python -m pytest v2/tests/test_query_correct_config.py -v` — Expected: PASS.

- [ ] **Step 6: Commit.** `git add bot/config.py v2/tests/test_query_correct_config.py && git commit -m "feat(query-correct): add QUERY_CORRECT_ENABLED flag (default off)"`

---

### Task 2: Acronym dictionary — `augment_acronyms`

**Files:**
- Create: `v2/core/retrieval/query_correct.py`
- Test: `v2/tests/test_query_correct_acronyms.py`

**Interfaces:**
- Produces: `augment_acronyms(text: str, protected: set[str] | None = None) -> str` — whole-word, case-insensitive; for each matched abbreviation appends its expansion AFTER the token (augment, not replace); never expands a token present in `protected` (real corpus terms / names). Returns the text unchanged when nothing matches. `ACRONYMS: dict[str, str]` module constant.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_query_correct_acronyms.py
from v2.core.retrieval.query_correct import augment_acronyms

def test_augment_keeps_bare_acronym_and_appends_expansion():
    assert augment_acronyms("what is gsa") == "what is gsa graduate student association"

def test_augment_multiple_tokens():
    assert augment_acronyms("heir of cs dep") == "heir of cs computer science dep department"

def test_augment_noop_when_no_abbrev():
    assert augment_acronyms("who is the dean of engineering") == "who is the dean of engineering"

def test_augment_is_case_insensitive_but_preserves_text():
    # bare token preserved verbatim; expansion appended lowercase
    assert augment_acronyms("What is GSA").lower() == "what is gsa graduate student association"

def test_augment_skips_protected_token():
    # a real surname that collides with an abbrev must NOT be expanded
    assert augment_acronyms("prof wang", protected={"prof"}) == "prof wang"
```

- [ ] **Step 2: Run test to verify it fails.** Run: `python -m pytest v2/tests/test_query_correct_acronyms.py -v` — Expected: FAIL (module/function missing).

- [ ] **Step 3: Implement**

```python
# v2/core/retrieval/query_correct.py
"""On-miss query correction: acronym augmentation + LLM-rewrite guards.

Deterministic pieces (augment_acronyms, name_guard, structure_guard) are pure and
have no I/O at call time. See specs/2026-06-29-query-correction-salvage-design.md."""
from __future__ import annotations
import re

# Curated, reviewed. Owns ALL acronym/abbreviation handling (the LLM is forbidden it).
ACRONYMS: dict[str, str] = {
    "gsa": "graduate student association",
    "dep": "department",
    "dept": "department",
    "prof": "professor",
    "cs": "computer science",
    "eng": "engineering",
    "uni": "university",
}

_WORD_RX = re.compile(r"[A-Za-z]+")


def augment_acronyms(text: str, protected: set[str] | None = None) -> str:
    """AUGMENT (keep the bare token + append its expansion). Never expand-in-place
    (would drop a high-IDF acronym from the bm25 leg). Never expand a protected token."""
    protected = protected or set()
    out: list[str] = []
    for tok in text.split():
        out.append(tok)
        core = "".join(_WORD_RX.findall(tok)).lower()
        if core and core in ACRONYMS and core not in protected:
            out.append(ACRONYMS[core])
    return " ".join(out)
```

- [ ] **Step 4: Run test to verify it passes.** Run: `python -m pytest v2/tests/test_query_correct_acronyms.py -v` — Expected: PASS.

- [ ] **Step 5: Commit.** `git add v2/core/retrieval/query_correct.py v2/tests/test_query_correct_acronyms.py && git commit -m "feat(query-correct): acronym AUGMENT dictionary"`

---

### Task 3: Name-token loader + `name_guard`

**Files:**
- Modify: `v2/core/retrieval/query_correct.py`
- Test: `v2/tests/test_query_correct_name_guard.py`

**Interfaces:**
- Consumes: `augment_acronyms` (Task 2 module).
- Produces:
  - `load_name_tokens(conn) -> set[str]` — distinct lowercased person-name tokens (len>2) from `nodes` where `type='Person' AND is_active=1`.
  - `name_guard(original: str, rewritten: str, name_tokens: set[str]) -> str` — returns `rewritten` unless a real-name token from `original` is absent from `rewritten`, in which case returns `original` (revert).
  - `_tokens(s: str) -> set[str]` — lowercased word tokens.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_query_correct_name_guard.py
import sqlite3, pytest
from v2.core.retrieval.query_correct import load_name_tokens, name_guard, _tokens

@pytest.fixture
def names():
    return {"koutis", "ioannis", "durvish", "paliwal", "wang", "guiling"}

def test_name_guard_reverts_dropped_name(names):
    # 'durvish' (a real name) changed to 'dhurjati' -> revert to original
    assert name_guard("durvish koutis", "Dhurjati Koutis", names) == "durvish koutis"

def test_name_guard_accepts_heir_to_chair(names):
    # no real-name token touched -> accept
    out = name_guard("heir of cs dep", "chair of computer science department", names)
    assert out == "chair of computer science department"

def test_name_guard_accepts_name_preserving_rewrite(names):
    assert name_guard("durvish paliwal contact", "contact durvish paliwal", names) \
        == "contact durvish paliwal"

def test_load_name_tokens_filters_short_and_inactive():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE nodes(type TEXT, name TEXT, is_active INT);"
        "INSERT INTO nodes VALUES('Person','Ioannis Koutis',1),"
        "('Person','Al Bo',0),('Org','CS Dept',1);")
    toks = load_name_tokens(conn)
    assert "koutis" in toks and "ioannis" in toks
    assert "bo" not in toks and "al" not in toks  # len<=2 / inactive row excluded
```

- [ ] **Step 2: Run test to verify it fails.** Run: `python -m pytest v2/tests/test_query_correct_name_guard.py -v` — Expected: FAIL (functions missing).

- [ ] **Step 3: Implement (append to `query_correct.py`)**

```python
def _tokens(s: str) -> set[str]:
    return set(t.lower() for t in _WORD_RX.findall(s))


def load_name_tokens(conn) -> set[str]:
    toks: set[str] = set()
    for (nm,) in conn.execute(
            "SELECT name FROM nodes WHERE type='Person' AND is_active=1"):
        for t in _WORD_RX.findall((nm or "").lower()):
            if len(t) > 2:
                toks.add(t)
    return toks


def name_guard(original: str, rewritten: str, name_tokens: set[str]) -> str:
    """Revert to original if the rewrite changed/dropped a real person-name token."""
    protected = _tokens(original) & name_tokens
    survived = _tokens(rewritten)
    if any(t not in survived for t in protected):
        return original
    return rewritten
```

- [ ] **Step 4: Run test to verify it passes.** Run: `python -m pytest v2/tests/test_query_correct_name_guard.py -v` — Expected: PASS.

- [ ] **Step 5: Commit.** `git add v2/core/retrieval/query_correct.py v2/tests/test_query_correct_name_guard.py && git commit -m "feat(query-correct): KG name-guard + name-token loader"`

---

### Task 4: `structure_guard` (clause-drop + hallucinated-name revert)

**Files:**
- Modify: `v2/core/retrieval/query_correct.py`
- Test: `v2/tests/test_query_correct_structure_guard.py`

**Interfaces:**
- Consumes: `_tokens` (Task 3).
- Produces:
  - `structure_guard(original: str, rewritten: str, name_tokens: set[str]) -> str` — reverts to `original` if (1) a non-stopword content token of `original` is absent from `rewritten` AND no `rewritten` token is within edit-distance ≤2 of it (a silent clause drop), OR (2) `rewritten` introduces a `name_tokens` token not in `original` (hallucinated name).
  - `_within_edit(token: str, candidates: set[str], max_dist: int = 2) -> bool`
  - `_edit_distance(a: str, b: str) -> int` (Damerau–Levenshtein, capped).
  - `_STOPWORDS: set[str]`.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_query_correct_structure_guard.py
from v2.core.retrieval.query_correct import structure_guard

NAMES = {"koutis", "koutsoupias"}

def test_reverts_on_dropped_clause():
    orig = "who can impeach a gsa officer and what vote is needed"
    rw = "what are the impeachment procedures for a gsa officer"  # 'vote','needed' dropped, no near-edit
    assert structure_guard(orig, rw, NAMES) == orig

def test_accepts_typo_fix_deletion():
    # 'profesor' deleted but 'professor' present within edit-distance 1 -> accept
    assert structure_guard("profesor koutis", "professor koutis", NAMES) \
        == "professor koutis"

def test_accepts_pure_augmentation():
    # additions only (heir->chair adds tokens, drops nothing meaningful) -> accept
    out = structure_guard("chair of cs", "chair of computer science", NAMES)
    assert out == "chair of computer science"

def test_reverts_on_hallucinated_name():
    # 'koutis' -> 'koutsoupias' introduces a name token not in original
    assert structure_guard("what is koutis citation", "koutsoupias citation", NAMES) \
        == "what is koutis citation"
```

- [ ] **Step 2: Run test to verify it fails.** Run: `python -m pytest v2/tests/test_query_correct_structure_guard.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement (append to `query_correct.py`)**

```python
_STOPWORDS = {
    "a", "an", "the", "of", "in", "at", "for", "to", "is", "are", "and", "or",
    "do", "does", "what", "who", "which", "how", "can", "i", "me", "my", "on",
    "with", "by", "be", "as", "that", "this", "it", "you", "your",
}


def _edit_distance(a: str, b: str) -> int:
    """Damerau–Levenshtein (transpositions counted). Small strings; cap at len."""
    la, lb = len(a), len(b)
    if abs(la - lb) > 2:
        return 3  # already over the ≤2 ceiling we care about
    prev2: list[int] = []
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if (i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]):
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[lb]


def _within_edit(token: str, candidates: set[str], max_dist: int = 2) -> bool:
    return any(_edit_distance(token, c) <= max_dist for c in candidates)


def structure_guard(original: str, rewritten: str, name_tokens: set[str]) -> str:
    """Revert a rewrite that silently dropped a content token (a clause) or
    introduced a person-name token that was not in the original."""
    o, r = _tokens(original), _tokens(rewritten)
    # (1) silent content-token deletion (allow if a near-edit typo fix is present)
    for tok in o:
        if tok in _STOPWORDS or tok in r:
            continue
        if not _within_edit(tok, r):
            return original
    # (2) hallucinated name introduced
    if (r & name_tokens) - (o & name_tokens):
        return original
    return rewritten
```

- [ ] **Step 4: Run test to verify it passes.** Run: `python -m pytest v2/tests/test_query_correct_structure_guard.py -v` — Expected: PASS.

- [ ] **Step 5: Commit.** `git add v2/core/retrieval/query_correct.py v2/tests/test_query_correct_structure_guard.py && git commit -m "feat(query-correct): structure-guard (clause-drop + hallucinated-name revert)"`

---

### Task 5: `llm_rewrite` constrained call

**Files:**
- Modify: `v2/core/retrieval/query_correct.py`
- Test: `v2/tests/test_query_correct_llm_rewrite.py`

**Interfaces:**
- Consumes: an Ollama client with `async generate(prompt=..., system=..., options=..., fmt="json") -> str` (the same call shape used at `message_handler.py:777`).
- Produces:
  - `REWRITE_SYSTEM: str` — the constrained prompt.
  - `async llm_rewrite(ollama, q1: str) -> str` — returns the parsed rewrite, or `q1` unchanged on any error/parse-fail/empty.

- [ ] **Step 1: Write the failing test** (stubbed client — model-free)

```python
# v2/tests/test_query_correct_llm_rewrite.py
import asyncio, json, pytest
from v2.core.retrieval.query_correct import llm_rewrite

class FakeOllama:
    def __init__(self, resp): self.resp = resp
    async def generate(self, prompt=None, system=None, options=None, fmt=None):
        return self.resp

def test_llm_rewrite_parses_json():
    o = FakeOllama(json.dumps({"rewritten": "chair of computer science department"}))
    assert asyncio.run(llm_rewrite(o, "heir of cs dep")) \
        == "chair of computer science department"

def test_llm_rewrite_passthrough_on_bad_json():
    o = FakeOllama("not json")
    assert asyncio.run(llm_rewrite(o, "heir of cs dep")) == "heir of cs dep"

def test_llm_rewrite_passthrough_on_empty():
    o = FakeOllama(json.dumps({"rewritten": ""}))
    assert asyncio.run(llm_rewrite(o, "heir of cs dep")) == "heir of cs dep"
```

- [ ] **Step 2: Run test to verify it fails.** Run: `python -m pytest v2/tests/test_query_correct_llm_rewrite.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement (append to `query_correct.py`)**

```python
import json as _json

REWRITE_SYSTEM = (
    "You normalize a student's SEARCH QUERY for NJIT so it can be matched against a "
    "database of people, departments, and policies. Fix spelling mistakes, and if a word "
    "clearly does not make sense in context, replace it with the word the student most "
    "likely meant (e.g. 'heir of cs' -> 'chair of computer science'; a person who leads a "
    "department is a 'chair', 'dean', or 'director'). "
    "STRICT RULES: Acronyms are ALREADY expanded — do not touch them. Never change, "
    "translate, drop, or invent a PERSON'S NAME. Never change a research TOPIC into a job "
    "title. Preserve EVERY part of the question — do not drop clauses. Do NOT answer the "
    'question. Output ONLY compact JSON: {"rewritten": "<query>"}'
)


async def llm_rewrite(ollama, q1: str) -> str:
    """One constrained rewrite call. Returns q1 unchanged on any failure (never breaks
    the path). The deterministic guards (name_guard, structure_guard) enforce fidelity —
    the prompt alone is not trusted (the 8B violates it; see spec §3)."""
    try:
        raw = await ollama.generate(
            prompt=q1, system=REWRITE_SYSTEM,
            options={"temperature": 0.0, "num_predict": 96}, fmt="json") or ""
        val = _json.loads(raw).get("rewritten", "")
        return val.strip() or q1
    except Exception:  # noqa: BLE001 - never break the message path
        return q1
```

- [ ] **Step 4: Run test to verify it passes.** Run: `python -m pytest v2/tests/test_query_correct_llm_rewrite.py -v` — Expected: PASS.

- [ ] **Step 5: Commit.** `git add v2/core/retrieval/query_correct.py v2/tests/test_query_correct_llm_rewrite.py && git commit -m "feat(query-correct): constrained llm_rewrite (passthrough on failure)"`

---

### Task 6: Wire `augment_acronyms` at the top of `handle()`

**Files:**
- Modify: `bot/core/message_handler.py` (top of `handle()`, after the context-rewrite block ~`:203`)
- Test: `v2/tests/test_query_correct_handle_wiring.py`

**Interfaces:**
- Consumes: `augment_acronyms` (Task 2), `botcfg.QUERY_CORRECT_ENABLED` (Task 1).
- Produces: when the flag is ON, `resolved_query` is replaced by its augmented form BEFORE Gate-1, `UnifiedRouter.decide`, `_try_structured`, and `_rag_pipeline`. `clean_text` is untouched. When the flag is OFF, behavior is byte-identical to today.

- [ ] **Step 1: Read the insertion point.** Run: `sed -n '196,265p' bot/core/message_handler.py`. Confirm `resolved_query` is assigned ~`:199-203` and first consumed by Gate-1 `_try_structured(resolved_query)` ~`:222`.

- [ ] **Step 2: Write the failing test** (assert the augmented string reaches routing)

```python
# v2/tests/test_query_correct_handle_wiring.py
import asyncio, importlib, types, pytest
import bot.config as botcfg
from bot.core.message_handler import MessageHandler, MessageRequest

class _CM:
    def get_mode(self, _): return "gsa"
    def get_history(self, *a, **k): return []
    def add_turn(self, *a, **k): pass
    def get_session(self, _): return None

def _handler(monkeypatch, capture):
    h = MessageHandler(retriever=None, ollama=None, conversation_manager=_CM(),
                       intent_detector=None, db=None, rate_limiter=None, kb=None,
                       config=types.SimpleNamespace(conversation_max_turns=5))
    async def fake_struct(q):  # capture what routing receives
        capture.append(q); return "STRUCTURED-OK"
    monkeypatch.setattr(h, "_try_structured", fake_struct)
    return h

def test_acronyms_augment_reaches_routing_when_on(monkeypatch):
    monkeypatch.setattr(botcfg, "QUERY_CORRECT_ENABLED", True)
    cap = []
    h = _handler(monkeypatch, cap)
    asyncio.run(h.handle(MessageRequest(user_id="u", text="heir of cs dep", platform="discord")))
    assert any("computer science" in c and "department" in c for c in cap)

def test_clean_path_unchanged_when_off(monkeypatch):
    monkeypatch.setattr(botcfg, "QUERY_CORRECT_ENABLED", False)
    cap = []
    h = _handler(monkeypatch, cap)
    asyncio.run(h.handle(MessageRequest(user_id="u", text="heir of cs dep", platform="discord")))
    assert cap == ["heir of cs dep"]  # original verbatim
```

(Note: if `handle()`'s early structure makes a no-retriever handler hard to drive, adapt the fixture to the existing `message_handler` test harness in `v2/tests/` — find it with `grep -rl "MessageHandler(" v2/tests`.)

- [ ] **Step 3: Run test to verify it fails.** Run: `python -m pytest v2/tests/test_query_correct_handle_wiring.py -v` — Expected: FAIL (augment not wired).

- [ ] **Step 4: Implement.** After the context-rewrite block (right before the explicit-live-search check ~`:209`), insert:

```python
        # Acronym/abbreviation augmentation (deterministic; owns acronym handling, never
        # the LLM). AUGMENT — keep the bare acronym, append the expansion — so the bm25 leg
        # keeps the high-IDF token. Feeds Gate-1, the router, the structured path, and RAG.
        if botcfg.QUERY_CORRECT_ENABLED:
            from v2.core.retrieval.query_correct import augment_acronyms
            resolved_query = augment_acronyms(resolved_query)
```

- [ ] **Step 5: Run tests to verify they pass.** Run: `python -m pytest v2/tests/test_query_correct_handle_wiring.py -v` — Expected: PASS. Then `python -m pytest v2/tests/ -k message_handler -q` — Expected: no regression.

- [ ] **Step 6: Commit.** `git add bot/core/message_handler.py v2/tests/test_query_correct_handle_wiring.py && git commit -m "feat(query-correct): augment acronyms at top of handle()"`

---

### Task 7: On-miss correction block in `_rag_pipeline` (KG + RAG rescue, after office/deep)

**Files:**
- Modify: `bot/core/message_handler.py` (`_rag_pipeline`, between the deep-fallback block end ~`:748` and the live block ~`:749`)
- Test: `v2/tests/test_query_correct_rescue.py`

**Interfaces:**
- Consumes: `augment_acronyms`/`llm_rewrite`/`name_guard`/`structure_guard`/`load_name_tokens` (Tasks 2–5), `self._try_structured`, `self.retriever.retrieve`, `self.retriever.top_relevance`, `self.db.log_question`.
- Produces: on a confirmed miss (after office/deep did not adopt), computes `q2`; a KG rescue returns a full `MessageResponse` with `question_id` (→ buttons); a RAG rescue sets `chunks=rescue`, `primary_miss=False`, and a local `retrieval_q=q2` consumed by Task 8. Adds `MessageResponse.is_corrected: bool = False`.
- Name tokens loaded ONCE (module-level lazy cache keyed by db_path), never per-call.

- [ ] **Step 1: Read the insertion window.** Run: `sed -n '706,760p' bot/core/message_handler.py`. Confirm `primary_miss`, `used_office`, `used_deep`, `base_q`, and the live block at `:749`.

- [ ] **Step 2: Add `is_corrected` to `MessageResponse`.** In the dataclass (~`:152`), add: `is_corrected: bool = False`.

- [ ] **Step 3: Write the failing test** (KG rescue + revert behaviors; stubbed retriever/db)

```python
# v2/tests/test_query_correct_rescue.py
import asyncio, types, pytest
import bot.config as botcfg
from bot.core import message_handler as MH

def test_corrected_query_kg_rescue(monkeypatch):
    monkeypatch.setattr(botcfg, "QUERY_CORRECT_ENABLED", True)
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", False)
    monkeypatch.setattr(MH.query_correct, "load_name_tokens", lambda conn: set())
    monkeypatch.setattr(MH.query_correct, "augment_acronyms", lambda q, protected=None: q)
    async def fake_rewrite(ollama, q1): return "chair of computer science department"
    monkeypatch.setattr(MH.query_correct, "llm_rewrite", fake_rewrite)
    monkeypatch.setattr(MH.query_correct, "name_guard", lambda o, r, n: r)
    monkeypatch.setattr(MH.query_correct, "structure_guard", lambda o, r, n: r)
    # handler with a retriever that always misses + a structured that answers the corrected q
    h = _miss_handler(monkeypatch)
    calls = {}
    async def fake_struct(q): calls["q"] = q; return "Dr. X chairs CS." if "chair" in q else None
    monkeypatch.setattr(h, "_try_structured", fake_struct)
    resp = asyncio.run(h._rag_pipeline(_req(), "heir of cs dep", MH.INTENT_QUESTION,
                                       resolved_query="heir of cs dep"))
    assert "chairs CS" in resp.text
    assert calls["q"] == "chair of computer science department"
    assert resp.is_corrected is True
```

(Implement `_miss_handler`/`_req` helpers in the test mirroring the existing `_rag_pipeline` tests — `grep -n "_rag_pipeline" v2/tests/*.py` for the established stub pattern, e.g. a retriever whose `retrieve` returns `[]` and `top_relevance` returns `0.0`.)

- [ ] **Step 4: Run test to verify it fails.** Run: `python -m pytest v2/tests/test_query_correct_rescue.py -v` — Expected: FAIL.

- [ ] **Step 5: Implement.** At the top of `message_handler.py`, add `from v2.core.retrieval import query_correct` and the name-token cache:

```python
_NAME_TOKENS_CACHE: dict[str, set] = {}

def _name_tokens_for(db_path: str) -> set:
    toks = _NAME_TOKENS_CACHE.get(db_path)
    if toks is None:
        import sqlite3
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            toks = query_correct.load_name_tokens(conn)
        finally:
            conn.close()
        _NAME_TOKENS_CACHE[db_path] = toks
    return toks
```

Then insert the rescue block AFTER the deep-fallback block (~`:748`), BEFORE the live block (`:749`). Declare `retrieval_q = base_q` once just before the gate region so Task 8 can read it:

```python
            retrieval_q = base_q          # what the gate/live should score against
            # ── On-miss query correction (LLM rewrite + guards) ───────────────────────
            # D-ORD=(b): runs only after office/deep did NOT adopt — the last resort
            # before live. The original (clean_text/base_q) is preserved for display/log.
            if (botcfg.QUERY_CORRECT_ENABLED and primary_miss and not used_office
                    and self.ollama and self.retriever):
                db_path = getattr(self.db, "db_path", None) if self.db else None
                name_tokens = _name_tokens_for(db_path) if db_path else set()
                q2 = await query_correct.llm_rewrite(self.ollama, base_q)
                q2 = query_correct.name_guard(base_q, q2, name_tokens)
                q2 = query_correct.structure_guard(base_q, q2, name_tokens)
                if q2 != base_q:
                    # 1) KG re-route on the corrected query (highest precision)
                    kg = await self._try_structured(q2)
                    if kg is not None:
                        qid = None
                        if self.db:
                            qid = self.db.log_question(
                                user_id=user_id, question=clean_text,
                                matched_topic="query-corrected (KG)", confidence=100.0,
                                guild_id=req.guild_id, platform=req.platform)
                        return MessageResponse(text=kg, used_ai=True, question_id=qid,
                                               is_corrected=True)
                    # 2) RAG re-retrieve on the corrected query
                    rescue = await self.retriever.retrieve(
                        query=q2, conversation_history=history)
                    rescue_rel = self.retriever.top_relevance(q2, rescue) if rescue else None
                    if rescue and rescue_rel is not None and rescue_rel >= botcfg.LIVE_THRESHOLD:
                        chunks = rescue
                        primary_miss = False
                        retrieval_q = q2          # gate/live/compose use the corrected q
```

- [ ] **Step 6: Run tests.** Run: `python -m pytest v2/tests/test_query_correct_rescue.py -v` — Expected: PASS. Then `python -m pytest v2/tests/ -k "message_handler or rag" -q` — Expected: no regression.

- [ ] **Step 7: Commit.** `git add bot/core/message_handler.py v2/tests/test_query_correct_rescue.py && git commit -m "feat(query-correct): on-miss KG+RAG rescue after office/deep (D-ORD=b)"`

---

### Task 8: Gate-2 + live-escape + compose use `retrieval_q`

**Files:**
- Modify: `bot/core/message_handler.py` (`:776`, `:777`, `:781`, `:799`, and the compose block `:822-824`)
- Test: `v2/tests/test_query_correct_gate.py`

**Interfaces:**
- Consumes: the local `retrieval_q` from Task 7.
- Produces: the answer-gate CE, `is_fact_shaped`, the Gate-2 prompt, AND the Gate-2→live escape all score against `retrieval_q` (the query the chunks were fetched for), not the original typo; compose receives the `q2` hint when a rescue rewrote the query.

- [ ] **Step 1: Read the gate region.** Run: `sed -n '760,835p' bot/core/message_handler.py`. Confirm the four `base_q` sites (`:776` CE, `:777` `is_fact_shaped`, `:781` `gate2_prompt`, `:799` `live_search`) and the compose block (`:822-824`).

- [ ] **Step 2: Write the failing test** (a rescued query must be gate-scored with q2, not the typo)

```python
# v2/tests/test_query_correct_gate.py
# Drives _rag_pipeline with ANSWER_GATE_ENABLED on, a retriever whose top_relevance
# returns HIGH for q2 and LOW for the typo; asserts the answer is NOT deflected.
# (Mirror the Task 7 stub harness; assert resp.text != _KB_MISS_RESPONSE and is_corrected.)
```

(Write the concrete body using the Task 7 `_miss_handler` harness: set `botcfg.ANSWER_GATE_ENABLED=True`; retriever `top_relevance` returns `0.0` for the typo and `0.9` for `q2`; assert the rescued answer is returned, proving the gate used `retrieval_q`.)

- [ ] **Step 3: Run test to verify it fails.** Run: `python -m pytest v2/tests/test_query_correct_gate.py -v` — Expected: FAIL (gate deflects because it scored the typo).

- [ ] **Step 4: Implement the 4-site swap.** In the Gate-2 block replace `base_q` → `retrieval_q` at:
  - `:776` `_g2_ce = self.retriever.top_relevance(retrieval_q, chunks)`
  - `:777` `_g2_fact = is_fact_shaped(retrieval_q)`
  - `:781` `_g2_sys, _g2_usr = gate2_prompt(retrieval_q, _g2_ctx)`
  - `:799` `live = await self.live_search(retrieval_q)`

  And in the compose block (`:822-824`), extend the existing resolved-query hint so a corrected query is threaded into compose:

```python
                compose_question = clean_text
                if retrieval_q and retrieval_q != clean_text:
                    compose_question = f"{clean_text}\n(resolved for retrieval: {retrieval_q})"
```

  (This generalizes the existing `resolved_query` RA3 hint to also cover `q2`. Confirm `retrieval_q` defaults to `base_q` so the non-corrected path keeps today's wording.)

- [ ] **Step 5: Run tests.** Run: `python -m pytest v2/tests/test_query_correct_gate.py -v` — Expected: PASS. Then `python -m pytest v2/tests/ -k "gate or message_handler or rag" -q` — Expected: no regression.

- [ ] **Step 6: Commit.** `git add bot/core/message_handler.py v2/tests/test_query_correct_gate.py && git commit -m "feat(query-correct): gate/live/compose use retrieval_q (4-site fix incl. :799)"`

---

### Task 9: Telemetry + shadow measure-only mode

**Files:**
- Modify: `bot/core/message_handler.py` (the Task 7 block — add logging)
- Test: covered by extending Task 7's test to assert a log line is emitted (use `caplog`).

**Interfaces:**
- Produces: a single `logger.info` per correction with `original`, `q1`, `q2`, rescue tier (`kg`/`rag`/`none`), and whether each guard reverted — feeding the shadow FP analysis.

- [ ] **Step 1: Write the failing test.** Extend `test_query_correct_rescue.py` with a `caplog`-based test asserting an `INFO` record containing `"query-correct"` and the original + q2 is emitted on a rescue.

- [ ] **Step 2: Run to verify it fails.** Run: `python -m pytest v2/tests/test_query_correct_rescue.py -k telemetry -v` — Expected: FAIL.

- [ ] **Step 3: Implement.** In the Task 7 block, after computing `q2` (post-guards), add:

```python
                logger.info("query-correct: orig=%r q1=%r q2=%r tier=%s",
                            clean_text[:80], base_q[:80], q2[:80],
                            "kg" if q2 != base_q else "none")
```

(Update the tier string to `"rag"`/`"kg"` at the respective rescue sites if finer telemetry is wanted.)

- [ ] **Step 4: Run to verify it passes.** Run: `python -m pytest v2/tests/test_query_correct_rescue.py -k telemetry -v` — Expected: PASS.

- [ ] **Step 5: Commit.** `git add bot/core/message_handler.py v2/tests/test_query_correct_rescue.py && git commit -m "feat(query-correct): correction telemetry"`

---

### Task 10: Eval breadth + live smoke (evidence-before-claim)

**Files:**
- Modify: `eval/questions.txt`
- (No code; this task is the merge-gate evidence.)

- [ ] **Step 1: Add eval queries.** Append to `eval/questions.txt` under a `# query-correction` header: `heir of cs dep`, `profesor koutis research`, `quantom computing reserch`, `machne learning faculty`, `who is the chair of cs`.

- [ ] **Step 2: Run the full query-correct test suite.** Run: `python -m pytest v2/tests/ -k query_correct -v` — Expected: ALL PASS.

- [ ] **Step 3: Live smoke (flag ON, Ollama up).** Run: `QUERY_CORRECT_ENABLED=1 bash scripts/ask.sh "heir of cs dep" --answer`. Expected: routes to the CS chair from the KG (NOT a web/live result). Capture the output.

- [ ] **Step 4: Clean-path smoke.** Run: `QUERY_CORRECT_ENABLED=1 bash scripts/ask.sh "who is the chair of cs" --answer` and the same with the flag off. Expected: same answer (clean query unaffected).

- [ ] **Step 5: No-regression.** Run: `bash scripts/eval.sh --limit 60`. Expected: coverage/accuracy not below the pre-change baseline. Capture output.

- [ ] **Step 6: Show the diff + evidence to the owner; await sign-off before merge/restart** (HARD GATE — do not commit-to-main/restart without it). Then `git add eval/questions.txt && git commit -m "test(query-correct): eval queries + smoke evidence"`.

---

## Self-Review notes (gaps flagged honestly)

- **Test harness coupling:** Tasks 6–9 drive `handle()`/`_rag_pipeline` directly. The exact stub shape must match the existing `v2/tests` message-handler harness (find it first — `grep -rl "_rag_pipeline\|MessageHandler(" v2/tests`). If the existing harness differs from the fixtures sketched here, adopt the existing one; the assertions (what string routing/gate received) stay the same.
- **`retrieval_q` scope:** it is a local in `_rag_pipeline` introduced in Task 7 and consumed in Task 8 — Task 7 MUST land first (subagent-driven ordering).
- **Buttons:** giving the KG-rescue a `question_id` (Task 7) yields buttons via the existing connector path — consistent with the owner's 2026-06-29 buttons-on-all reversal. The broader buttons-on-all-answers change (structured/live paths globally) is a SEPARATE plan ([[project_open_items]] #10), not in scope here.
- **O1b / topic-inflation (G2):** not built (deferred per spec §7); Task 10's eval queries include a topic-typo case but a dedicated topic-inflation adversarial set lands with G2.
