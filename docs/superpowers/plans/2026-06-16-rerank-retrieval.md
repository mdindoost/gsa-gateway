# Cross-Encoder Reranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an ONNX cross-encoder reranker over the v2 retriever's fused candidate pool so the chunk containing the asked fact rises to the top, fixing the "right doc, wrong chunk" misses found by the 100-question eval.

**Architecture:** A new `CrossEncoderReranker` (onnxruntime + `tokenizers`, no torch) scores each `(query, chunk)` pair. `V2Retriever.retrieve()` reorders its fused pool by `sigmoid(CE) × type_boost` before `_diversify_and_expand`. Strictly additive: any failure falls back to the current RRF order. Gated by settings; model auto-downloads once to `models/reranker/`.

**Tech Stack:** Python 3.11, onnxruntime, tokenizers, huggingface_hub, numpy, SQLite, pytest. Model: `Xenova/ms-marco-MiniLM-L-6-v2`.

**Design spec:** `docs/superpowers/specs/2026-06-16-rerank-retrieval-design.md` (read it; senior-review items C1/C2/S1-S4/N1-N5 are folded into the tasks below).

**Branch:** `feat/rerank-retrieval` (already created).

---

## File Structure

- Create `v2/core/retrieval/reranker.py` — `CrossEncoderReranker` (load, score, fallback).
- Create `v2/tests/test_reranker.py` — unit tests (fast fallback + slow real-model ordering).
- Create `v2/tests/rerank_gold.py` — frozen {question → fact-substring} maps (gold + guard).
- Create `v2/tests/test_rerank_gold_chunks.py` — **the deterministic acceptance gate**.
- Create `scripts/_rerank_recall_diagnostic.py` — pre-build recall-vs-ranking diagnostic (S2).
- Create `scripts/fetch_reranker.py` — explicit one-time model pre-warm.
- Modify `v2/core/retrieval/retriever.py` — `reranker` arg, settings, `_load_bool`, `_rerank` seam.
- Modify `v2/integration/retriever_shim.py` — hold + pass a shared reranker.
- Modify `bot/core/assistant.py` — build the shared reranker, `warm()` at startup, pass to shim.
- Modify `.gitignore` — add `models/reranker/`.

---

## Task 1: Gold-fact maps + recall diagnostic (informs S2 before building)

**Files:**
- Create: `v2/tests/rerank_gold.py`
- Create: `scripts/_rerank_recall_diagnostic.py`

- [ ] **Step 1: Write the frozen gold/guard maps**

Gold facts are matched by a **stable content substring** (not an id, which shifts on re-ingest). `GOLD` = the ~11 wrong-chunk misses; `GUARD` = already-correct questions that must not regress. (Q3 "six positions" is deliberately excluded — it's a structured-skill case, out of scope for reranking.)

Create `v2/tests/rerank_gold.py`:

```python
"""Frozen question -> gold fact substring maps for the rerank acceptance gate.

A question 'passes' when a retrieved chunk's content CONTAINS the substring. Substrings
are id-stable (survive re-ingest). GOLD = the wrong-chunk misses the reranker must fix;
GUARD = already-correct questions that must not regress.
"""

GOLD = {
    "Who chairs the GSA General Assembly meetings?": "Chair the General Assembly meetings",
    "What is the minimum GPA to run for a GSA Executive Board position?": "minimum 3.00 GPA",
    "How many terms can someone serve in one GSA officer position?": "more than two terms",
    "Who are the GSA's two advisors and which offices are they from?":
        "Academic Advisor shall be a member of the Office of Graduate Studies",
    "What is the per-person food cost limit for a club event of 25 students?":
        "$9 per person for an event of 0 to 30 students",
    "How many events must a graduate club hold on campus per semester?":
        "at least 2 events on-campus per semester",
    "How much can a graduate club receive from a conference/competition grant?":
        "Organizations can receive up to a $500 grant",
    "How many days after travel must I submit the Chrome River Expense Report?":
        "within 30 days of travel",
    "Are AirBNB stays reimbursable under the GSA travel award?":
        "AirBNBs, VRBOs, or other vacation rentals are not eligible",
    "How is the GSA Vice President for Academic Affairs selected?":
        "nominate and appoint the Vice President for Academic Affairs",
    "Who can impeach a GSA officer and what vote is needed?":
        "two-thirds majority vote of all the department representatives present",
}

GUARD = {
    "What is the maximum GSA travel award per fiscal year?": "maximum of $900",
    "How much is an asset grant for a graduate club?": "up to a $150 grant",
    "What percentage of a club's budget can be spent on prizes?":
        "15% of their whole budget on prizes",
    "Can a club use petty cash reimbursement?": "Petty cash reimbursement will NOT",
    "What happens to a club on its 2nd financial bylaw offense?":
        "10% off their original budget",
    "What is the IRS mileage rate used for GSA travel reimbursement?": "$0.70 per mile",
    "What cumulative GPA must a CS PhD student maintain?": "at least 3.5",
    "Who sits on the CS PhD Qualifying Exam Committee?": "three tenure-track NJIT faculty",
    "Where is the GSA office located and what are its hours?": "Campus Center 110A",
    "When does the GSA fiscal year run?": "July 1st through June 30th",
}
```

- [ ] **Step 2: Verify every gold/guard substring actually exists in the active KB**

Create and run a quick check (this catches typos before they cause confusing test failures):

```bash
cd /home/md724/gsa-gateway
.venv/bin/python - <<'EOF'
from v2.core.database.schema import get_connection
from v2.tests.rerank_gold import GOLD, GUARD
c = get_connection("gsa_gateway.db")
missing = []
for q, sub in {**GOLD, **GUARD}.items():
    n = c.execute("SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND lower(content) LIKE ?",
                  (f"%{sub.lower()}%",)).fetchone()[0]
    if n == 0:
        missing.append((q, sub))
    print(("OK " if n else "MISSING "), n, "::", sub[:45])
assert not missing, f"fix these substrings (not found in KB): {missing}"
print("ALL SUBSTRINGS PRESENT")
EOF
```

Expected: `ALL SUBSTRINGS PRESENT`. If any MISSING, fix the substring in `rerank_gold.py` to match the real chunk text (inspect with a `LIKE` query), then re-run.

- [ ] **Step 3: Write the recall diagnostic**

Create `scripts/_rerank_recall_diagnostic.py`:

```python
"""Pre-build diagnostic (senior review S2): for each GOLD question, find the fused rank of
the chunk that contains the gold fact, WITHOUT reranking. Tells us whether each miss is a
ranking failure (gold in pool, rank>1 -> reranker fixes it) or a recall failure (gold
outside pool_size -> we must widen recall, not just rerank)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v2.core.database.schema import get_connection
from v2.core.retrieval.retriever import V2Retriever
from v2.core.retrieval.embedder import Embedder
from v2.tests.rerank_gold import GOLD


def fused_rank(retr, conn, query, substr):
    # Reproduce retrieve()'s pool with a large limit so we see deep ranks.
    chunks = retr.retrieve(query, limit=50, group_by_entity=False)
    for i, ch in enumerate(chunks, start=1):
        if substr.lower() in (ch.content or "").lower():
            return i, len(chunks)
    return None, len(chunks)


def main():
    conn = get_connection("gsa_gateway.db")
    retr = V2Retriever(conn, Embedder())
    print(f"pool_size={retr.pool_size}")
    for q, sub in GOLD.items():
        rank, n = fused_rank(retr, conn, q, sub)
        verdict = "RECALL-MISS (widen pool)" if rank is None else (
            "rank-1 already" if rank == 1 else f"RANKING-MISS rank={rank}")
        print(f"  {verdict:<28} pool={n:<3} | {q[:55]}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the diagnostic and record the finding**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python scripts/_rerank_recall_diagnostic.py 2>&1 | grep -v "httpx\|HTTP Request\|INFO\|WARNING"`

Expected: a line per gold question. **Decision rule:** if every gold chunk shows `RANKING-MISS` (rank>1, in pool) → reranking alone suffices, keep `rerank_pool = pool_size`. If any show `RECALL-MISS` → in Task 3, raise the `pool_size` default (e.g. 40→80) until that gold chunk enters the pool, and note it in the spec. Record the outcome as a comment at the top of the diagnostic file.

- [ ] **Step 5: Commit**

```bash
cd /home/md724/gsa-gateway
git add v2/tests/rerank_gold.py scripts/_rerank_recall_diagnostic.py
git commit -m "test(rerank): gold-fact maps + recall-vs-ranking diagnostic"
```

---

## Task 2: CrossEncoderReranker (load, score, fallback)

**Files:**
- Create: `v2/core/retrieval/reranker.py`
- Test: `v2/tests/test_reranker.py`

- [ ] **Step 1: Write the failing fallback test**

Create `v2/tests/test_reranker.py`:

```python
from pathlib import Path
from v2.core.retrieval.reranker import CrossEncoderReranker


def test_score_returns_none_when_model_absent(tmp_path, monkeypatch):
    # Empty model dir + block any download -> score() must return None, never raise.
    r = CrossEncoderReranker(model_dir=tmp_path / "nope")

    def _boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr(r, "_download", _boom)
    assert r.score("q", ["a", "b"]) is None
    assert r.available is False


def test_score_empty_passages_is_empty_list(tmp_path):
    r = CrossEncoderReranker(model_dir=tmp_path / "nope")
    assert r.score("q", []) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest v2/tests/test_reranker.py -q`
Expected: FAIL (ModuleNotFoundError: no `reranker` module).

- [ ] **Step 3: Implement `reranker.py`**

Create `v2/core/retrieval/reranker.py`:

```python
"""Cross-encoder reranker (ONNX) over the fused retrieval pool.

Reorders candidate chunks by joint (query, passage) relevance — fixes the "right doc,
wrong chunk" failures pure RRF/semantic top-K produces. Uses onnxruntime + the `tokenizers`
library only (no torch/transformers). The model auto-downloads once to models/reranker/ and
is cached. Any failure (model missing offline, onnx error) makes score() return None, so the
caller keeps the existing RRF order — reranking is strictly additive.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_REPO_ID = "Xenova/ms-marco-MiniLM-L-6-v2"
_MODEL_DIR = Path(__file__).resolve().parents[3] / "models" / "reranker"
_MAX_LEN = 512


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


class CrossEncoderReranker:
    def __init__(self, model_dir: Path = _MODEL_DIR, repo_id: str = _REPO_ID,
                 max_len: int = _MAX_LEN):
        self.model_dir = Path(model_dir)
        self.repo_id = repo_id
        self.max_len = max_len
        self._lock = threading.Lock()
        self._session = None
        self._tokenizer = None
        self._input_names: set[str] = set()
        self.available = True  # flips False after a hard load failure

    def warm(self) -> bool:
        try:
            self._ensure_loaded()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("reranker warm failed (continuing without rerank): %s", exc)
            return False

    def _model_path(self) -> Path:
        # Xenova repos place the model under onnx/; accept either layout.
        for p in (self.model_dir / "model.onnx", self.model_dir / "onnx" / "model.onnx"):
            if p.exists():
                return p
        return self.model_dir / "onnx" / "model.onnx"

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return
        with self._lock:
            if self._session is not None:
                return
            import onnxruntime as ort
            from tokenizers import Tokenizer

            tok_path = self.model_dir / "tokenizer.json"
            if not self._model_path().exists() or not tok_path.exists():
                self._download()
            tok = Tokenizer.from_file(str(tok_path))
            tok.enable_truncation(max_length=self.max_len)  # passage-side; query is short
            tok.enable_padding()
            so = ort.SessionOptions()
            so.intra_op_num_threads = 2  # shared box also runs Ollama (N1)
            sess = ort.InferenceSession(str(self._model_path()), sess_options=so,
                                        providers=["CPUExecutionProvider"])
            self._input_names = {i.name for i in sess.get_inputs()}
            self._tokenizer = tok
            self._session = sess
            logger.info("reranker loaded (%s); inputs=%s", self.repo_id, sorted(self._input_names))

    def _download(self) -> None:
        from huggingface_hub import snapshot_download
        self.model_dir.mkdir(parents=True, exist_ok=True)
        snapshot_download(repo_id=self.repo_id, local_dir=str(self.model_dir),
                          allow_patterns=["onnx/model.onnx", "tokenizer.json",
                                          "*.json", "vocab.txt"])

    def score(self, query: str, passages: list[str]) -> list[float] | None:
        if not passages:
            return []
        try:
            self._ensure_loaded()
        except Exception as exc:  # noqa: BLE001
            logger.warning("reranker unavailable, falling back to RRF: %s", exc)
            self.available = False
            return None
        try:
            encs = [self._tokenizer.encode(query, p) for p in passages]
            ids = np.array([e.ids for e in encs], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._input_names:
                feed["token_type_ids"] = np.array([e.type_ids for e in encs], dtype=np.int64)
            out = np.asarray(self._session.run(None, feed)[0])
            logits = out[:, 1] if (out.ndim == 2 and out.shape[1] == 2) \
                else out.reshape(out.shape[0], -1)[:, 0]
            return [float(s) for s in _sigmoid(logits)]
        except Exception as exc:  # noqa: BLE001
            logger.warning("reranker scoring failed, falling back to RRF: %s", exc)
            return None
```

- [ ] **Step 4: Run the fallback tests to verify they pass**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest v2/tests/test_reranker.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Add the slow real-model ordering test**

Append to `v2/tests/test_reranker.py`:

```python
import pytest


@pytest.mark.slow
def test_orders_relevant_passage_first():
    """Downloads the real model once (network on first run). The relevant passage must
    outscore the off-topic one — guards real ms-marco output shape, not a mock."""
    r = CrossEncoderReranker()
    q = "Who chairs the GSA General Assembly meetings?"
    relevant = "Chair the General Assembly meetings and coordinate with Department Representatives."
    off = "Bi-weekly General Assembly Meetings begin no later than the third full week of classes."
    scores = r.score(q, [off, relevant])
    assert scores is not None
    assert scores[1] > scores[0]
```

- [ ] **Step 6: Run the slow test (downloads ~90MB once)**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest v2/tests/test_reranker.py -q -m slow`
Expected: PASS. If it errors on inputs/shape, inspect with the snippet in spec §S4 (`session.get_inputs()`) and adjust `score()` — the dynamic input/shape handling should already cover it.

- [ ] **Step 7: Register the `slow` marker + commit**

Add to `pyproject.toml`/`pytest.ini` markers if a markers section exists (else skip). Then:

```bash
cd /home/md724/gsa-gateway
git add v2/core/retrieval/reranker.py v2/tests/test_reranker.py
git commit -m "feat(rerank): CrossEncoderReranker (ONNX cross-encoder, RRF fallback)"
```

---

## Task 3: Integrate the reranker into V2Retriever

**Files:**
- Modify: `v2/core/retrieval/retriever.py` (`__init__` ~line 119, after `ranked = sorted(...)` ~line 296)
- Test: `v2/tests/test_reranker_integration.py`

- [ ] **Step 1: Write the failing integration test (stub reranker, no model needed)**

Create `v2/tests/test_reranker_integration.py`:

```python
from v2.core.database.schema import get_connection
from v2.core.retrieval.retriever import V2Retriever
from v2.core.retrieval.embedder import Embedder


class _StubReranker:
    """Scores by whether a target substring is present — deterministic, no model."""
    available = True

    def __init__(self, target):
        self.target = target.lower()

    def score(self, query, passages):
        return [1.0 if self.target in (p or "").lower() else 0.0 for p in passages]


def test_rerank_lifts_target_chunk_to_top():
    conn = get_connection("gsa_gateway.db")
    q = "Who chairs the GSA General Assembly meetings?"
    target = "Chair the General Assembly meetings"

    base = V2Retriever(conn, Embedder())  # no reranker
    base_top = (base.retrieve(q, limit=1) or [None])[0]

    rr = V2Retriever(conn, Embedder(), reranker=_StubReranker(target))
    rr_top = rr.retrieve(q, limit=1)[0]
    assert target.lower() in (rr_top.content or "").lower()


def test_reranker_none_is_unchanged_behaviour():
    conn = get_connection("gsa_gateway.db")
    a = V2Retriever(conn, Embedder())
    b = V2Retriever(conn, Embedder(), reranker=None)
    q = "What is the maximum GSA travel award per fiscal year?"
    assert [c.item_id for c in a.retrieve(q, limit=5)] == [c.item_id for c in b.retrieve(q, limit=5)]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest v2/tests/test_reranker_integration.py -q`
Expected: FAIL (`V2Retriever.__init__() got an unexpected keyword argument 'reranker'`).

- [ ] **Step 3: Add `reranker` arg, settings, and `_load_bool` to `__init__`**

In `v2/core/retrieval/retriever.py`, change the signature and add settings. Replace:

```python
    def __init__(self, conn, embedder):
        self.conn = conn
        self.embedder = embedder
```

with:

```python
    def __init__(self, conn, embedder, reranker=None):
        self.conn = conn
        self.embedder = embedder
        self.reranker = reranker
```

Then after the existing `self.exclude_types = ...` line, add:

```python
        # Cross-encoder rerank of the fused pool (admin-tunable; instant kill-switch).
        self.rerank_enabled = self._load_bool("retriever.rerank_enabled", True)
        # Rerank the FULL fused pool by default (senior review S2), never below pool_size.
        self.rerank_pool = max(self.pool_size,
                               int(self._load_boost("retriever.rerank_pool", self.pool_size)))
```

And add this method next to `_load_boost`:

```python
    def _load_bool(self, key: str, default: bool) -> bool:
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key=? ORDER BY org_id LIMIT 1", (key,)
        ).fetchone()
        if not row or row["value"] is None:
            return default
        return str(row["value"]).strip().lower() in ("1", "true", "yes", "on")
```

- [ ] **Step 4: Add the `_rerank` seam method and call it**

Add this method to `V2Retriever` (e.g. right after `_boost_for`):

```python
    def _rerank(self, query, ranked, rows):
        """Reorder the fused pool by sigmoid(CE) x type_boost (senior review C2: keep the
        boost as a multiplicative prior so event_info items don't get demoted). Returns
        `ranked` unchanged on any miss — reranking is strictly additive."""
        if not self.rerank_enabled or self.reranker is None or len(ranked) < 2:
            return ranked
        window = ranked[: self.rerank_pool]
        passages = [rows[iid]["content"] or "" for iid, _ in window]
        ce = self.reranker.score(query, passages)
        if ce is None:
            return ranked
        rescored = [(iid, s * self._boost_for(rows[iid]["type"]))
                    for (iid, _old), s in zip(window, ce)]
        rescored.sort(key=lambda kv: -kv[1])
        return rescored + ranked[self.rerank_pool:]
```

Then find the line `ranked = sorted(scores.items(), key=lambda kv: -kv[1])` and insert immediately after it:

```python
        ranked = self._rerank(query, ranked, rows)
```

- [ ] **Step 5: Run the integration tests to verify they pass**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest v2/tests/test_reranker_integration.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Run the full retrieval test suite (no regressions)**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest v2/tests/ -q -m "not slow"`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
cd /home/md724/gsa-gateway
git add v2/core/retrieval/retriever.py v2/tests/test_reranker_integration.py
git commit -m "feat(rerank): wire reranker seam into V2Retriever (boost-preserving, gated)"
```

---

## Task 4: The deterministic acceptance gate (gold-chunk gate, real model)

**Files:**
- Test: `v2/tests/test_rerank_gold_chunks.py`

- [ ] **Step 1: Write the gate test**

Create `v2/tests/test_rerank_gold_chunks.py`:

```python
"""Acceptance gate (senior review C1): deterministic, chunk-level, no LLM. With the real
reranker ON, every GOLD fact must surface in the top-`limit` retrieved chunks, and no GUARD
fact may regress. Marked slow (downloads the model once)."""
import pytest

from v2.core.database.schema import get_connection
from v2.core.retrieval.retriever import V2Retriever
from v2.core.retrieval.embedder import Embedder
from v2.core.retrieval.reranker import CrossEncoderReranker
from v2.tests.rerank_gold import GOLD, GUARD

LIMIT = 5


def _hits(retr, query, substr):
    return any(substr.lower() in (c.content or "").lower()
               for c in retr.retrieve(query, limit=LIMIT))


@pytest.fixture(scope="module")
def reranked():
    conn = get_connection("gsa_gateway.db")
    return V2Retriever(conn, Embedder(), reranker=CrossEncoderReranker())


@pytest.mark.slow
@pytest.mark.parametrize("q,sub", list(GOLD.items()))
def test_gold_fact_in_top_k_with_rerank(reranked, q, sub):
    assert _hits(reranked, q, sub), f"GOLD miss after rerank: {q!r} (want {sub!r})"


@pytest.mark.slow
@pytest.mark.parametrize("q,sub", list(GUARD.items()))
def test_guard_fact_not_regressed(reranked, q, sub):
    assert _hits(reranked, q, sub), f"GUARD regressed after rerank: {q!r} (want {sub!r})"
```

- [ ] **Step 2: Run the gate**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest v2/tests/test_rerank_gold_chunks.py -q -m slow`
Expected: all GOLD + GUARD pass. **If a GOLD case fails:** check the Task 1 diagnostic — if it was a RECALL-MISS, raise the `retriever.pool_size` default in `retriever.py` (e.g. `DEFAULT_POOL_SIZE` 40→80) and re-run; if RANKING-MISS, confirm `rerank_pool` covers it. Tune `pool_size`/`rerank_pool` until the gate is green. Do not proceed until green.

- [ ] **Step 3: Commit**

```bash
cd /home/md724/gsa-gateway
git add v2/tests/test_rerank_gold_chunks.py v2/core/retrieval/retriever.py
git commit -m "test(rerank): deterministic gold-chunk acceptance gate (GOLD fixed, GUARD held)"
```

---

## Task 5: Wire into the bot (shared singleton) + provisioning

**Files:**
- Modify: `v2/integration/retriever_shim.py` (`__init__` line 46; `_retrieve_sync` line 73)
- Modify: `bot/core/assistant.py` (where the shim/retriever is built)
- Modify: `.gitignore`
- Create: `scripts/fetch_reranker.py`

- [ ] **Step 1: Gitignore the model dir**

Append to `.gitignore`:

```
# Reranker model (auto-downloaded once, ~90MB)
models/reranker/
```

- [ ] **Step 2: Shim holds + passes a shared reranker**

In `v2/integration/retriever_shim.py`, change `V2RetrieverShim.__init__` to accept and store `reranker=None`:

```python
    def __init__(self, db_path: str, embedder, org_id: int | None = None,
                 max_concurrency: int = 4, reranker=None):
        self.db_path = db_path
        self.embedder = embedder
        self.org_id = org_id
        self.reranker = reranker
        self._sem = asyncio.Semaphore(max_concurrency)
```

And in `_retrieve_sync`, change the construction to pass the shared reranker (do NOT build a new one per call — N2):

```python
            retriever = V2Retriever(conn, self.embedder, self.reranker)
```

- [ ] **Step 3: Build the shared reranker in the assistant and warm it**

In `bot/core/assistant.py`, where the embedder/shim are created, build one reranker, `warm()` it (non-blocking, non-fatal), and pass it to the shim. Locate the shim construction and add before it:

```python
    from v2.core.retrieval.reranker import CrossEncoderReranker
    reranker = CrossEncoderReranker()
    try:
        reranker.warm()  # one-time load/download; failure is non-fatal (falls back to RRF)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).warning("reranker warm failed; using RRF order")
```

Then add `reranker=reranker` to the `V2RetrieverShim(...)` constructor call.

- [ ] **Step 4: Write the explicit fetch script**

Create `scripts/fetch_reranker.py`:

```python
#!/usr/bin/env python
"""Pre-warm the reranker model (one-time download to models/reranker/). Optional — the bot
auto-downloads on first use; run this to provision ahead of time."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v2.core.retrieval.reranker import CrossEncoderReranker

if __name__ == "__main__":
    ok = CrossEncoderReranker().warm()
    print("reranker ready" if ok else "reranker warm FAILED")
    sys.exit(0 if ok else 1)
```

- [ ] **Step 5: Verify the bot wiring imports cleanly + reranker provisions**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python scripts/fetch_reranker.py`
Expected: `reranker ready`.

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -c "import bot.core.assistant"`
Expected: no import error.

- [ ] **Step 6: Commit**

```bash
cd /home/md724/gsa-gateway
git add v2/integration/retriever_shim.py bot/core/assistant.py scripts/fetch_reranker.py .gitignore
git commit -m "feat(rerank): wire shared reranker into bot path + fetch script + gitignore model"
```

---

## Task 6: Secondary smoke — re-run the 100-Q eval

**Files:** none (uses `scripts/_eval_kb_100.py`)

- [ ] **Step 1: Re-run the end-to-end eval**

Run: `cd /home/md724/gsa-gateway && rm -f eval_results.jsonl && .venv/bin/python scripts/_eval_kb_100.py --limit 100 > eval_run.log 2>&1 &` then wait for completion (`until [ "$(wc -l < eval_results.jsonl)" -ge 100 ] || ! pgrep -f _eval_kb_100 >/dev/null; do sleep 5; done`).

- [ ] **Step 2: Read the answers for the GOLD questions and confirm improvement**

Inspect the answers to the 11 GOLD questions in `eval_results.jsonl`. Expected: the previously-wrong ones (who chairs GA, AirBNB, conference grant $500, term limits, advisors, 30-day deadline, GPA-to-run, impeachment, VPAA selection, food cost, events/semester) now answer correctly. Spot-check that no GUARD answer broke. This is a human read, not a hard gate (the gold-chunk test in Task 4 is the gate).

- [ ] **Step 3: Clean up the eval's logged test questions**

```bash
cd /home/md724/gsa-gateway
.venv/bin/python - <<'EOF'
from pathlib import Path; import sys; sys.path.insert(0, str(Path('.').resolve()))
from scripts._area_tag_migrate import hardened_backup
from v2.core.database.schema import get_connection
c = get_connection("gsa_gateway.db")
wm = c.execute("SELECT COALESCE(MAX(id),0) FROM questions").fetchone()[0]
print("note watermark before any future runs:", wm)
EOF
```

(The harness already deletes only what it logged via its own watermark; this step just records state. No commit — `eval_results.jsonl`/`eval_run.log` stay gitignored/untracked.)

---

## Task 7: Finalize

- [ ] **Step 1: Full test sweep (fast + slow)**

Run: `cd /home/md724/gsa-gateway && .venv/bin/python -m pytest v2/tests/ bot/tests/ -q` then the slow gate: `... -m slow -q`.
Expected: all green.

- [ ] **Step 2: Update the spec status + memory**

In `docs/superpowers/specs/2026-06-16-rerank-retrieval-design.md`, change Status to `Implemented`. Append the measured before/after (gold-gate pass count, eval delta) to the spec.

- [ ] **Step 3: Commit**

```bash
cd /home/md724/gsa-gateway
git add docs/superpowers/specs/2026-06-16-rerank-retrieval-design.md
git commit -m "docs(rerank): mark spec implemented + record measured results"
```

- [ ] **Step 4: Report to the user** the gold-gate result (X/11 GOLD fixed, GUARD held), the eval delta, and offer to merge `feat/rerank-retrieval` → `main` (and push/restart) per their call.
