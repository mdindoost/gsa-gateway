import hashlib
import numpy as np
from v2.core.retrieval.route_classifier import RouteClassifier


class _Ident:
    def mask(self, q): return q


def _fake_encode(texts):
    rows = []
    for t in texts:
        v = np.zeros(8)
        for tok in t.lower().split():
            v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % 8] += 1.0
        n = np.linalg.norm(v)
        rows.append(v / n if n else v)
    return np.array(rows)


def test_top_returns_family_score_margin():
    ex = [("who teaches cs", "KG"), ("free pizza today", "RAG"), ("hello there", "COMMAND")]
    clf = RouteClassifier(ex, _fake_encode, _Ident())
    fam, score, margin = clf.top("who teaches math")
    assert fam == "KG"
    assert 0.0 <= score <= 1.0001
    assert margin >= 0.0


def test_masker_is_applied_to_query():
    class _MaskOrg:
        def mask(self, q): return q.replace("cs", "<ORG>")
    ex = [("who teaches <ORG>", "KG"), ("free pizza", "RAG")]
    clf = RouteClassifier(ex, _fake_encode, _MaskOrg())
    fam, _, _ = clf.top("who teaches cs")     # masked to "who teaches <ORG>" → exact KG exemplar
    assert fam == "KG"


def test_failed_exemplar_embed_is_dropped_not_ragged():
    # A None embed for one exemplar must be dropped (with its label), leaving a CLEAN float matrix —
    # not a ragged object-dtype array that would make every decide() matmul raise (review F6).
    def enc(texts):
        out = []
        for t in texts:
            if "bad" in t:
                out.append(None)                       # simulate a failed/empty embed
            elif "kg" in t:
                out.append([1.0, 0.0])
            else:
                out.append([0.0, 1.0])
        return out
    clf = RouteClassifier([("good kg", "KG"), ("bad row", "RAG"), ("good rag", "RAG")], enc, _Ident())
    assert clf.mat.shape[0] == 2                        # the None exemplar dropped
    assert clf.mat.dtype != object                      # clean float matrix
    assert clf.row_label == ["KG", "RAG"]               # labels stay aligned
    fam, _, _ = clf.top("a good kg query")
    assert fam == "KG"


def test_failed_query_embed_returns_empty_ranked():
    def enc(texts):
        return [None for _ in texts]                    # every embed fails
    clf = RouteClassifier([("x", "KG")], enc, _Ident())
    assert clf.mat.shape[0] == 0                         # all exemplars dropped → empty matrix
    assert clf.ranked("anything") == []                 # no crash; caller degrades gracefully
