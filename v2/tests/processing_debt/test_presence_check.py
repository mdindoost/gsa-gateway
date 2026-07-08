import sys, sqlite3
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.presence_check import kg_probe, fts_probe, grep_probe, presence, _nli_windows

def _conn(fixture_db):
    c = sqlite3.connect(fixture_db); c.row_factory = sqlite3.Row; return c

def test_fts_probe_finds_normal_type(fixture_db):
    ev = fts_probe(_conn(fixture_db), "MS in Computer Science requires a four-year computing degree")
    assert any(e.probe == "fts_probe" and e.row_or_node_id == "10" for e in ev)

def test_fts_probe_finds_excluded_publication_type(fixture_db):
    ev = fts_probe(_conn(fixture_db), "Veil volume-hiding algorithm")
    assert any(e.item_type == "publication" for e in ev)

def test_kg_probe_finds_person(fixture_db):
    ev = kg_probe(_conn(fixture_db), "Pan Xu is an Assistant Professor")
    assert any(e.probe == "kg_probe" and e.row_or_node_id == "1" for e in ev)

def test_kg_probe_span_includes_attrs_and_edges(fixture_db):   # M1 regression guard
    ev = kg_probe(_conn(fixture_db), "Pan Xu")
    span = next(e.span for e in ev if e.row_or_node_id == "1")
    assert "Assistant Professor" in span and "4310" in span   # from edge titles + node attrs, not bare name

def test_grep_probe_exact_string(fixture_db):
    ev = grep_probe(_conn(fixture_db), "four-year computing degree")
    assert any(e.probe == "grep_probe" for e in ev)

def test_grep_probe_hits_node_attrs(fixture_db):               # M3 regression guard
    ev = grep_probe(_conn(fixture_db), "4310 Guttenberg Information Technologies Center")
    assert any(e.probe == "grep_probe" and e.source_type == "node" for e in ev)

def test_presence_present_when_verdict_yes(fixture_db):
    r = presence(_conn(fixture_db), "MS in Computer Science requires a four-year computing degree",
                 embedder="SKIP", verdict_fn=lambda f, t: "yes")
    assert r.present is True and "fts_probe" in r.probes_hit
    assert r.low_conf is False and r.max_score >= 0.99

def test_presence_unsure_is_low_conf_not_present(fixture_db):  # NEW lean (Fable B2): unsure != present
    r = presence(_conn(fixture_db), "MS in Computer Science requires a four-year computing degree",
                 embedder="SKIP", verdict_fn=lambda f, t: "unsure")
    assert r.present is False           # confident-only presence
    assert r.low_conf is True           # but surfaced as a low-confidence bucket, not dropped
    assert r.evidence                   # unsure spans are RETAINED for human adjudication

def test_presence_absent_when_verdict_no(fixture_db):
    r = presence(_conn(fixture_db), "The provost announced a tuition freeze in 1998",
                 embedder="SKIP", verdict_fn=lambda f, t: "no")
    assert r.present is False and r.low_conf is False

def test_presence_yes_beats_unsure_for_same_fact(fixture_db):
    # a mix: one span yes -> present wins, low_conf stays False
    flip = {"n": 0}
    def vf(f, t):
        flip["n"] += 1
        return "yes" if flip["n"] == 1 else "unsure"
    r = presence(_conn(fixture_db), "MS in Computer Science requires a four-year computing degree",
                 embedder="SKIP", verdict_fn=vf)
    assert r.present is True and r.low_conf is False


# --- B3: window long spans so a far-in entailing sentence still reaches the 512-token NLI ---
def test_nli_windows_short_span_unchanged():
    assert _nli_windows("a short span about NJIT", "NJIT fact") == ["a short span about NJIT"]

def test_nli_windows_long_span_yields_match_neighborhood():
    needle = "Deric Raymond 973-642-7042"
    long = ("filler " * 400) + needle + (" filler" * 400)
    wins = _nli_windows(long, "Media Relations: Deric Raymond, 973-642-7042")
    assert any("Deric Raymond" in w for w in wins)
    assert all(len(w) <= 1400 for w in wins)   # each window bounded well under the 512-token cap


# --- batch/NLI path (no verdict_fn): uses entailment.batch_verdicts with an injected judge ---
class _FakeJudge:
    def __init__(self, score): self._score = score
    def score(self, fact, spans): return [self._score] * len(spans)

def test_presence_batch_path_uses_judge_scores(fixture_db):
    r = presence(_conn(fixture_db), "MS in Computer Science requires a four-year computing degree",
                 embedder="SKIP", judge=_FakeJudge(0.95))
    assert r.present is True and r.low_conf is False and r.max_score >= 0.95

def test_presence_batch_path_low_conf_band(fixture_db):
    r = presence(_conn(fixture_db), "MS in Computer Science requires a four-year computing degree",
                 embedder="SKIP", judge=_FakeJudge(0.42))
    assert r.present is False and r.low_conf is True
