# v2/tests/test_deep_fallback_ladder.py
# Pure unit test of the adopt-if-better decision, extracted as a helper.
import asyncio
import struct
from types import SimpleNamespace

import bot.config as botcfg
from bot.core.message_handler import (
    _deep_adopt, MessageHandler, MessageRequest)
from bot.services.intent_detector import INTENT_QUESTION
from v2.core.database.schema import create_all, get_connection
from v2.core.retrieval.model_descriptor import active_descriptor
from v2.integration.retriever_shim import V2RetrieverShim

_D = active_descriptor()


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


def test_adopt_when_exactly_at_threshold():
    assert _deep_adopt(current_rel=0.10, rescue_rel=0.15, threshold=0.15) is True


# ── A1: deep-fallback rescue is gated on corpus_build_ready ───────────────────
# The shim is the seam the hot path uses to reach a connection; corpus_ready()
# answers "is the chunk corpus built+coherent for the active model?" (cached
# per-process). The handler skips the deep branch when it is False, so flipping
# RETRIEVAL_DEEP_FALLBACK on a DB whose chunks aren't built is a safe no-op.

def _seed_served_item(conn):
    """A servable item with NO chunks — the realistic 'chunks not built yet' state."""
    conn.execute(
        "INSERT OR IGNORE INTO organizations(id,name,slug,type) VALUES (1,'A','a','office')")
    conn.execute(
        "INSERT OR IGNORE INTO knowledge_items(id,org_id,type,content,is_active) "
        "VALUES (100,1,'policy','x',1)")
    conn.commit()


def _seed_served_chunk(conn):
    """A fully-wired served item: item + chunk + chunk-vector → invariant holds."""
    _seed_served_item(conn)
    cur = conn.execute(
        "INSERT INTO knowledge_chunks(parent_id,source_key,ordinal,text,content_hash,model_id) "
        "VALUES (100,'item:100',0,'x','h',?)", (_D.id,))
    conn.execute(
        "INSERT INTO knowledge_chunk_vectors(chunk_id,embedding,org_id,type,parent_id) "
        "VALUES (?,?,1,'policy',100)",
        (cur.lastrowid, struct.pack(f"{_D.dim}f", *([0.0] * _D.dim))))
    conn.commit()


def test_corpus_ready_false_when_chunks_not_built(tmp_path):
    db = str(tmp_path / "t.db")
    conn = create_all(db)
    _seed_served_item(conn)                  # servable content, but chunks not built
    conn.close()
    shim = V2RetrieverShim(db_path=db, embedder=None)
    assert shim.corpus_ready() is False     # uncovered served item → deep branch skipped


def test_corpus_ready_true_when_chunks_built(tmp_path):
    db = str(tmp_path / "t.db")
    conn = create_all(db)
    _seed_served_chunk(conn)
    conn.close()
    shim = V2RetrieverShim(db_path=db, embedder=None)
    assert shim.corpus_ready() is True       # built corpus → existing behavior


def test_corpus_ready_cached_per_process(tmp_path):
    db = str(tmp_path / "t.db")
    conn = create_all(db)
    _seed_served_item(conn)                  # servable content, chunks not built
    shim = V2RetrieverShim(db_path=db, embedder=None)
    assert shim.corpus_ready() is False      # not ready yet → result cached
    _seed_served_chunk(conn)                 # build it AFTER the first check
    conn.close()
    # Cheap-but-not-free check is cached per-process (flags only flip at restart).
    assert shim.corpus_ready() is False


# ── A1 (handler level): the miss-ladder HONORS corpus_ready() ─────────────────
# Proves the gate is wired into _rag_pipeline, not just present on the shim.

class _FakeRescueChunk:
    def __init__(self, text, rel):
        self.text = text; self.relevance_score = rel; self.item_id = 7
        self.source_file = "deep__doc"; self.section_title = "Deep"; self.source_url = None


class _FakeDeepRetriever:
    """Primary + office retrieves MISS (→ deep branch reached); retrieve_deep would
    rescue, but only if the gate lets it run. corpus_ready is configurable and
    retrieve_deep records whether it was called."""
    def __init__(self, ready):
        self._ready = ready
        self.deep_called = False

    async def retrieve(self, query=None, conversation_history=None,
                       source_type_filter=None, item_types=None):
        return []                                       # curated + office miss

    def corpus_ready(self):
        return self._ready

    async def retrieve_deep(self, query, query_vec=None, item_types=None):
        self.deep_called = True
        return [_FakeRescueChunk("Deep policy: the answer is 42.", 0.9)]

    def top_relevance(self, q, chunks):
        return chunks[0].relevance_score if chunks else None


class _FakeOllama:
    async def generate_answer(self, question, chunks, conversation_history=None, temperature=0.3):
        return f"{chunks[0].text} (doc_id {chunks[0].item_id})"
    async def expand_query(self, t):
        return t


class _FakeConv:
    def get_mode(self, uid): return "gsa"
    def get_history(self, uid, max_turns=5): return []
    def add_turn(self, **k): pass


def _deep_handler(retriever):
    return MessageHandler(retriever=retriever, ollama=_FakeOllama(),
                          conversation_manager=_FakeConv(), intent_detector=None, db=None,
                          rate_limiter=None, kb=None,
                          config=SimpleNamespace(conversation_max_turns=5))


def test_handler_skips_deep_when_corpus_not_ready(monkeypatch):
    monkeypatch.setattr(botcfg, "RETRIEVAL_DEEP_FALLBACK", True)
    monkeypatch.setattr(botcfg, "DEEP_FALLBACK_THRESHOLD", 0.15)
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", False)   # isolate: no live fallback
    monkeypatch.setattr(botcfg, "LIVE_THRESHOLD", 0.15)
    r = _FakeDeepRetriever(ready=False)
    h = _deep_handler(r)
    req = MessageRequest(user_id="u", text="deep policy question", platform="discord")
    resp = asyncio.run(h._rag_pipeline(req, "deep policy question", INTENT_QUESTION))
    assert r.deep_called is False        # gate skipped the rescue
    assert resp.is_deep is False


def test_handler_runs_deep_when_corpus_ready(monkeypatch):
    monkeypatch.setattr(botcfg, "RETRIEVAL_DEEP_FALLBACK", True)
    monkeypatch.setattr(botcfg, "DEEP_FALLBACK_THRESHOLD", 0.15)
    monkeypatch.setattr(botcfg, "LIVE_ENABLED", False)
    monkeypatch.setattr(botcfg, "LIVE_THRESHOLD", 0.15)
    r = _FakeDeepRetriever(ready=True)
    h = _deep_handler(r)
    req = MessageRequest(user_id="u", text="deep policy question", platform="discord")
    resp = asyncio.run(h._rag_pipeline(req, "deep policy question", INTENT_QUESTION))
    assert r.deep_called is True         # gate allowed the rescue
    assert resp.is_deep is True
    assert "42" in resp.text
