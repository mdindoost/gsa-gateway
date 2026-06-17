# Live njit.edu Search Fallback (Grounded Extractive) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the KB can't confidently answer an NJIT question, live-search njit.edu (Brave), fetch the top page, and answer from verbatim, page-grounded excerpts + the source link — never inventing.

**Architecture:** Three small units sharing one grounded-extract core: `grounded_extract` (LLM selects verbatim spans → keep only spans literally on the page), `njit_search` (Brave Search API, injectable HTTP), and `live_fallback` (orchestration), wired into `message_handler`'s RAG-miss branch behind a relevance trigger + kill-switch. Extractive only (no generative rewrite) → no hallucination.

**Tech Stack:** Python 3.11, urllib, the existing Ollama client + reranker + `explore.http_fetch` + `web_crawler.clean_text`, pytest.

**Design spec:** `docs/superpowers/specs/2026-06-17-live-search-fallback-design.md` (read it).
**Branch:** `feat/page-crawler` (current — this is Sub-project 1; the batch crawler is Sub-project 2 on the same track).

---

## File Structure
- `v2/core/ingestion/grounded_extract.py` — verbatim-span extraction + literal-presence grounding (shared core).
- `v2/integration/njit_search.py` — Brave Search API client (injectable HTTP).
- `bot/core/live_fallback.py` — orchestration: search → fetch → extract → answer|None.
- `bot/config.py` — `BRAVE_API_KEY`, `live_enabled`, `live_threshold` (modify).
- `bot/core/message_handler.py` — RAG-miss → live fallback (modify).
- `v2/integration/retriever_shim.py` — expose `top_relevance(query, chunks)` (modify).
- Tests: `v2/tests/test_grounded_extract.py`, `v2/tests/test_njit_search.py`, `v2/tests/test_live_fallback.py`.

---

## Task 1: Grounded-extract core (the trust core)

**Files:**
- Create: `v2/core/ingestion/grounded_extract.py`
- Test: `v2/tests/test_grounded_extract.py`

- [ ] **Step 1: Write the failing tests**

Create `v2/tests/test_grounded_extract.py`:

```python
from v2.core.ingestion.grounded_extract import answer_from_page, ground_spans, build_extract_prompt

PAGE = ("Graduate Admission. The application fee is $75 and is non-refundable. "
        "International applicants must submit TOEFL scores with a minimum of 79.")

def test_keeps_verbatim_span_present_on_page():
    llm = lambda s, u: '{"spans": ["The application fee is $75 and is non-refundable."]}'
    ans = answer_from_page("how much is the fee", PAGE, "https://njit.edu/x", llm)
    assert ans is not None
    assert ans.spans == ["The application fee is $75 and is non-refundable."]
    assert ans.source_url == "https://njit.edu/x"

def test_drops_hallucinated_span_not_on_page():
    # the model invents a fee that is NOT on the page -> must be dropped -> None
    llm = lambda s, u: '{"spans": ["The application fee is $200."]}'
    assert answer_from_page("fee", PAGE, "https://njit.edu/x", llm) is None

def test_none_when_no_spans():
    llm = lambda s, u: '{"spans": []}'
    assert answer_from_page("parking", PAGE, "https://njit.edu/x", llm) is None

def test_none_on_bad_json():
    llm = lambda s, u: 'not json'
    assert answer_from_page("fee", PAGE, "https://njit.edu/x", llm) is None

def test_prompt_contains_question_and_page():
    sys, user = build_extract_prompt("how much is the fee", PAGE)
    assert "how much is the fee" in user and "$75" in user and "VERBATIM" in sys.upper()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest v2/tests/test_grounded_extract.py -q`
Expected: FAIL (ModuleNotFoundError: grounded_extract).

- [ ] **Step 3: Implement the module**

Create `v2/core/ingestion/grounded_extract.py`:

```python
"""Extractive, span-grounded answering from a fetched page.

The LLM SELECTS verbatim spans from the page that answer the question; we keep a span only
if it appears literally on the page (whitespace-normalized substring). No generative rewrite,
so the combination/paraphrase hallucination class cannot occur. Returns None if nothing
grounded survives -> we never fabricate. `call_llm(system, user) -> str` is injected so tests
run offline. This core is shared with the batch crawler (Sub-project 2)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

_SYS = (
    "You extract answers from an official NJIT web page. Given the PAGE text and a student's "
    "QUESTION, return ONLY exact sentences COPIED VERBATIM from the page that answer the "
    "question. Do NOT paraphrase, summarize, combine, translate, or add anything not on the "
    'page. Respond with strict JSON: {"spans": ["<verbatim sentence>", ...]}. '
    'If the page does not answer the question, respond {"spans": []}.'
)
_MAX_PAGE_CHARS = 12000
_MIN_SPAN = 12
_MAX_SPANS = 6


@dataclass
class AnswerSpans:
    spans: list[str]
    source_url: str


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def build_extract_prompt(question: str, page_text: str) -> tuple[str, str]:
    user = f"QUESTION: {question}\n\nPAGE:\n{page_text[:_MAX_PAGE_CHARS]}"
    return _SYS, user


def ground_spans(llm_raw: str, page_text: str, source_url: str) -> "AnswerSpans | None":
    try:
        blob = llm_raw[llm_raw.index("{"): llm_raw.rindex("}") + 1]
        cand = json.loads(blob).get("spans") or []
    except (ValueError, json.JSONDecodeError):
        return None
    page_n = _norm(page_text)
    kept: list[str] = []
    seen: set[str] = set()
    for s in cand:
        if not isinstance(s, str):
            continue
        s = s.strip()
        sn = _norm(s)
        if len(sn) >= _MIN_SPAN and sn in page_n and sn not in seen:
            seen.add(sn)
            kept.append(s)
    return AnswerSpans(kept[:_MAX_SPANS], source_url) if kept else None


def answer_from_page(question: str, page_text: str, source_url: str, call_llm) -> "AnswerSpans | None":
    system, user = build_extract_prompt(question, page_text)
    try:
        raw = call_llm(system, user)
    except Exception:
        return None
    return ground_spans(raw or "", page_text, source_url)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest v2/tests/test_grounded_extract.py -q`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/md724/gsa-gateway
git add v2/core/ingestion/grounded_extract.py v2/tests/test_grounded_extract.py
git commit -m "feat(grounded-extract): verbatim-span extraction + literal-presence grounding (shared core)"
```

---

## Task 2: Brave Search API client

**Files:**
- Create: `v2/integration/njit_search.py`
- Test: `v2/tests/test_njit_search.py`

- [ ] **Step 1: Write the failing tests**

Create `v2/tests/test_njit_search.py`:

```python
import json
from v2.integration.njit_search import search

BRAVE_JSON = json.dumps({"web": {"results": [
    {"url": "https://www.njit.edu/registrar/registration"},
    {"url": "https://catalog.njit.edu/x"},
    {"url": "https://evil.example.com/x"},
]}})

def test_returns_njit_urls_only():
    got = search("how do I register", k=3, http_get=lambda url, headers: BRAVE_JSON, key="K")
    assert got == ["https://www.njit.edu/registrar/registration", "https://catalog.njit.edu/x"]

def test_empty_without_key():
    assert search("x", http_get=lambda url, headers: BRAVE_JSON, key="") == []

def test_empty_on_error():
    def boom(url, headers): raise RuntimeError("network")
    assert search("x", http_get=boom, key="K") == []

def test_scopes_query_to_njit():
    captured = {}
    def cap(url, headers):
        captured["url"] = url
        return BRAVE_JSON
    search("parking rules", http_get=cap, key="K")
    assert "njit.edu" in captured["url"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest v2/tests/test_njit_search.py -q`
Expected: FAIL (ModuleNotFoundError: njit_search).

- [ ] **Step 3: Implement the module**

Create `v2/integration/njit_search.py`:

```python
"""Brave Search API client scoped to njit.edu. search(query) -> top njit.edu URLs.

Network is injected (`http_get(url, headers) -> str`) so unit tests need no key. Returns []
on any error (missing key, quota, network) so the live fallback degrades to today's decline.
The API key is read from BRAVE_API_KEY (never committed)."""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_UA = "GSA-Gateway-Bot/1.0 (+https://github.com/mdindoost/gsa-gateway)"


def _default_get(url: str, headers: dict) -> str:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.read().decode("utf-8", "replace")


def search(query: str, k: int = 3, http_get=_default_get, key: str | None = None) -> list[str]:
    key = key if key is not None else os.getenv("BRAVE_API_KEY", "")
    if not key:
        return []
    q = f"{query} site:njit.edu"
    url = f"{_ENDPOINT}?{urllib.parse.urlencode({'q': q, 'count': max(k, 5)})}"
    headers = {"X-Subscription-Token": key, "Accept": "application/json", "User-Agent": _UA}
    try:
        results = json.loads(http_get(url, headers)).get("web", {}).get("results", [])
        urls = [r["url"] for r in results if "njit.edu" in (r.get("url") or "")]
        return urls[:k]
    except Exception:
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest v2/tests/test_njit_search.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/md724/gsa-gateway
git add v2/integration/njit_search.py v2/tests/test_njit_search.py
git commit -m "feat(njit-search): Brave Search API client scoped to njit.edu (injectable, fail-soft)"
```

---

## Task 3: Config + expose KB-miss relevance on the shim

**Files:**
- Modify: `bot/config.py` (add settings)
- Modify: `v2/integration/retriever_shim.py` (add `top_relevance`)
- Test: extend `v2/tests/test_grounded_extract.py` is not it — add `v2/tests/test_live_fallback.py` later covers the shim via mock.

- [ ] **Step 1: Add config settings**

In `bot/config.py`, add these module-level settings near the other config values (read env with defaults):

```python
# --- Live njit.edu search fallback (Sub-project 1) ---
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
# live fallback fires when the top reranked chunk's relevance < LIVE_THRESHOLD (0..1),
# or there are no chunks. Set LIVE_ENABLED=0 to disable entirely (kill-switch).
LIVE_ENABLED = os.getenv("LIVE_ENABLED", "1") == "1"
LIVE_THRESHOLD = float(os.getenv("LIVE_THRESHOLD", "0.15"))
```

(If `bot/config.py` does not already `import os`, add it at the top.)

- [ ] **Step 2: Add `top_relevance` to the shim**

In `v2/integration/retriever_shim.py`, add a method to the `V2RetrieverShim` class that scores
the top returned chunk with the reranker (the answerability signal). The chunk objects the shim
returns expose `.text`:

```python
    def top_relevance(self, query: str, chunks) -> "float | None":
        """Cross-encoder relevance (0..1) of the best returned chunk — the KB-miss signal.
        None if no reranker or no chunks (caller treats None as 'cannot judge')."""
        if not getattr(self, "reranker", None) or not chunks:
            return None
        try:
            scores = self.reranker.score(query, [chunks[0].text])
        except Exception:
            return None
        return float(scores[0]) if scores else None
```

(If the shim stores the reranker under a different attribute name, use that name — check the
`__init__`. The reranker's `.score(query, [text]) -> list[float]` already returns 0..1 values.)

- [ ] **Step 3: Verify imports/syntax**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -c "import bot.config as c; print(c.LIVE_ENABLED, c.LIVE_THRESHOLD); from v2.integration.retriever_shim import V2RetrieverShim; print(hasattr(V2RetrieverShim,'top_relevance'))"`
Expected: prints `True 0.15` and `True`.

- [ ] **Step 4: Commit**

```bash
cd /home/md724/gsa-gateway
git add bot/config.py v2/integration/retriever_shim.py
git commit -m "feat(live-fallback): config (BRAVE_API_KEY/LIVE_*) + shim.top_relevance KB-miss signal"
```

---

## Task 4: Live-fallback orchestration + wire into the handler

**Files:**
- Create: `bot/core/live_fallback.py`
- Modify: `bot/core/message_handler.py` (RAG-miss branch)
- Test: `v2/tests/test_live_fallback.py`

- [ ] **Step 1: Write the failing tests**

Create `v2/tests/test_live_fallback.py`:

```python
import asyncio
from bot.core.live_fallback import maybe_answer_live

PAGE_HTML = "<html><body><p>Visitor parking is available in the Lock Street Deck.</p></body></html>"

def _run(coro): return asyncio.run(coro)

def test_returns_grounded_answer_with_link():
    search_fn = lambda q: ["https://www.njit.edu/parking"]
    fetch_fn = lambda u: ("https://www.njit.edu/parking", PAGE_HTML, "200")
    llm = lambda s, u: '{"spans": ["Visitor parking is available in the Lock Street Deck."]}'
    ans = _run(maybe_answer_live("where do visitors park", search_fn=search_fn,
                                 fetch_fn=fetch_fn, call_llm=llm))
    assert ans is not None
    assert "Lock Street Deck" in ans.text
    assert ans.source_url == "https://www.njit.edu/parking"

def test_none_when_no_search_results():
    ans = _run(maybe_answer_live("x", search_fn=lambda q: [], fetch_fn=lambda u: ("", "", "200"),
                                 call_llm=lambda s, u: '{"spans": []}'))
    assert ans is None

def test_none_when_page_does_not_answer():
    search_fn = lambda q: ["https://www.njit.edu/parking"]
    fetch_fn = lambda u: ("https://www.njit.edu/parking", PAGE_HTML, "200")
    llm = lambda s, u: '{"spans": []}'  # page has no answer
    assert _run(maybe_answer_live("tuition cost", search_fn=search_fn, fetch_fn=fetch_fn,
                                  call_llm=llm)) is None

def test_skips_failed_fetch_then_tries_next():
    calls = {"n": 0}
    def fetch_fn(u):
        calls["n"] += 1
        if calls["n"] == 1:
            return ("", "", "404")        # first result fails
        return ("https://www.njit.edu/parking", PAGE_HTML, "200")
    search_fn = lambda q: ["https://www.njit.edu/bad", "https://www.njit.edu/parking"]
    llm = lambda s, u: '{"spans": ["Visitor parking is available in the Lock Street Deck."]}'
    ans = _run(maybe_answer_live("parking", search_fn=search_fn, fetch_fn=fetch_fn, call_llm=llm))
    assert ans is not None and calls["n"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest v2/tests/test_live_fallback.py -q`
Expected: FAIL (ModuleNotFoundError: live_fallback).

- [ ] **Step 3: Implement the orchestrator**

Create `bot/core/live_fallback.py`:

```python
"""KB-miss -> live njit.edu search -> fetch top page -> extractive grounded answer + link.

Returns None if search/fetch/extract yields nothing grounded (caller keeps today's decline).
All collaborators are injected: `search_fn(query)->[url]`, `fetch_fn(url)->(final_url, html,
status)`, `call_llm(system, user)->str`. Network/LLM run in threads so the bot event loop is
not blocked."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from v2.core.ingestion.grounded_extract import answer_from_page
from v2.core.ingestion.web_crawler import clean_text

logger = logging.getLogger(__name__)


@dataclass
class LiveAnswer:
    text: str
    source_url: str


def _format(spans: list[str], source_url: str) -> str:
    body = " ".join(spans)
    return f"From NJIT's website: {body}\n\nSource: {source_url}"


async def maybe_answer_live(question, *, search_fn, fetch_fn, call_llm, max_pages: int = 2):
    try:
        urls = await asyncio.to_thread(search_fn, question)
    except Exception:
        logger.warning("live search failed", exc_info=True)
        return None
    for url in (urls or [])[:max_pages]:
        try:
            final_url, html, status = await asyncio.to_thread(fetch_fn, url)
        except Exception:
            continue
        if not html or not str(status).startswith("2"):
            continue
        page_text = clean_text(html)
        ans = await asyncio.to_thread(
            answer_from_page, question, page_text, final_url or url, call_llm)
        if ans is not None:
            return LiveAnswer(text=_format(ans.spans, ans.source_url), source_url=ans.source_url)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest v2/tests/test_live_fallback.py -q`
Expected: 4 passed.

- [ ] **Step 5: Wire into the handler's RAG-miss branch**

In `bot/core/message_handler.py`, find the RAG pipeline's deflection point — where, after KB
retrieval, the code returns the "I couldn't find specific information / contact a GSA officer"
`MessageResponse` (the no-good-answer branch). Replace that deflection with a live-fallback
attempt. Add this import at the top of the file:

```python
from bot.core.live_fallback import maybe_answer_live
from bot.core.headsup import apply_headsup
import bot.config as config
from v2.integration.njit_search import search as brave_search
from v2.core.ingestion.explore import http_fetch
```

Then, at the deflection point (where `chunks` is empty OR the top relevance is below threshold),
insert BEFORE returning the deflection text:

```python
        # KB miss -> live njit.edu fallback (Sub-project 1). Fires when there is no usable KB
        # chunk OR the best chunk's reranker relevance is below threshold. Kill-switch via config.
        relevance = self.retriever.top_relevance(query, chunks) if chunks else None
        kb_miss = (not chunks) or (relevance is not None and relevance < config.LIVE_THRESHOLD)
        if config.LIVE_ENABLED and config.BRAVE_API_KEY and kb_miss:
            def _llm(system, user):
                return asyncio.run(self.ollama.generate(user, system))
            live = await maybe_answer_live(
                query, search_fn=brave_search, fetch_fn=http_fetch, call_llm=_llm)
            if live is not None:
                text = apply_headsup(live.text, query)
                return MessageResponse(text=text)
        # else: fall through to today's deflection (unchanged)
```

Notes for the implementer: `query` is the user's question variable in scope (use whatever the
function already calls it). `self.ollama.generate(prompt, system)` is the existing async Ollama
call — confirm its argument order in `bot/services/ollama_client.py` and match it; here we bridge
it to the sync `call_llm` the extractor expects via `asyncio.run` **inside the worker thread**
(`maybe_answer_live` calls `call_llm` through `asyncio.to_thread`, so a nested `asyncio.run` is
safe — it is not on the bot's main loop). `MessageResponse` and `self.ollama` are already used in
this file.

- [ ] **Step 6: Verify the wiring imports cleanly + unit tests still pass**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -c "import bot.core.message_handler" && .venv/bin/python -m pytest v2/tests/test_live_fallback.py v2/tests/test_grounded_extract.py v2/tests/test_njit_search.py -q`
Expected: import OK; 13 passed.

- [ ] **Step 7: Commit**

```bash
cd /home/md724/gsa-gateway
git add bot/core/live_fallback.py bot/core/message_handler.py v2/tests/test_live_fallback.py
git commit -m "feat(live-fallback): orchestration + handler wiring (KB miss -> grounded njit.edu answer)"
```

---

## Task 5: Live smoke (needs the Brave key) + finalize

**Files:** none (runtime verification)

- [ ] **Step 1: Add the key to `.env` (when the maintainer provides it)**

Append to `/home/md724/gsa-gateway/.env` (create if absent; `.env` is git-ignored — verify with
`git check-ignore .env`):

```
BRAVE_API_KEY=<the token the maintainer sends>
```

- [ ] **Step 2: Live smoke — an uncovered question returns a grounded njit.edu answer**

Run (after the key is set):
```bash
cd /home/md724/gsa-gateway && set -a && . ./.env && set +a && .venv/bin/python - <<'EOF'
import asyncio
from v2.integration.njit_search import search
from v2.core.ingestion.explore import http_fetch
from bot.core.live_fallback import maybe_answer_live
from bot.services.ollama_client import OllamaClient
oc = OllamaClient()
def _llm(s, u): return asyncio.run(oc.generate(u, s))
for q in ["where can visitors park at NJIT", "when is spring break at NJIT"]:
    ans = asyncio.run(maybe_answer_live(q, search_fn=search, fetch_fn=http_fetch, call_llm=_llm))
    print(f"\nQ: {q}\n{'-> '+ans.text if ans else '-> (no grounded answer; would deflect)'}")
EOF
```
Expected: at least one question returns text containing a verbatim NJIT snippet + a `njit.edu`
`Source:` link; an unanswerable one prints the deflect line. (If `search` returns `[]`, the key
isn't loaded — re-check `.env`.)

- [ ] **Step 3: Regression — covered questions still answer instantly from the KB**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest v2/tests/test_admissions_gold.py v2/tests/test_international_gold.py v2/tests/test_office_routing_gold.py -q -m slow`
Expected: 52 passed (the live path only fires below threshold; covered questions are unchanged).

- [ ] **Step 4: Restart the bots + spot check end-to-end**

Run: `cd /home/md724/gsa-gateway && bash scripts/restart.sh` then ask the bot (Discord/Telegram) a
covered question (instant KB answer) and an uncovered one (e.g. "where do visitors park?") — the
latter should return a grounded answer citing a njit.edu link, within a few seconds.

- [ ] **Step 5: Mark spec implemented + record results**

In `docs/superpowers/specs/2026-06-17-live-search-fallback-design.md`, set Status to
`Implemented (2026-06-17)` and append the smoke result (which uncovered questions got grounded
answers, regression 52/52).

```bash
cd /home/md724/gsa-gateway
git add docs/superpowers/specs/2026-06-17-live-search-fallback-design.md
git commit -m "docs: mark live-search fallback implemented + record smoke/regression results"
```

- [ ] **Step 6: Report** the smoke result + regression, then proceed to finishing-a-development-branch (merge + restart per the maintainer's call). Sub-project 2 (the batch crawler, reusing `grounded_extract`) is the next track.
