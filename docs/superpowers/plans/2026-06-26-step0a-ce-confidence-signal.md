# Step 0a — Calibrated CE Confidence Signal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the per-item cross-encoder relevance score (computed on the MATCHED CHUNK) as a first-class, reusable confidence signal on retrieval results — without a second CE pass — so later tiers can gate on it.

**Architecture:** `_rerank` already computes raw CE scores over the matched-chunk passages but discards them (keeps only ranks). Capture the raw scores, thread them onto `RetrievedChunk.ce_score`, carry them through the shim's `V1Chunk.metadata`, and let `top_relevance` read the existing score instead of re-running the cross-encoder on the (CE-truncated) full document.

**Tech Stack:** Python 3.11, sqlite-vec, `CrossEncoderReranker` (sentence-transformers), pytest.

## Global Constraints

- **LLM-agnostic (HARD LINE):** no model-specific constants baked in; the CE score is whatever the configured reranker emits. One line.
- **Additive / no-regression:** reranking is "strictly additive — returns `ranked` unchanged on any miss" (existing `_rerank` contract). The ce_score field defaults `None` ("cannot judge") and changes NO ordering. Existing 96 retrieval tests must still pass.
- **Gate on CE, never on the generator** (review R3): this signal is the cross-encoder's, consumed downstream; this plan only EXPOSES it (calibration + tier-gating are later plans).
- **Test command:** `python3 -m pytest <file> -q`. Reranker integration tests read the live `gsa_gateway.db` (read-only) — same as the existing `test_reranker_integration.py`.
- **No production behavior change** in this plan: `ce_score` is populated but not yet read by any gate except `top_relevance` (which already gates the live-fallback; its numeric result is unchanged for the common path — see Task 3 equivalence test).

---

### Task 1: Add the `ce_score` field to `RetrievedChunk`

**Files:**
- Modify: `v2/core/retrieval/retriever.py:166-177` (the `RetrievedChunk` dataclass)
- Test: `v2/tests/test_ce_confidence_signal.py` (create)

**Interfaces:**
- Produces: `RetrievedChunk.ce_score: float | None` (default `None`). Consumed by Task 2 (attach) and Task 3 (read via shim).

- [ ] **Step 1: Write the failing test**

```python
# v2/tests/test_ce_confidence_signal.py
from v2.core.retrieval.retriever import RetrievedChunk


def test_retrieved_chunk_has_ce_score_default_none():
    c = RetrievedChunk(
        item_id=1, title="t", type="policy", content="body",
        org_path="NJIT > GSA", similarity=0.5, source="hybrid", rrf_score=0.1,
    )
    assert c.ce_score is None
    c.ce_score = 0.83
    assert c.ce_score == 0.83
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_ce_confidence_signal.py::test_retrieved_chunk_has_ce_score_default_none -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument` is NOT raised (positional ok), but `assert c.ce_score is None` raises `AttributeError: 'RetrievedChunk' object has no attribute 'ce_score'`.

- [ ] **Step 3: Add the field**

In `v2/core/retrieval/retriever.py`, in the `RetrievedChunk` dataclass, after the `verified` field:

```python
    source_url: str | None = None  # provenance carried to the prompt (R4)
    verified: bool = True          # False = first-layer LLM draft, not authoritative
    ce_score: float | None = None  # cross-encoder relevance of the MATCHED CHUNK; None = not reranked / cannot judge
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest v2/tests/test_ce_confidence_signal.py::test_retrieved_chunk_has_ce_score_default_none -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add v2/core/retrieval/retriever.py v2/tests/test_ce_confidence_signal.py
git commit -m "feat(retrieval): add ce_score field to RetrievedChunk (step 0a)"
```

---

### Task 2: Thread the raw matched-chunk CE score out of `_rerank` and attach it in `retrieve`

**Files:**
- Modify: `v2/core/retrieval/retriever.py:282-313` (`_rerank` — return the ce map)
- Modify: `v2/core/retrieval/retriever.py:566` (caller) and `:597-603` (chunk construction)
- Test: `v2/tests/test_ce_confidence_signal.py` (extend)

**Interfaces:**
- Consumes: `RetrievedChunk.ce_score` (Task 1).
- Produces: `_rerank(...) -> tuple[list[tuple[int, float]], dict[int, float]]` — `(ranked, ce_by_iid)` where `ce_by_iid` maps item_id → raw CE score for items that were reranked (empty dict when rerank skipped). `retrieve` attaches `ce_score=ce_by_iid.get(iid)`.

- [ ] **Step 1: Write the failing test**

```python
# append to v2/tests/test_ce_confidence_signal.py
from v2.core.database.schema import get_connection
from v2.core.retrieval.retriever import V2Retriever
from v2.core.retrieval.embedder import Embedder


class _StubReranker:
    available = True
    def __init__(self, target): self.target = target.lower()
    def score(self, query, passages):
        return [1.0 if self.target in (p or "").lower() else 0.0 for p in passages]


def test_retrieve_attaches_matched_chunk_ce_score():
    conn = get_connection("gsa_gateway.db")
    q = "What is the maximum GSA travel award per fiscal year?"
    rr = V2Retriever(conn, Embedder(), reranker=_StubReranker("travel award"))
    chunks = rr.retrieve(q, limit=5)
    # at least one reranked chunk carries a real CE score (not None)
    assert any(c.ce_score is not None for c in chunks)
    # the stub scores are exactly 0.0/1.0
    assert all(c.ce_score in (0.0, 1.0) for c in chunks if c.ce_score is not None)


def test_retrieve_ce_score_none_when_no_reranker():
    conn = get_connection("gsa_gateway.db")
    rr = V2Retriever(conn, Embedder(), reranker=None)
    chunks = rr.retrieve("What is the maximum GSA travel award per fiscal year?", limit=5)
    assert all(c.ce_score is None for c in chunks)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_ce_confidence_signal.py -q`
Expected: FAIL — `test_retrieve_attaches_matched_chunk_ce_score` fails (`assert any(...)` is False; all ce_score are None because nothing attaches them yet).

- [ ] **Step 3: Make `_rerank` return the ce map**

In `_rerank`, change the two early-return paths and the final return:

```python
        if not self.rerank_enabled or self.reranker is None or len(ranked) < 2:
            return ranked, {}
        window = ranked[: self.rerank_pool]
        passages = []
        for iid, _ in window:
            passage = None
            if chunk_ids and iid in chunk_ids:
                passage = self._chunk_passage(chunk_ids[iid])   # CE sees the matched deep content
            passages.append(passage if passage else (rows[iid]["content"] or ""))
        ce = self.reranker.score(query, passages)
        if ce is None:
            return ranked, {}
        ce_by_iid = {window[i][0]: float(ce[i]) for i in range(len(window))}
        fused_rank = {iid: r for r, (iid, _) in enumerate(window, start=1)}
        ce_order = sorted(range(len(window)), key=lambda i: -ce[i])
        ce_rank = {window[i][0]: r for r, i in enumerate(ce_order, start=1)}

        def _score(iid):
            rrf = 1.0 / (RRF_K + fused_rank[iid]) + 1.0 / (RERANK_CE_K + ce_rank[iid])
            return rrf * self._boost_for(rows[iid], now)

        rescored = sorted(((iid, _score(iid)) for iid, _ in window), key=lambda kv: -kv[1])
        return rescored + ranked[self.rerank_pool:], ce_by_iid
```

- [ ] **Step 4: Update the caller and chunk construction**

At line 566, change:

```python
        ranked, ce_by_iid = self._rerank(query, ranked, rows, now, best_chunk or None)
```

In the chunk-construction loop (line 597), add the `ce_score` kwarg:

```python
            chunks.append(RetrievedChunk(
                item_id=iid, title=r["title"], type=r["type"], content=r["content"],
                org_path=self.org_path(r["org_id"]),
                similarity=None if expanded else sim.get(iid),
                source=source, rrf_score=boosted,
                source_url=r["source_url"], verified=_meta_verified(r["metadata"]),
                ce_score=ce_by_iid.get(iid),
            ))
```

- [ ] **Step 5: Run the new tests AND the full retrieval suite (no-regression)**

Run: `python3 -m pytest v2/tests/test_ce_confidence_signal.py v2/tests/test_reranker_integration.py v2/tests/test_retrieval.py v2/tests/test_chunk_retrieval.py -q`
Expected: PASS (new ce_score tests pass; existing rerank/retrieval tests unchanged — ordering is identical because ce_by_iid is metadata only).

- [ ] **Step 6: Commit**

```bash
git add v2/core/retrieval/retriever.py v2/tests/test_ce_confidence_signal.py
git commit -m "feat(retrieval): thread matched-chunk CE score onto RetrievedChunk.ce_score (step 0a)"
```

---

### Task 3: Let `top_relevance` reuse `ce_score` instead of a second CE pass

**Files:**
- Modify: `v2/integration/retriever_shim.py:101-118` (`_to_v1` — carry ce_score in metadata)
- Modify: `v2/integration/retriever_shim.py:70-80` (`top_relevance` — prefer existing score)
- Test: `v2/tests/test_ce_confidence_signal.py` (extend)

**Interfaces:**
- Consumes: `RetrievedChunk.ce_score` (Task 2), surfaced as `V1Chunk.metadata["ce_score"]`.
- Produces: `top_relevance(query, chunks)` returns `chunks[0].metadata["ce_score"]` when present (no reranker call); else falls back to the existing `reranker.score(query, [chunks[0].text])`; `None` if neither.

- [ ] **Step 1: Write the failing test**

```python
# append to v2/tests/test_ce_confidence_signal.py
from v2.integration.retriever_shim import V2RetrieverShim


class _ExplodingReranker:
    available = True
    def score(self, query, passages):
        raise AssertionError("top_relevance must NOT re-run the cross-encoder when ce_score is present")


class _FakeV1:
    def __init__(self, ce):
        self.text = "some body text"
        self.metadata = {"ce_score": ce}


def test_top_relevance_reuses_ce_score_without_second_pass():
    shim = object.__new__(V2RetrieverShim)
    shim.reranker = _ExplodingReranker()
    assert shim.top_relevance("q", [_FakeV1(0.91)]) == 0.91


def test_top_relevance_falls_back_when_no_ce_score():
    shim = object.__new__(V2RetrieverShim)
    shim.reranker = type("R", (), {"available": True,
                                   "score": staticmethod(lambda q, p: [0.42])})()
    assert shim.top_relevance("q", [_FakeV1(None)]) == 0.42
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest v2/tests/test_ce_confidence_signal.py::test_top_relevance_reuses_ce_score_without_second_pass -q`
Expected: FAIL — current `top_relevance` always calls `self.reranker.score`, so `_ExplodingReranker` raises `AssertionError`.

- [ ] **Step 3: Carry ce_score through `_to_v1`**

In `_to_v1`, add ce_score to the metadata dict:

```python
            metadata={"org_path": c.org_path, "source": c.source,
                      "ce_score": getattr(c, "ce_score", None)},
```

- [ ] **Step 4: Make `top_relevance` prefer the existing score**

Replace the body of `top_relevance`:

```python
    def top_relevance(self, query, chunks):
        """Cross-encoder relevance of the best chunk (0..1), the gate signal for the live
        njit.edu fallback. Prefers the ce_score already computed on the matched chunk during
        rerank (no second CE pass, and not the CE-truncated full doc); falls back to a direct
        score. None if it cannot judge."""
        if not chunks:
            return None
        pre = (getattr(chunks[0], "metadata", None) or {}).get("ce_score")
        if pre is not None:
            return pre
        if not self.reranker:
            return None
        scores = self.reranker.score(query, [chunks[0].text])
        return scores[0] if scores else None
```

- [ ] **Step 5: Run the new tests + shim tests (no-regression)**

Run: `python3 -m pytest v2/tests/test_ce_confidence_signal.py v2/tests/test_shim_item_types.py v2/tests/test_shim_query_vec.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add v2/integration/retriever_shim.py v2/tests/test_ce_confidence_signal.py
git commit -m "feat(retrieval): top_relevance reuses matched-chunk ce_score, no second CE pass (step 0a)"
```

---

## Self-Review

**Spec coverage:** This plan implements the gate-SIGNAL half of spec §12 R3 / goal G8 ("free `ce_score`, matched-chunk passage, no double CE pass"). The remaining G8 work — calibrating per-tier thresholds on a labeled set, and gating tiers on the signal — is explicitly DEFERRED to step-0b (eval/labeled set) and step-4 (tier wiring) plans, per the spec's "no tier wiring until the signal exists." Not in scope here by design.

**Placeholder scan:** none — every step has runnable code and exact commands.

**Type consistency:** `_rerank` now returns `tuple[list[tuple[int,float]], dict[int,float]]`; the single caller (line 566) is updated to unpack it. `ce_score: float | None` is consistent across `RetrievedChunk` (Task 1), `retrieve` attach (Task 2), and `V1Chunk.metadata["ce_score"]` (Task 3). `top_relevance` return type unchanged (`float | None`).

**Risk note:** `_rerank` has exactly one caller (verified: `grep -n _rerank retriever.py` → defn + line 566). If a future caller is added, it must unpack the tuple — flagged for the reviewer.
