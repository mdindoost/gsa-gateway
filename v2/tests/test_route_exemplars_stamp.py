import pytest
from v2.core.retrieval.route_exemplars import ROUTER_PREFIX, router_embed, encoder_stamp, verify_stamp


class _Enc:
    # Mirrors the real Embedder surface used by router_embed: ._embed(text) + .normalize(vec).
    model = "nomic-embed-text"
    base_url = "http://localhost:11434"
    def _embed(self, text):                 # real Embedder._embed(text)->list|None
        self.last = text
        return [3.0] * 768
    @staticmethod
    def normalize(vec):                     # real Embedder.normalize is a @staticmethod
        return vec


def test_router_embed_uses_router_prefix_not_search_query():
    e = _Enc()
    router_embed(e, "who teaches cs")
    assert e.last == ROUTER_PREFIX + "who teaches cs"     # router prefix, NOT "search_query: "


def test_stamp_roundtrip_ok():
    e = _Enc()
    verify_stamp(e, encoder_stamp(e))      # no raise


def test_stamp_drift_raises():
    e = _Enc()
    bad = encoder_stamp(e) | {"model": "some-other-model"}
    with pytest.raises(RuntimeError):
        verify_stamp(e, bad)
