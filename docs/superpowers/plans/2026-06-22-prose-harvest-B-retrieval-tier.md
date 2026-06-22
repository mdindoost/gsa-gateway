# Prose Harvest — Plan B: Retrieval Tier — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make `office_page` prose answerable WITHOUT diluting the curated corpus — exclude it from the primary retrieve, add an isolated office-tier retrieve that fires only on a primary miss (gated by its own floor), insert it into the precedence ladder BEFORE the live Brave fallback, and retire the Plan A stopgap guard now that isolation is structural.

**Architecture:** The retriever already supports `item_types=[...]` (whitelist that bypasses exclusion) and `exclude_types`. So: (1) add `office_page` to `DEFAULT_EXCLUDE_TYPES` (curated retrieve never sees it); (2) the office tier = a SECOND `retrieve(query, item_types=['office_page'])` call in `message_handler._rag_pipeline`, fired only when the curated result is a "primary miss" (the existing `top_relevance < LIVE_THRESHOLD` signal), adopted only if its own `OFFICE_THRESHOLD` floor is cleared, placed BEFORE the live Brave call. An office answer is a normal local-KB answer (`is_live=False`, `attempted_live=False`, source link surfaced, feedback buttons).

**Tech Stack:** Python 3.11, the existing `V2Retriever`, `bot/core/message_handler.py`, `bot/config.py`. Tests: pytest (+ `pytest.mark.asyncio` is NOT used here — use `asyncio.run` to drive the async pipeline, matching the repo's existing async tests).

## Global Constraints

- `office_page` MUST be excluded from the primary answer corpus at the CODE level (`DEFAULT_EXCLUDE_TYPES`), not just a mutable setting [SE2, RA1].
- The office tier is a SECOND retrieve scoped to `item_types=['office_page']` — searched in ISOLATION, never co-ranked with curated content [RA1].
- "Primary miss" = the EXISTING signal: `(not chunks) OR top_relevance(question, chunks) < LIVE_THRESHOLD (0.15)`. REUSE `top_relevance`; do NOT invent a second miss signal [RA2].
- The office tier has its OWN relevance floor `OFFICE_THRESHOLD` (default = `LIVE_THRESHOLD`, env-tunable); below it, do NOT answer from office prose — fall through to live [RA3].
- Precedence ladder: `structured → curated RAG (excl office_page) → LOCAL office tier → LIVE Brave → deflection`. Local office BEFORE live [RA6].
- An office answer sets `is_live=False` and does NOT set `attempted_live=True` (the user can still escalate to a live search) [RA6]. Source link surfaced as `source_note`.
- Do NOT change the curated retrieve call, the people path, or structured routing.
- HARD GATE: built TDD, diffs shown for sign-off; nothing merged to main / no restart without owner approval.

---

## File structure

- **Modify** `v2/core/retrieval/retriever.py:56` — add `'office_page'` to `DEFAULT_EXCLUDE_TYPES`.
- **Modify** `bot/config.py` (after `LIVE_THRESHOLD`, line ~142) — add `OFFICE_THRESHOLD`.
- **Modify** `bot/core/message_handler.py` (`_rag_pipeline`, lines ~622-668) — insert the office tier before the live block; surface the office source link.
- **Modify** `scripts/harvest_office.py` — remove the Plan A `--pre-tier-ok` stopgap (isolation is now structural).
- **Create** `v2/tests/test_office_tier_exclusion.py`, `v2/tests/test_office_threshold_config.py`, `bot/tests/test_office_fallback_tier.py`.
- **Modify** `v2/tests/test_harvest_office_cli.py` — update the stopgap test.

---

### Task 1: Exclude `office_page` from the primary corpus

**Files:**
- Modify: `v2/core/retrieval/retriever.py:56`
- Test: `v2/tests/test_office_tier_exclusion.py`

**Interfaces:**
- Consumes: existing `V2Retriever(conn, embedder, reranker=None)`, `V2Retriever._allowed_ids(org_id, org_subtree, item_types, exclude_types=None)`, `V2Retriever.exclude_types`.
- Produces: `DEFAULT_EXCLUDE_TYPES` now contains `'office_page'`.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_office_tier_exclusion.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.database.schema import create_all
from v2.core.graph.orgs import ensure_org
from v2.core.retrieval.retriever import DEFAULT_EXCLUDE_TYPES, V2Retriever


def _seed(conn):
    with conn:
        oid = ensure_org(conn, slug="njit", name="NJIT", parent_slug=None, type="university")
        cur = conn.execute(
            "INSERT INTO knowledge_items(org_id,type,title,content,is_active,created_by) "
            "VALUES(?,?,?,?,1,'dashboard')", (oid, "policy", "Curated", "curated body"))
        curated_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO knowledge_items(org_id,type,title,content,is_active,created_by) "
            "VALUES(?,?,?,?,1,'crawler')", (oid, "office_page", "Office", "office body"))
        office_id = cur.lastrowid
    return curated_id, office_id


def test_office_page_in_default_exclude_types():
    assert "office_page" in DEFAULT_EXCLUDE_TYPES


def test_default_retrieve_excludes_office_page_but_whitelist_includes_it(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    curated_id, office_id = _seed(conn)
    r = V2Retriever(conn, embedder=None)            # light ctor; _allowed_ids is pure SQL
    default_allowed = r._allowed_ids(None, None, None, exclude_types=r.exclude_types)
    assert office_id not in default_allowed and curated_id in default_allowed
    office_only = r._allowed_ids(None, None, ["office_page"], None)
    assert office_only == {office_id}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_office_tier_exclusion.py -q`
Expected: FAIL — `test_office_page_in_default_exclude_types` fails (`office_page` not in the frozenset) and the default-exclude test finds `office_id` still allowed.

- [ ] **Step 3: Add `office_page` to the exclude set**

In `v2/core/retrieval/retriever.py:56`, change:
```python
DEFAULT_EXCLUDE_TYPES = frozenset({"publication", "webpage"})
```
to:
```python
DEFAULT_EXCLUDE_TYPES = frozenset({"publication", "webpage", "office_page"})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest v2/tests/test_office_tier_exclusion.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add v2/core/retrieval/retriever.py v2/tests/test_office_tier_exclusion.py
git commit -m "feat(prose-harvest): exclude office_page from the primary answer corpus [SE2/RA1]"
```

---

### Task 2: `OFFICE_THRESHOLD` config

**Files:**
- Modify: `bot/config.py` (after `LIVE_THRESHOLD`, ~line 142)
- Test: `v2/tests/test_office_threshold_config.py`

**Interfaces:**
- Produces: `bot.config.OFFICE_THRESHOLD: float` — the office-tier relevance floor; env `OFFICE_THRESHOLD`, default = `LIVE_THRESHOLD`.

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_office_threshold_config.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
import bot.config as botcfg


def test_office_threshold_present_and_defaults_to_live_threshold():
    assert isinstance(botcfg.OFFICE_THRESHOLD, float)
    assert botcfg.OFFICE_THRESHOLD == botcfg.LIVE_THRESHOLD   # default: same floor as live
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_office_threshold_config.py -q`
Expected: FAIL — `AttributeError: module 'bot.config' has no attribute 'OFFICE_THRESHOLD'`.

- [ ] **Step 3: Add the config line**

In `bot/config.py`, immediately after the `LIVE_THRESHOLD = float(...)` line (~142), add:
```python
# Office-tier (local prose fallback) relevance floor. The office prose corpus
# (type='office_page') is consulted only on a primary KB miss, and only adopted when its best
# chunk clears this floor — else fall through to the live njit.edu fallback. Default = LIVE_THRESHOLD.
OFFICE_THRESHOLD = float(os.getenv("OFFICE_THRESHOLD", str(LIVE_THRESHOLD)))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest v2/tests/test_office_threshold_config.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add bot/config.py v2/tests/test_office_threshold_config.py
git commit -m "feat(prose-harvest): OFFICE_THRESHOLD config (office-tier floor, default=LIVE_THRESHOLD) [RA3]"
```

---

### Task 3: Insert the local office tier into `_rag_pipeline` (precedence ladder)

**Files:**
- Modify: `bot/core/message_handler.py` (`_rag_pipeline`, the live-fallback block ~622-638 + the source-note after generation ~665-668)
- Test: `bot/tests/test_office_fallback_tier.py`

**Interfaces:**
- Consumes: `self.retriever.retrieve(query, conversation_history, item_types=['office_page'])`, `self.retriever.top_relevance(text, chunks)`, `botcfg.OFFICE_THRESHOLD`, `botcfg.LIVE_THRESHOLD`, `self.live_search(text)`.
- Produces: an office answer when curated misses but office clears its floor — `chunks` becomes the office chunks (generation composes from them), `source_note` = the office page `source_url`, `is_live=False`, `attempted_live` NOT set, live Brave skipped.

- [ ] **Step 1: Write the failing test**

```python
# bot/tests/test_office_fallback_tier.py
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
import bot.config as botcfg
from bot.core.message_handler import MessageHandler, MessageRequest
from bot.core.intent import INTENT_QUESTION   # adjust import to where INTENT_QUESTION is defined


class FakeChunk:
    def __init__(self, text, source_url, rel):
        self.text = text; self.source_url = source_url; self.relevance_score = rel
        self.item_id = 1; self.source_file = "eos__visitor-parking"; self.section_title = "Visitor Parking"


class FakeRetriever:
    def __init__(self, office_rel):
        self.office_rel = office_rel
    async def retrieve(self, query=None, conversation_history=None, source_type_filter=None, item_types=None):
        if item_types == ["office_page"]:
            return [FakeChunk("Visitor parking is in the Lock Street Deck.",
                              "https://www.njit.edu/parking/visitor-parking", self.office_rel)]
        return []                                   # curated miss -> primary_miss
    def top_relevance(self, q, chunks):
        return chunks[0].relevance_score if chunks else None


class FakeOllama:
    async def generate_answer(self, question, chunks, conversation_history=None, temperature=0.3):
        return f"Visitor parking is in the Lock Street Deck. (doc_id {chunks[0].item_id})"
    async def expand_query(self, t): return t


class FakeConv:
    def get_mode(self, uid): return "gsa"
    def get_history(self, uid, max_turns=5): return []
    def add_turn(self, **k): pass


def _handler(office_rel):
    h = MessageHandler(retriever=FakeRetriever(office_rel), ollama=FakeOllama(),
                       conversation_manager=FakeConv(), intent_detector=None, db=None,
                       rate_limiter=None, kb=None, config=SimpleNamespace(conversation_max_turns=5))
    h.live_calls = 0
    async def _no_live(text):
        h.live_calls += 1
        return None
    h.live_search = _no_live                        # record whether live was reached
    return h


def test_office_tier_answers_before_live_and_is_not_live(monkeypatch):
    monkeypatch.setattr(botcfg, "LIVE_THRESHOLD", 0.15)
    monkeypatch.setattr(botcfg, "OFFICE_THRESHOLD", 0.15)
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", True)
    monkeypatch.setattr(botcfg, "BRAVE_API_KEY", "x")
    h = _handler(office_rel=0.9)                     # office clears its floor
    req = MessageRequest(user_id="u", text="where do I park", platform="discord")
    resp = asyncio.run(h._rag_pipeline(req, "where do I park", INTENT_QUESTION))
    assert "Lock Street Deck" in resp.text
    assert resp.source_note == "https://www.njit.edu/parking/visitor-parking"
    assert resp.is_live is False
    assert h.live_calls == 0                         # office preempted the live fallback


def test_office_below_floor_falls_through_to_live(monkeypatch):
    monkeypatch.setattr(botcfg, "LIVE_THRESHOLD", 0.15)
    monkeypatch.setattr(botcfg, "OFFICE_THRESHOLD", 0.5)   # high floor
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", True)
    monkeypatch.setattr(botcfg, "BRAVE_API_KEY", "x")
    h = _handler(office_rel=0.2)                     # office BELOW the 0.5 floor
    req = MessageRequest(user_id="u", text="where do I park", platform="discord")
    resp = asyncio.run(h._rag_pipeline(req, "where do I park", INTENT_QUESTION))
    assert h.live_calls == 1                         # office not adopted -> live attempted
```

(If `INTENT_QUESTION`'s import path differs, find it with `grep -rn "INTENT_QUESTION =" bot/` and adjust the import — do not change its definition.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest bot/tests/test_office_fallback_tier.py -q`
Expected: FAIL — with no office tier, the curated miss goes straight to live (`h.live_calls == 1` in the first test, and `resp.source_note` is not the office URL).

- [ ] **Step 3: Implement the office tier**

In `bot/core/message_handler.py`, REPLACE the existing live-fallback block (the comment + the `used_live=False ... attempted_live=False ... if botcfg.LIVE_ENABLED ...` block, ~lines 622-638) with:

```python
            # Primary miss → LOCAL office tier BEFORE the live njit.edu fallback (precedence
            # ladder). "primary miss" reuses the existing signal: no usable chunk OR best
            # reranked relevance < LIVE_THRESHOLD. The office prose corpus (type='office_page',
            # excluded from the primary retrieve) is searched in ISOLATION and adopted only when
            # its OWN floor (OFFICE_THRESHOLD) is cleared — else we fall through to live.
            used_live = False
            used_office = False
            attempted_live = False
            is_canned_deflection = False
            relevance = self.retriever.top_relevance(clean_text, chunks) if (self.retriever and chunks) else None
            primary_miss = (not chunks) or (relevance is not None and relevance < botcfg.LIVE_THRESHOLD)
            if primary_miss and self.retriever:
                office_chunks = await self.retriever.retrieve(
                    query=search_query, conversation_history=history, item_types=["office_page"])
                office_rel = self.retriever.top_relevance(clean_text, office_chunks) if office_chunks else None
                if office_chunks and office_rel is not None and office_rel >= botcfg.OFFICE_THRESHOLD:
                    chunks = office_chunks            # generate from local office prose (KB)
                    used_office = True
            if (primary_miss and not used_office and botcfg.LIVE_ENABLED and botcfg.BRAVE_API_KEY
                    and self.ollama and self.retriever):
                attempted_live = True
                live = await self.live_search(clean_text)   # single seam (provider wiring + gate)
                if live is not None:
                    response_text = live.text
                    source_note = live.source_url
                    used_ai = True
                    used_live = True
                    logger.info("live njit.edu fallback answered from %s", live.source_url)
```

Then, immediately AFTER the generation if/elif chain (after the `elif chunks:` raw-text branch and before the `else:` canned-deflection branch is NOT possible — instead add this right after the whole generate block, i.e. after the line that sets `ollama_failed = True`'s elif/else chain closes, before the "Deflection offer" comment ~line 680), insert:

```python
            # Office answers surface the authoritative njit.edu page as the verify-link (RA6),
            # mirroring the live-fallback's source_url note.
            if used_office and chunks:
                source_note = getattr(chunks[0], "source_url", None) or source_note
```

Leave the `MessageResponse(... is_live=used_live)` return unchanged — `used_office` keeps `is_live=False` and does not set `attempted_live`, so the user can still escalate to live.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest bot/tests/test_office_fallback_tier.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Regression — message_handler + live-fallback tests still green**

Run: `python3 -m pytest bot/tests/ -q -k "offer_live or message_handler or live"`
Expected: no new failures (the live block's behavior is unchanged when there is no office hit; `primary_miss` is the same condition as before).

- [ ] **Step 6: Commit**

```bash
git add bot/core/message_handler.py bot/tests/test_office_fallback_tier.py
git commit -m "feat(prose-harvest): local office tier in the precedence ladder (before live Brave) [RA2/RA3/RA6]"
```

---

### Task 4: Retire the Plan A `--pre-tier-ok` stopgap (isolation is now structural)

**Files:**
- Modify: `scripts/harvest_office.py` (remove the `--pre-tier-ok` flag + the block that returned 2)
- Test: `v2/tests/test_harvest_office_cli.py` (replace the stopgap test)

**Interfaces:**
- Produces: `harvest_office.main` no longer special-cases `--pre-tier-ok`; the only gate is the standard dry-run-default / `--commit`. Dilution is now prevented structurally by Task 1 (office_page excluded from the primary corpus).

- [ ] **Step 1: Update the test FIRST (red)**

In `v2/tests/test_harvest_office_cli.py`, REPLACE `test_commit_without_pre_tier_ok_is_blocked` with:

```python
def test_pre_tier_ok_flag_removed_and_office_page_isolated():
    # The Plan A stopgap is retired: --pre-tier-ok is no longer a valid arg, because dilution
    # is now prevented structurally (office_page excluded from the primary corpus, Plan B Task 1).
    import pytest
    from scripts.harvest_office import main
    from v2.core.retrieval.retriever import DEFAULT_EXCLUDE_TYPES
    assert "office_page" in DEFAULT_EXCLUDE_TYPES
    with pytest.raises(SystemExit):                  # unknown arg -> argparse error
        main(["--prefix-unused-ignore", "--pre-tier-ok"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_harvest_office_cli.py::test_pre_tier_ok_flag_removed_and_office_page_isolated -q`
Expected: FAIL — `--pre-tier-ok` is still a recognized arg (no SystemExit) OR the old test name is gone; either way RED before the code change.

- [ ] **Step 3: Remove the stopgap from `harvest_office.py`**

Delete the `ap.add_argument("--pre-tier-ok", ...)` line and the guard block:
```python
    if args.commit and not args.pre_tier_ok:
        print(... "*** BLOCKED — office_page is NOT retrieval-isolated yet ..." ...)
        return 2
```
Restore the plain dry-run notice to its original form:
```python
    if not args.commit:
        print("(dry run — pass --commit; a hardened backup is taken first)")
        return 0
```
and drop the "(office_page is not retrieval-isolated until Plan B …)" tail from the final print, leaving:
```python
    print("next: python v2/scripts/embed_all.py  (then review staged high-stakes pages)")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest v2/tests/test_harvest_office_cli.py -q`
Expected: PASS (the orchestrator test + the updated isolation/flag-removed test).

- [ ] **Step 5: Commit**

```bash
git add scripts/harvest_office.py v2/tests/test_harvest_office_cli.py
git commit -m "chore(prose-harvest): retire --pre-tier-ok stopgap; office_page now structurally isolated [Plan B]"
```

---

## Self-review (run before execution)

- **Spec coverage (Plan B scope, §4.4):** office_page excluded from primary corpus at code level (Task 1) ✓ [SE2/RA1]; isolated `item_types=['office_page']` retrieve (Task 3 — reuses the retriever's existing whitelist) ✓ [RA1]; primary miss = existing `top_relevance < LIVE_THRESHOLD` (Task 3, reused — not reinvented) ✓ [RA2]; own floor `OFFICE_THRESHOLD` (Tasks 2+3) ✓ [RA3]; precedence ladder local-office-before-live + response flags `is_live=False`/`attempted_live` unset + source link (Task 3) ✓ [RA6]; Plan A stopgap retired (Task 4) ✓.
- **Deferred (loudly):** recurrence/404-410 retire + self-extension = **Plan C**; Wave-1 harvest + chat verify + eval gate = **Plan D**. The office tier is now wired but answers nothing until Plan D ingests office_page content (and `OFFICE_THRESHOLD` may be tuned against real pages in Plan D).
- **Placeholder scan:** none — full code/commands in every step. The two import-path caveats (`INTENT_QUESTION`, the exact post-generation insertion line) are explicit grep-and-adjust instructions, not placeholders.
- **Type consistency:** `top_relevance(text, chunks) -> float|None`, `retrieve(..., item_types=['office_page'])` async, `chunks[0].source_url` — all match the real signatures verified in retriever.py / message_handler.py. `is_live=used_live` and the unset `attempted_live` are consistent with the MessageResponse contract.
```
