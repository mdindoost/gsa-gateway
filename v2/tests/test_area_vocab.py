import numpy as np, sqlite3, json
import v2.core.retrieval.area_expand as ax
import v2.core.retrieval.area_cache as area_cache


def _fixture(conn):
    conn.execute("CREATE TABLE knowledge_items(id INTEGER PRIMARY KEY, type TEXT, is_active INT, metadata TEXT)")
    for i, (tags) in enumerate([["cyber security"], ["network security", "cloud security"]]):
        conn.execute("INSERT INTO knowledge_items(type,is_active,metadata) VALUES('research_areas',1,?)",
                     (json.dumps({"entity_id": f"e{i}", "areas": tags}),))
    conn.commit()


def test_vocab_and_signature_and_embeddings(monkeypatch):
    conn = sqlite3.connect(":memory:"); _fixture(conn)
    assert set(ax.area_vocab(conn)) == {"cyber security", "network security", "cloud security"}
    sig1 = ax.vocab_signature(conn)

    class Stub:  # deterministic 2-d embeddings
        def embed_documents(self, texts): return [[1.0, 0.0] for _ in texts]

    tags, mat = ax.vocab_embeddings(conn, embedder=Stub())
    assert len(tags) == 3 and mat.shape == (3, 2)
    conn.execute("INSERT INTO knowledge_items(type,is_active,metadata) VALUES('research_areas',1,?)",
                 (json.dumps({"entity_id": "e9", "areas": ["malware"]}),)); conn.commit()
    assert ax.vocab_signature(conn) != sig1        # change detected


def test_stale_blob_is_discarded_not_misaligned(monkeypatch):
    """A cached blob whose element count doesn't evenly divide the current tag
    count must never be reshaped/returned as-is (that would misalign vectors to
    tags) — it must be discarded and the vocab re-embedded, without raising."""
    conn = sqlite3.connect(":memory:"); _fixture(conn)
    ax._VOCAB_MEMO.clear()
    sig = ax.vocab_signature(conn)

    store: dict[str, bytes] = {}
    # Poison the cache: 3 tags currently, but stash a blob with 7 floats —
    # 7 % 3 != 0, so a reshape(3, -1) would raise or (if it happened to divide)
    # silently misalign. Under the current key so the memo/blob lookup finds it.
    store[f"vocab:{sig}"] = np.array([1.0] * 7, dtype=np.float32).tobytes()

    def fake_get_blob(name):
        return store.get(name)

    def fake_put_blob(name, data):
        store[name] = data

    monkeypatch.setattr(area_cache, "get_blob", fake_get_blob)
    monkeypatch.setattr(area_cache, "put_blob", fake_put_blob)

    class Stub:
        def embed_documents(self, texts): return [[1.0, 0.0] for _ in texts]

    tags, mat = ax.vocab_embeddings(conn, embedder=Stub())
    assert len(tags) == 3
    assert mat.shape == (3, 2)                     # re-embedded, correctly aligned
    # the fresh matrix must have overwritten the stale blob at the same key
    fresh = np.frombuffer(store[f"vocab:{sig}"], dtype=np.float32)
    assert fresh.size == 3 * 2 and fresh.size % len(tags) == 0


def test_corrupt_blob_bytes_are_discarded(monkeypatch):
    """A blob that can't even be interpreted as float32 (odd byte count) must
    fall through to re-embedding instead of propagating a ValueError."""
    conn = sqlite3.connect(":memory:"); _fixture(conn)
    ax._VOCAB_MEMO.clear()
    sig = ax.vocab_signature(conn)

    store: dict[str, bytes] = {f"vocab:{sig}": b"\x00\x01\x02"}  # 3 bytes, not a multiple of 4

    monkeypatch.setattr(area_cache, "get_blob", lambda name: store.get(name))
    monkeypatch.setattr(area_cache, "put_blob", lambda name, data: store.__setitem__(name, data))

    class Stub:
        def embed_documents(self, texts): return [[1.0, 0.0] for _ in texts]

    tags, mat = ax.vocab_embeddings(conn, embedder=Stub())
    assert len(tags) == 3 and mat.shape == (3, 2)
