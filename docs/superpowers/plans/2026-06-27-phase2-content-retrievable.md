# Phase 2 (Track B) — Make Content Retrievable — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make answer-bearing content retrievable — fix M2 long-page semantic blindness as a safe deep-fallback chunk-rescue, extract PDF-trapped content, and keep the chunk index invalidation-complete — without regressing the common case.

**Architecture:** Reuse the chunk infra already on this branch (`chunker`, `chunk_populate`, `knowledge_chunks`/`knowledge_chunk_vectors`, `_semantic_chunks`, `embed_chunks`, `vector_gc`). Expose chunks ONLY as a rescue: `retrieve_deep()` (a thin wrapper over `retrieve(semantic_mode="chunk")`) is consulted in `_rag_pipeline` only on a primary-miss, and its result is adopted ONLY if it scores strictly better than the existing chunks (the no-regression contract). PDF text comes in via a new pure `pdf_extract` module, ingested as `type='pdf'` rows that get chunked by the existing batch pass.

**Tech Stack:** Python 3.12, sqlite + sqlite-vec (vec0), Ollama nomic-embed-text (768d), pypdf 6.x, pytest.

**Spec:** `docs/superpowers/specs/2026-06-27-phase2-content-retrievable-design.md` (rev 2). Reviewer findings #N referenced inline.

## Global Constraints

- **Flags default OFF.** `RETRIEVAL_DEEP_FALLBACK` OFF; `RETRIEVAL_CHUNKS` (always-on) stays OFF/untouched. Flag-off ⇒ retrieval control flow unchanged.
- **LLM-agnostic / use-max-capacity:** all sizes/prefixes via `v2/core/retrieval/model_descriptor.py` (`active_descriptor()`); never a magic constant. pypdf isolated behind `pdf_extract`.
- **Verbatim / never-withhold:** chunks find, full PARENT pages serve. PDF cleanup = text-preserving whitespace normalization ONLY (no token-join, no punctuation inference, no row reconstruction). Degraded tables are served-with-safeguards, never withheld.
- **Crawl = mechanical / data-bringing only:** no serving/gating logic in extraction or ingestion.
- **Gated writes:** any live-DB write uses `scripts/_area_tag_migrate.py::hardened_backup`, dry-run default, `--commit`. Immortal posts/post_deliveries + judging_* untouched. All evals run on a COPY (`/tmp/*.db`), never the live DB. `LIVE_ENABLED=0` in evals (no Brave cost).
- **No attribution** in commit messages (per `feedback_no_attribution`). Commit with `git -c commit.gpgsign=false`.
- **Eval harness gotchas:** call `retriever.retrieve(q, limit=5)` with the `limit=` keyword (2nd positional is `org_id`). Shim honors `DATABASE_PATH`.
- **Worktree:** all work in `/home/md724/gsa-gateway/.claude/worktrees/teacher-eval-phase2` on branch `worktree-teacher-eval-phase2`. NEVER write to the main checkout path.

---

### Task 1: `pdf_extract.py` — pure PDF text extraction module

**Files:**
- Create: `v2/core/ingestion/pdf_extract.py`
- Create: `v2/tests/test_pdf_extract.py`
- Create: `v2/tests/fixtures/pdf/calendar.pdf`, `v2/tests/fixtures/pdf/tuition.pdf` (real NJIT PDFs, tiny: 18 KB / 20 KB)
- Create: `v2/tests/fixtures/pdf/README.md` (source URL + date + sha256 per fixture, finding #17)
- Modify: `requirements.txt` (add `pypdf`)

**Interfaces:**
- Produces: `extract_pdf_text(source) -> ExtractResult` where
  `ExtractResult = dataclass(text: str | None, status: str, n_pages: int, median_chars_per_page: int, bytes_per_text_char: float, table_degraded: bool, reason: str)`;
  `status ∈ {"ok","empty","image_heavy","mixed_low_text","invalid"}`. `source` = a filesystem path (str/Path) OR raw `bytes`. `text` is None for `empty`/`image_heavy`/`invalid`.

- [ ] **Step 1: Add the dependency and fetch fixtures**

Run:
```bash
cd /home/md724/gsa-gateway/.claude/worktrees/teacher-eval-phase2
grep -q '^pypdf' requirements.txt || echo 'pypdf>=6,<7' >> requirements.txt
pip install --user --break-system-packages -q 'pypdf>=6,<7'
mkdir -p v2/tests/fixtures/pdf
UA="GSA-Gateway-Bot/2.0 (+https://gsanjit.com)"
curl -sL -A "$UA" -o v2/tests/fixtures/pdf/calendar.pdf "https://catalog.njit.edu/about-university/academic-calendar/academic-calendar.pdf"
curl -sL -A "$UA" -o v2/tests/fixtures/pdf/tuition.pdf  "https://catalog.njit.edu/undergraduate/admissions-financial-aid/tuition-fees/tuition-fees.pdf"
python3 - <<'PY'
import hashlib,glob
for f in sorted(glob.glob("v2/tests/fixtures/pdf/*.pdf")):
    print(f, hashlib.sha256(open(f,'rb').read()).hexdigest()[:16], "OK" if open(f,'rb').read(5)==b'%PDF-' else "NOT-A-PDF")
PY
```
Expected: both print `OK` (valid `%PDF-` header). Write the printed sha256s + source URLs into `v2/tests/fixtures/pdf/README.md`.

- [ ] **Step 2: Write the failing tests**

```python
# v2/tests/test_pdf_extract.py
from pathlib import Path
from v2.core.ingestion.pdf_extract import extract_pdf_text

FIX = Path(__file__).parent / "fixtures" / "pdf"

def test_prose_pdf_extracts_clean_text():
    r = extract_pdf_text(FIX / "calendar.pdf")
    assert r.status == "ok"
    assert r.text and "Academic Calendar" in r.text
    # newline->space normalization: no raw newlines, no mid-word joins of separate words
    assert "\n" not in r.text
    assert "Last Day to Add/Drop a Class" in r.text       # facts intact, words separated

def test_cleanup_is_text_preserving_no_token_join():
    # wrapped separate words must keep their boundary (finding #8): "an undergraduate" not "anundergraduate"
    raw = "as part of an\nundergraduate program"
    from v2.core.ingestion.pdf_extract import _clean
    assert _clean(raw) == "as part of an undergraduate program"

def test_dense_numeric_table_flagged_degraded():
    r = extract_pdf_text(FIX / "tuition.pdf")
    assert r.status == "ok"
    assert r.table_degraded is True            # tuition schedule = degraded numeric grid
    assert "Tuition and Fees" in r.text

def test_invalid_pdf_skipped():
    r = extract_pdf_text(b"<!DOCTYPE html><html>not a pdf</html>")
    assert r.status == "invalid"
    assert r.text is None

def test_image_heavy_pdf_skipped(tmp_path):
    # synth a minimal multi-page PDF with ~no text but large byte size -> image_heavy heuristic
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(8):
        w.add_blank_page(width=600, height=800)
    p = tmp_path / "img.pdf"
    # pad to look image-heavy: low chars/page AND high bytes/text-char
    with open(p, "wb") as fh:
        w.write(fh); fh.write(b"%" + b"0" * 200000)   # trailing bytes inflate size (header still valid)
    r = extract_pdf_text(p)
    assert r.status in ("image_heavy", "empty")
    assert r.text is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest v2/tests/test_pdf_extract.py -q`
Expected: FAIL (`ModuleNotFoundError: pdf_extract` / `extract_pdf_text` undefined).

- [ ] **Step 4: Implement the module**

```python
# v2/core/ingestion/pdf_extract.py
"""Mechanical PDF text extraction (provider-isolated behind pypdf).

Crawl-lane, mechanical-only: pypdf default extraction + text-preserving whitespace
normalization. NO OCR, NO rewriting, NO row reconstruction. Image-only / invalid PDFs
are skip-flagged (status), never faked. Dense numeric tables extract verbatim but with
degraded row boundaries -> table_degraded=True (serving layer adds source-link + warning).
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfError

# image-heavy heuristic (validated on real NJIT PDFs 2026-06-27)
_MIN_CHARS_PER_PAGE = 200
_MIN_BYTES_PER_TEXT_CHAR = 800
# dense-numeric-table heuristic: many runs of digits with merged decimals (e.g. "939.001.5")
_MERGED_NUMERIC = re.compile(r"\d+\.\d{2}\d")            # a 2-dp number immediately followed by another digit
_MANY_NUMBERS = re.compile(r"\d[\d,]*\.\d{2}")


@dataclass
class ExtractResult:
    text: str | None
    status: str           # ok | empty | image_heavy | mixed_low_text | invalid
    n_pages: int
    median_chars_per_page: int
    bytes_per_text_char: float
    table_degraded: bool
    reason: str


def _clean(text: str) -> str:
    """Text-preserving mechanical normalization: collapse ALL whitespace (incl. newlines) to
    single spaces. Never delete a character between word chars (would join wrapped words)."""
    return re.sub(r"\s+", " ", text).strip()


def _read_bytes(source) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    return Path(source).read_bytes()


def extract_pdf_text(source) -> ExtractResult:
    raw = _read_bytes(source)
    size = len(raw)
    if raw[:5] != b"%PDF-":
        return ExtractResult(None, "invalid", 0, 0, 0.0, False, "missing %PDF- header")
    import io
    try:
        reader = PdfReader(io.BytesIO(raw))
        page_texts = [(p.extract_text() or "") for p in reader.pages]
    except (PdfError, Exception) as e:          # pypdf raises various; treat all as invalid
        return ExtractResult(None, "invalid", 0, 0, 0.0, False, f"{type(e).__name__}: {e}")

    n = len(page_texts)
    per_page_chars = [len(t.strip()) for t in page_texts]
    total_chars = sum(per_page_chars)
    median_cpp = int(statistics.median(per_page_chars)) if per_page_chars else 0
    bpc = size / total_chars if total_chars else float("inf")
    near_empty = sum(1 for c in per_page_chars if c < 20)

    if total_chars == 0:
        return ExtractResult(None, "empty", n, 0, bpc, False, "no extractable text")
    if median_cpp < _MIN_CHARS_PER_PAGE and bpc > _MIN_BYTES_PER_TEXT_CHAR:
        return ExtractResult(None, "image_heavy", n, median_cpp, round(bpc, 1), False,
                             "low text + high bytes/char -> likely scanned/screenshots")

    text = _clean("\n".join(page_texts))
    degraded = bool(_MERGED_NUMERIC.search(text)) and len(_MANY_NUMBERS.findall(text)) >= 5
    status = "ok"
    reason = ""
    if n > 1 and near_empty >= 1 and near_empty < n:
        status, reason = "mixed_low_text", f"{near_empty}/{n} pages near-empty (review)"
    return ExtractResult(text, status, n, median_cpp, round(bpc, 1), degraded, reason)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest v2/tests/test_pdf_extract.py -q`
Expected: PASS (5 tests). If `test_dense_numeric_table_flagged_degraded` fails, inspect `extract_pdf_text(FIX/'tuition.pdf').text` and adjust the `_MERGED_NUMERIC`/`_MANY_NUMBERS` thresholds to match the real tuition fixture (the run-together decimals are present — verified during spec validation).

- [ ] **Step 6: Commit**

```bash
git add v2/core/ingestion/pdf_extract.py v2/tests/test_pdf_extract.py v2/tests/fixtures/pdf requirements.txt
git -c commit.gpgsign=false commit -m "feat(pdf): mechanical pdf_extract module (default extract + whitespace cleanup + status codes + degraded-table flag)"
```

---

### Task 2: `retrieve(semantic_mode=...)` + `retrieve_deep` (rescue path)

**Files:**
- Modify: `v2/core/retrieval/retriever.py` (`retrieve` signature + the semantic-leg branch ~481-520; add `retrieve_deep`)
- Modify: `v2/integration/retriever_shim.py` (add `retrieve_deep` passthrough; `top_relevance` getattr fallback, finding #14)
- Test: `v2/tests/test_retrieve_deep.py`

**Interfaces:**
- Consumes: existing `V2Retriever.retrieve(...)`, `_semantic_chunks`, `_semantic`.
- Produces: `V2Retriever.retrieve(..., semantic_mode: str | None = None)` — `None` ⇒ today's behavior (`"chunk" if self.use_chunks else "whole_doc"`); `"chunk"`/`"whole_doc"` force the leg. `V2Retriever.retrieve_deep(query, query_vec=None, org_id=None, item_types=None, limit=5) -> list[RetrievedChunk]` = `retrieve(..., semantic_mode="chunk")`. `V2RetrieverShim.retrieve_deep(query, query_vec=None, item_types=None) -> list[V1Chunk]`.

- [ ] **Step 1: Write the failing tests**

```python
# v2/tests/test_retrieve_deep.py
import struct
from v2.core.database.schema import create_all
from v2.core.retrieval.retriever import V2Retriever

def _v(idx, val=1.0):
    x = [0.0]*768; x[idx]=val; return struct.pack("768f", *x)

def _setup(tmp_path):
    conn = create_all(str(tmp_path/"t.db"))
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES (1,'A','a','office')")
    conn.execute("INSERT INTO knowledge_items(id,org_id,type,content) VALUES (100,1,'policy','deep page'),(200,1,'policy','other')")
    conn.execute("INSERT INTO knowledge_chunk_vectors(chunk_id,embedding,org_id,type,parent_id) VALUES (1,?,1,'policy',100),(2,?,1,'policy',200)",
                 (_v(0,1.0), _v(1,1.0)))
    return conn

def test_retrieve_deep_uses_chunk_leg_even_when_use_chunks_off(tmp_path):
    conn = _setup(tmp_path)
    r = V2Retriever(conn, embedder=object())
    assert r.use_chunks is False                      # default off
    out = r.retrieve_deep("q", query_vec=list(struct.unpack("768f", _v(0,1.0))), limit=5)
    assert out and out[0].item_id == 100              # chunk-KNN found the matching parent

def test_semantic_mode_whole_doc_matches_default(tmp_path):
    conn = _setup(tmp_path)
    # add an item vector so whole_doc has something to find
    conn.execute("INSERT INTO knowledge_vectors(item_id,embedding) VALUES (200,?)", (_v(1,1.0),))
    r = V2Retriever(conn, embedder=object())
    qv = list(struct.unpack("768f", _v(1,1.0)))
    a = r.retrieve("q", query_vec=qv, limit=5)
    b = r.retrieve("q", query_vec=qv, limit=5, semantic_mode="whole_doc")
    assert [c.item_id for c in a] == [c.item_id for c in b]   # None == explicit whole_doc
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest v2/tests/test_retrieve_deep.py -q`
Expected: FAIL (`retrieve_deep` undefined; `semantic_mode` unexpected kwarg).

- [ ] **Step 3: Implement**

In `retriever.py`, add `semantic_mode: str | None = None` to `retrieve(...)` params, and replace the semantic-leg selection (currently `elif self.use_chunks:`):

```python
        mode = semantic_mode or ("chunk" if self.use_chunks else "whole_doc")
        qvec = query_vec if query_vec is not None else self.embedder.embed_query(query)
        if not qvec:
            sem = []
        elif mode == "chunk":
            org_ids = (self._subtree_ids(org_id) if org_subtree else [org_id]) \
                if org_id is not None else None
            chunk_fetch = min(self.pool_size * self.chunk_overfetch, _VEC_KNN_MAX)
            sem = self._semantic_chunks(qvec, chunk_fetch, allowed, org_ids, min_parents=pool)
        else:
            sem = self._semantic(qvec, sem_fetch, allowed)
```

Add the wrapper (after `retrieve`):

```python
    def retrieve_deep(self, query, query_vec=None, org_id=None, org_subtree=True,
                      item_types=None, limit=5):
        """Deep-fallback rescue: force the chunk semantic leg regardless of self.use_chunks.
        Returns full PARENT pages (chunks find, parents serve). Reuses query_vec if given."""
        return self.retrieve(query, org_id=org_id, org_subtree=org_subtree,
                             item_types=item_types, limit=limit, query_vec=query_vec,
                             semantic_mode="chunk")
```

In `retriever_shim.py`, add the passthrough + harden `top_relevance` (finding #14):

```python
    async def retrieve_deep(self, query, query_vec=None, item_types=None):
        async with self._sem:
            return await asyncio.to_thread(self._retrieve_deep_sync, query, item_types, query_vec)

    def _retrieve_deep_sync(self, query, item_types, query_vec):
        conn = get_connection(self.db_path)
        try:
            r = V2Retriever(conn, self.embedder, self.reranker)
            return [self._to_v1(c) for c in r.retrieve_deep(query, query_vec=query_vec,
                                                             org_id=self.org_id, item_types=item_types, limit=5)]
        except Exception:
            logger.exception("V2 deep retrieval failed: %s", query[:80]); return []
        finally:
            conn.close()
```

And in `top_relevance`, after the `metadata.ce_score` check, add a direct-field fallback:

```python
        pre = (getattr(chunks[0], "metadata", None) or {}).get("ce_score")
        if pre is None:
            pre = getattr(chunks[0], "ce_score", None)     # finding #14: v2 RetrievedChunk field
        if pre is not None:
            return pre
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest v2/tests/test_retrieve_deep.py v2/tests/test_chunk_retrieval.py -q`
Expected: PASS (new + existing chunk tests; no regression).

- [ ] **Step 5: Full retrieval regression**

Run: `python3 -m pytest v2/tests/ -q -k "retriev or chunk or rerank"`
Expected: PASS (all retrieval/chunk/rerank tests green).

- [ ] **Step 6: Commit**

```bash
git add v2/core/retrieval/retriever.py v2/integration/retriever_shim.py v2/tests/test_retrieve_deep.py
git -c commit.gpgsign=false commit -m "feat(retrieval): retrieve_deep rescue via retrieve(semantic_mode=chunk) + shim passthrough + top_relevance ce field fallback"
```

---

### Task 3: Config flags + deep-fallback miss-ladder wiring

**Files:**
- Modify: `bot/config.py` (after line 146)
- Modify: `bot/core/message_handler.py` (the primary-miss ladder ~647-672)
- Test: `v2/tests/test_deep_fallback_ladder.py`

**Interfaces:**
- Consumes: `retriever.retrieve_deep`, `retriever.top_relevance`, `botcfg.RETRIEVAL_DEEP_FALLBACK`, `botcfg.DEEP_FALLBACK_THRESHOLD`.
- Produces: a `used_deep: bool` path in `_rag_pipeline`; deep-rescue chunks adopted into `chunks` iff strictly better.

- [ ] **Step 1: Add config flags**

In `bot/config.py` after `OFFICE_THRESHOLD` (line 146):

```python
# Phase-2 deep-fallback chunk-rescue (M2). OFF by default. On a primary miss, consult the
# chunk index and ADOPT the rescued parent pages ONLY if they score strictly better than the
# existing chunks (no-regression contract). Distinct floor from LIVE_THRESHOLD (chunk-CE is
# not calibrated against whole-doc CE) — calibrated on a copy (Task 6).
RETRIEVAL_DEEP_FALLBACK = os.getenv("RETRIEVAL_DEEP_FALLBACK", "").strip().lower() in ("1","true","yes","on")
DEEP_FALLBACK_THRESHOLD = float(os.getenv("DEEP_FALLBACK_THRESHOLD", str(LIVE_THRESHOLD)))
```

- [ ] **Step 2: Write the failing test (branch logic, no Ollama)**

```python
# v2/tests/test_deep_fallback_ladder.py
# Pure unit test of the adopt-if-better decision, extracted as a helper.
from bot.core.message_handler import _deep_adopt   # to be added in Step 4

def test_adopt_when_strictly_better_and_over_threshold():
    assert _deep_adopt(current_rel=0.10, rescue_rel=0.40, threshold=0.15) is True

def test_reject_when_not_better():
    assert _deep_adopt(current_rel=0.50, rescue_rel=0.40, threshold=0.15) is False

def test_reject_when_below_threshold():
    assert _deep_adopt(current_rel=0.05, rescue_rel=0.12, threshold=0.15) is False

def test_adopt_when_current_is_none_but_over_threshold():
    # relevance None => not a miss for the normal path, but if we got here (no chunks) adopt if >=T
    assert _deep_adopt(current_rel=None, rescue_rel=0.40, threshold=0.15) is True

def test_reject_when_rescue_rel_none():
    assert _deep_adopt(current_rel=0.10, rescue_rel=None, threshold=0.15) is False
```

- [ ] **Step 3: Run to verify fail**

Run: `python3 -m pytest v2/tests/test_deep_fallback_ladder.py -q`
Expected: FAIL (`_deep_adopt` undefined).

- [ ] **Step 4: Implement the helper + wire the ladder**

Add the pure helper to `message_handler.py` (module level):

```python
def _deep_adopt(current_rel, rescue_rel, threshold) -> bool:
    """Adopt deep-rescue chunks iff they clear the floor AND beat what's already there.
    current_rel None => no usable primary chunk, so any rescue >= threshold is an improvement."""
    if rescue_rel is None or rescue_rel < threshold:
        return False
    return current_rel is None or rescue_rel > current_rel
```

Wire it into the primary-miss ladder — insert AFTER the office tier block (after the `used_office` assignment, before the live block, ~line 662):

```python
            used_deep = False
            if (primary_miss and not used_office and botcfg.RETRIEVAL_DEEP_FALLBACK
                    and self.retriever):
                rescue = await self.retriever.retrieve_deep(base_q)   # query_vec reuse: see note
                rescue_rel = self.retriever.top_relevance(base_q, rescue) if rescue else None
                if rescue and _deep_adopt(relevance, rescue_rel, botcfg.DEEP_FALLBACK_THRESHOLD):
                    chunks = rescue
                    used_deep = True
                    primary_miss = False        # rescued -> do not fall through to live
```

(Note: `query_vec` reuse — the primary `retrieve()` embeds internally today; threading the vector out is Task 5's perf optimization. For correctness now, `retrieve_deep` re-embeds. Task 5 removes the double embed.)

- [ ] **Step 5: Run to verify pass + flag-off regression**

Run: `python3 -m pytest v2/tests/test_deep_fallback_ladder.py -q`
Expected: PASS (5 tests).
Run: `python3 -m pytest v2/tests/ -q -k "message_handler or rag or handler"`
Expected: PASS (flag defaults OFF ⇒ ladder unchanged).

- [ ] **Step 6: Commit**

```bash
git add bot/config.py bot/core/message_handler.py v2/tests/test_deep_fallback_ladder.py
git -c commit.gpgsign=false commit -m "feat(retrieval): wire deep-fallback rescue into miss-ladder (office->deep->live), adopt-if-better, flag off"
```

---

### Task 4: Perf — thread query_vec + diversity refuse-adopt

**Files:**
- Modify: `bot/core/message_handler.py` (thread the primary query vector into `retrieve_deep`)
- Modify: `v2/core/retrieval/retriever.py` (`retrieve_deep` refuses adoption signal on low parent diversity at the KNN cap, finding #16)
- Modify: `v2/integration/retriever_shim.py` (expose the primary query_vec)
- Test: `v2/tests/test_retrieve_deep.py` (extend)

**Interfaces:**
- Produces: `retrieve_deep(..., min_distinct_parents: int = 3)` — returns `[]` when the vec0 KNN cap was hit AND distinct parents recovered `< min_distinct_parents` (pool dominated by one long doc → don't serve it; fall through to live).

- [ ] **Step 1: Write the failing test**

```python
def test_retrieve_deep_refuses_low_diversity_at_cap(tmp_path, monkeypatch):
    conn = _setup(tmp_path)
    r = V2Retriever(conn, embedder=object())
    # force the cap-hit + single-parent condition via a tiny _VEC_KNN_MAX
    import v2.core.retrieval.retriever as R
    monkeypatch.setattr(R, "_VEC_KNN_MAX", 1)
    out = r.retrieve_deep("q", query_vec=list(struct.unpack("768f", _v(0,1.0))), limit=5)
    assert out == []        # cap hit, <3 distinct parents -> refuse adoption
```

- [ ] **Step 2: Run to verify fail** — `python3 -m pytest v2/tests/test_retrieve_deep.py::test_retrieve_deep_refuses_low_diversity_at_cap -q` → FAIL.

- [ ] **Step 3: Implement** — `retrieve_deep` checks the `_semantic_chunks` result diversity (distinct parents) and whether the fetch hit `_VEC_KNN_MAX`; if cap-hit and `< min_distinct_parents`, return `[]`. (Add a small counter return from `_semantic_chunks` or recompute distinct parents from its output, which already returns one row per parent.) Thread `query_vec` from `message_handler`: capture the primary embed by passing `query_vec` through the shim — in `message_handler` change the deep call to `await self.retriever.retrieve_deep(base_q, query_vec=primary_qvec)` where `primary_qvec` is obtained from the shim (add `retriever.last_query_vec` or compute once and pass to both `retrieve` and `retrieve_deep`). Simplest: in the shim's `retrieve`, embed once and stash on the returned objects' shared metadata; OR expose `embedder.embed_query(base_q)` in message_handler and pass to both. Use the latter (explicit, no hidden state).

- [ ] **Step 4: Run to verify pass** — `python3 -m pytest v2/tests/test_retrieve_deep.py -q` → PASS.

- [ ] **Step 5: Add latency logging** — in `message_handler`, wrap the deep call with timing and `logger.info("deep-fallback: rescued=%s rel=%.3f adopted=%s %.0fms", ...)`.

- [ ] **Step 6: Commit**

```bash
git add bot/core/message_handler.py v2/core/retrieval/retriever.py v2/integration/retriever_shim.py v2/tests/test_retrieve_deep.py
git -c commit.gpgsign=false commit -m "perf(retrieval): reuse query_vec for deep-fallback + refuse adoption on low-diversity KNN-cap + latency log"
```

---

### Task 5: Invalidation completeness — invariant + build-version gating

**Files:**
- Modify: `v2/core/database/vector_gc.py` (extend `assert_no_orphans` → also assert active-item-has-current-descriptor-chunks + dim match + no stale model_id)
- Modify: `v2/core/retrieval/chunk_populate.py` or a new `v2/core/retrieval/chunk_invariant.py` (build-ready check)
- Test: `v2/tests/test_chunk_invariant.py`

**Interfaces:**
- Produces: `assert_chunk_invariant(conn, descriptor)` — raises `AssertionError` unless: every active served item has ≥1 chunk with `model_id == descriptor.id`; every chunk has a vector; every vector's parent is active; no chunk/vector for inactive/missing parent; vector dim == `descriptor.dim`; no chunks with a `model_id != descriptor.id`. `corpus_build_ready(conn, descriptor) -> bool` (the invariant passes without raising).

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_chunk_invariant.py
import pytest, struct
from v2.core.database.schema import create_all
from v2.core.retrieval.model_descriptor import active_descriptor
from v2.core.database.vector_gc import assert_chunk_invariant

D = active_descriptor()
def _v(): return struct.pack(f"{D.dim}f", *([0.0]*D.dim))

def _served_item(conn, iid, model_id=D.id):
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES (1,'A','a','office')")
    conn.execute("INSERT INTO knowledge_items(id,org_id,type,content,is_active) VALUES (?,1,'policy','x',1)", (iid,))
    cur = conn.execute("INSERT INTO knowledge_chunks(parent_id,source_key,ordinal,text,content_hash,model_id) VALUES (?,?,0,'x','h',?)",
                       (iid, f"item:{iid}", model_id))
    conn.execute("INSERT INTO knowledge_chunk_vectors(chunk_id,embedding,org_id,type,parent_id) VALUES (?,?,1,'policy',?)",
                 (cur.lastrowid, _v(), iid))

def test_invariant_passes_for_well_formed(tmp_path):
    conn = create_all(str(tmp_path/"t.db")); _served_item(conn, 100)
    assert_chunk_invariant(conn, D)              # no raise

def test_invariant_fails_active_item_without_chunks(tmp_path):
    conn = create_all(str(tmp_path/"t.db"))
    conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES (1,'A','a','office')")
    conn.execute("INSERT INTO knowledge_items(id,org_id,type,content,is_active) VALUES (100,1,'policy','x',1)")
    with pytest.raises(AssertionError):
        assert_chunk_invariant(conn, D)

def test_invariant_fails_stale_model_id(tmp_path):
    conn = create_all(str(tmp_path/"t.db")); _served_item(conn, 100, model_id="old-model@v0")
    with pytest.raises(AssertionError):
        assert_chunk_invariant(conn, D)
```

- [ ] **Step 2: Run to verify fail** → `assert_chunk_invariant` undefined.

- [ ] **Step 3: Implement `assert_chunk_invariant`** in `vector_gc.py` (reuse existing `assert_no_orphans` for the orphan half; add the active-coverage + model-id + dim checks). Exclude publication/excluded types from "served" the same way the retriever does. `corpus_build_ready` wraps it in try/except → bool.

- [ ] **Step 4: Run to verify pass** → `python3 -m pytest v2/tests/test_chunk_invariant.py v2/tests/test_chunk_vectors.py -q` PASS.

- [ ] **Step 5: Wire build-gating** — in `message_handler`, the deep-fallback branch additionally checks `corpus_build_ready` once at startup (cache it; do NOT run per-query). Add `self._deep_ready` computed in `__init__`/first use.

- [ ] **Step 6: Commit**

```bash
git add v2/core/database/vector_gc.py v2/tests/test_chunk_invariant.py bot/core/message_handler.py
git -c commit.gpgsign=false commit -m "feat(chunks): chunk invariant (coverage+model-id+dim) + build-version gating for deep-fallback"
```

---

### Task 6: PDF ingestion wiring (crawl-lane)

**Files:**
- Modify: `v2/core/ingestion/college_crawl.py` (or the shared crawl ingest helper) — on a linked PDF URL, call `extract_pdf_text`, write a `type='pdf'` `knowledge_items` row with `source_url`, content-hash recrawl, manifest-flag skips.
- Modify: `v2/core/retrieval/retriever.py` — ensure `type='pdf'` is in the served corpus (NOT in `DEFAULT_EXCLUDE_TYPES`); degraded-table rows carry the flag in `metadata`.
- Modify: `bot/services/ollama_client.py` — when a served chunk has `metadata.pdf_table_degraded`, append the "see source link for exact figures" instruction (deterministic, like the existing suffix pattern).
- Test: `v2/tests/test_pdf_ingest.py`

**Interfaces:**
- Consumes: `extract_pdf_text`.
- Produces: PDF rows as `knowledge_items(type='pdf', source_url=<pdf url>, metadata.pdf_table_degraded=<bool>)`; skip rows recorded in the crawl manifest.

- [ ] **Step 1: Write the failing test** — feed a fake crawl a fixture PDF URL (monkeypatch the fetch to return the fixture bytes), assert a `type='pdf'` row is created with the source_url and the degraded flag for the tuition fixture; assert an image-heavy/invalid PDF creates NO row but a manifest entry.
- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** the PDF branch in the crawl ingest path (mechanical: fetch → `extract_pdf_text` → on `ok`/`mixed_low_text` insert row; else manifest-flag). Content-hash on the cleaned text for recrawl diff.
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Verify served-corpus inclusion** — `python3 -m pytest v2/tests/test_pdf_ingest.py v2/tests/ -q -k "pdf or retriev"` PASS.
- [ ] **Step 6: Commit**

```bash
git add v2/core/ingestion/college_crawl.py v2/core/retrieval/retriever.py bot/services/ollama_client.py v2/tests/test_pdf_ingest.py
git -c commit.gpgsign=false commit -m "feat(pdf): ingest pdf text as type=pdf rows (mechanical, content-hash recrawl, degraded-table serving safeguard)"
```

---

### Task 7: Calibration + full-pipeline eval (the binding gate) — analysis task

**Files:**
- Create: `scratchpad/phase2_calibrate.py` (NOT committed — analysis), output notes → the spec / memory.
- Modify: `eval/questions.txt` (add deep-content + PDF-sourced + a KG/structured regression question).

**Steps (commands, run on a COPY — never live):**

- [ ] **Step 1: Make a dev copy + build chunks**

```bash
cp /home/md724/gsa-gateway/gsa_gateway.db /tmp/p2.db
python3 v2/scripts/embed_chunks.py --db /tmp/p2.db        # ~1 min; builds chunks on the copy
python3 -c "from v2.core.database.schema import get_connection; from v2.core.database.vector_gc import assert_chunk_invariant; from v2.core.retrieval.model_descriptor import active_descriptor as D; assert_chunk_invariant(get_connection('/tmp/p2.db'), D()); print('invariant OK')"
```

- [ ] **Step 2: Build positive deep + hard-negative sets** — reuse the paraphrased deep-content set from `project_chunking_resume` (scratchpad `deep_paraphrase_eval.py` pattern); hard negatives = common-eval questions the normal path already answers.

- [ ] **Step 3: Threshold sweep** — for `DEEP_FALLBACK_THRESHOLD ∈ {0.05,0.10,0.15,0.20,0.30}` measure: deep-set recall@5, adoption-rate on the common set (should be ~0), false-adoption (a common-eval question whose answer changed). **Select by lowest false-adoption with deep recall up.** Freeze the value; record it in memory + `bot/config.py` default.

- [ ] **Step 4: Full-pipeline eval.sh A/B (BINDING reject #1)** — per-question, deep OFF vs ON, on the copy, live off:

```bash
DATABASE_PATH=/tmp/p2.db V2_RETRIEVER_ENABLED=true LIVE_ENABLED=0 OLLAMA_ENABLED=true RETRIEVAL_DEEP_FALLBACK=0 bash scripts/eval.sh 2>&1 | tee /tmp/p2_off.log
DATABASE_PATH=/tmp/p2.db V2_RETRIEVER_ENABLED=true LIVE_ENABLED=0 OLLAMA_ENABLED=true RETRIEVAL_DEEP_FALLBACK=1 DEEP_FALLBACK_THRESHOLD=<frozen> bash scripts/eval.sh 2>&1 | tee /tmp/p2_on.log
```
**GATE:** diff per-question — NO previously-correct answer may become incorrect (reject #1). Aggregate ≥84 within judge σ secondary. If any regress → tune threshold / adopt-if-better, do NOT proceed.

- [ ] **Step 5: PDF-ingestion-impact eval (P2-G9)** — ingest a small PDF batch into a second copy, eval deep OFF (proves ingestion alone didn't pollute), then ON.

- [ ] **Step 6: Lock regressions in** — add the deep-content Qs + a PDF-sourced Q + a KG/structured regression Q to `eval/questions.txt`; commit:

```bash
git add eval/questions.txt
git -c commit.gpgsign=false commit -m "test(eval): add deep-content + pdf + kg-regression questions for Phase 2"
```

---

### Task 8: Live re-chunk/embed (OWNER CHECKPOINT — gated production write)

**This task writes the LIVE DB and is NOT executed without explicit owner approval at the checkpoint.** It is staged here for completeness; the recommended path is folding it into the planned DB wipe+rebuild (free re-embed).

**Frozen value:** `DEEP_FALLBACK_THRESHOLD=0.30` (Task 7 calibration).

**MUST-FIX BEFORE FLIPPING THE FLAG (from the final whole-branch review — these are NOT shipped in the
flag-off branch; all serving-safe while the flag is OFF, but required before `RETRIEVAL_DEEP_FALLBACK=1`):**
- [ ] **P1 (review Important-2):** wire `vector_gc.corpus_build_ready(conn, descriptor)` into the runtime
      miss-ladder — compute once at startup, gate deep-eligibility on it, so a half-built chunk index can't
      be served the instant the flag is set. (TDD; own gate.)
- [ ] **P2 (review Important-1):** wire chunk invalidation into `reconcile` (call the `vector_gc` chunk
      sweeps) **OR** document + enforce a mandatory post-crawl/post-reconcile `embed_chunks` step; resolve
      the dead `sweep_orphan_chunks`. (Closes spec §6.2; today chunk rows accumulate as cruft.)
- [ ] **P3 (review Minor-5):** confirm no active served row with empty/whitespace content can stall the
      invariant (it would hold `corpus_build_ready` False forever) — scope coverage to non-empty content
      or guarantee no empty served rows.
- [ ] **P4 (review Minor-8, recommended):** add one end-to-end ingest→embed→recrawl→sweep→invariant test
      (locks reject #5).

**Execution steps:**
- [ ] **Step 1:** `hardened_backup` the live DB (un-skippable).
- [ ] **Step 2:** `python3 v2/scripts/embed_chunks.py --db gsa_gateway.db --commit` (after dev-copy dry-run proven). *(Now runs the FULL `assert_chunk_invariant` at the end — truthful self-check.)*
- [ ] **Step 3:** `assert_chunk_invariant` on the live DB → evidence-before-claim (print counts).
- [ ] **Step 3b (folds in Step 5 / P2-G9):** ingest the real live-crawl PDF batch, then run the PDFs-added deep-OFF then deep-ON eval A/B (owner chose to run G9 here against the real batch, not a copy).
- [ ] **Step 4:** Owner enables `RETRIEVAL_DEEP_FALLBACK=1` + `DEEP_FALLBACK_THRESHOLD=0.30` and restarts (`bash scripts/restart.sh`). DB-only changes need no restart, but the flag is read at startup.

---

## Self-Review

**Spec coverage:** A (deep-fallback) → Tasks 2,3,4,7; B (PDF) → Tasks 1,6; C (re-embed+invalidation) → Tasks 5,7,8. Goals P2-G1..G9 each map to a task (G1→T2, G2→T3, G3→T7, G4→T1, G5→T6, G6→T7/T8, G7→T5, G8 deferred, G9→T7). Reject criteria #1 (per-question)→T7 step 4; #3 (PDF-ingest)→T7 step 5; #4 (PDF faithful)→T1; #5 (invariant)→T5. No gaps.

**Placeholder scan:** Tasks 4 & 6 step-3 describe approach with the interface pinned but defer some inline code to implementation (the diversity-counter return shape and the crawl-ingest seam depend on reading the exact current `_semantic_chunks` return + crawl ingest function at execution time) — flagged for the implementing subagent to read those files first; all signatures/return types are pinned in the Interfaces blocks.

**Type consistency:** `extract_pdf_text → ExtractResult` (T1) consumed in T6; `retrieve_deep(query, query_vec=...)` (T2) consumed in T3/T4; `_deep_adopt(current_rel, rescue_rel, threshold)` (T3) consistent; `assert_chunk_invariant(conn, descriptor)` (T5) consumed in T7. Consistent.
