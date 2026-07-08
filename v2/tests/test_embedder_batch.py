from v2.core.retrieval.embedder import Embedder


def test_embed_documents_prefixes_and_normalizes(monkeypatch):
    e = Embedder.__new__(Embedder)
    from v2.core.retrieval.model_descriptor import active_descriptor
    e.descriptor = active_descriptor()
    seen = {}

    def fake_batch(texts, timeout=60):
        seen["texts"] = texts
        return [[3.0, 4.0], [0.0, 0.0]]  # 2nd is un-normalizable

    e._embed_batch = fake_batch
    out = e.embed_documents(["alpha", "beta"])
    assert seen["texts"][0].endswith("alpha")  # doc prefix applied, text preserved
    assert abs((out[0][0] ** 2 + out[0][1] ** 2) - 1.0) < 1e-6  # normalized
    assert out[1] is None  # zero vector → None
