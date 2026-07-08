"""Judge-fix wiring in entailment.py (Fable gate): NLI-primary scoring, threshold->verdict,
FAIL-LOUD (never silent granite fallback), improved prompt for generative backends, env select,
and a BATCH scorer seam so presence_check can score all spans in one NLI call."""
import sys
from pathlib import Path
import pytest
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt import entailment as E


class _FakeJudge:
    def __init__(self, scores): self._scores = scores
    def score(self, fact, spans): return self._scores


# --- threshold mapping (HI=0.5, LO=0.35) ---
def test_score_to_verdict_yes_above_hi():
    assert E.score_to_verdict(0.60) == "yes"

def test_score_to_verdict_unsure_in_band():
    assert E.score_to_verdict(0.40) == "unsure"

def test_score_to_verdict_no_below_lo():
    assert E.score_to_verdict(0.20) == "no"

def test_thresholds_are_boundaries():
    assert E.score_to_verdict(E.HI) == "yes"        # >= HI
    assert E.score_to_verdict(E.LO) == "unsure"     # >= LO, < HI
    assert E.score_to_verdict(E.LO - 0.001) == "no"


# --- batch scorer: one NLI call, (verdict, score) per span, in order ---
def test_batch_verdicts_maps_scores_in_order():
    out = E.batch_verdicts("f", ["s1", "s2", "s3"], judge=_FakeJudge([0.9, 0.4, 0.1]))
    assert out == [("yes", 0.9), ("unsure", 0.4), ("no", 0.1)]

def test_batch_verdicts_empty_spans():
    assert E.batch_verdicts("f", [], judge=_FakeJudge([])) == []


# --- FAIL LOUD: NLI None must raise, never silently fall back ---
def test_batch_verdicts_fail_loud_when_judge_returns_none():
    with pytest.raises(RuntimeError):
        E.batch_verdicts("f", ["s"], judge=_FakeJudge(None))


# --- env-selected judge id (audit) ---
def test_active_judge_id_defaults_to_nli(monkeypatch):
    monkeypatch.delenv("PD_JUDGE", raising=False)
    assert E.active_judge_id() == "nli"

def test_active_judge_id_reads_env(monkeypatch):
    monkeypatch.setenv("PD_JUDGE", "gemma3:12b")
    assert E.active_judge_id() == "gemma3:12b"


# --- improved prompt for generative backends (no more lazy 'unsure' default) ---
def test_improved_system_prompt_exists_and_is_stricter():
    assert "different subject" in E.IMPROVED_SYSTEM.lower()
    assert E.IMPROVED_SYSTEM != E._SYSTEM

def test_generative_verdict_uses_improved_prompt():
    seen = {}
    def gen(system, prompt, schema):
        seen["system"] = system
        return {"verdict": "no"}
    v = E.generative_verdict("A fact", "A different-subject text", model="llama3.1:8b", gen=gen)
    assert v == "no"
    assert "different subject" in seen["system"].lower()


# --- existing gen-injection path stays intact (back-compat) ---
def test_entail_verdict_gen_injection_unchanged():
    assert E.entail_verdict("x", "y", gen=lambda s, p, sc: {"verdict": "yes"}) == "yes"
    assert E.entail_verdict("x", "y", gen=lambda s, p, sc: None) == "no"


# === windowed IN_ANSWER + lenient GUARD (Fable: 2nd/3rd call sites) ===
class _ContentJudge:
    """Scores a span high iff it contains NEEDLE — lets us prove windowing surfaces a far-in match."""
    def score(self, fact, spans):
        return [0.95 if "NEEDLE" in s else 0.02 for s in spans]

def test_text_entails_fact_strict_yes():
    assert E.text_entails_fact("f", "t", judge=_FakeJudge([0.6])) is True      # >= HI
    assert E.text_entails_fact("f", "t", judge=_FakeJudge([0.45])) is False    # unsure != in-answer

def test_text_entails_fact_windows_long_text():
    # entailing content sits past the 512-token head; unwindowed NLI would truncate & miss it
    long = ("filler " * 600) + "NEEDLE here" + (" filler" * 600)
    assert E.text_entails_fact("some fact", long, judge=_ContentJudge()) is True

def test_supported_by_keeps_loose_support():
    assert E.supported_by("f", "t", judge=_FakeJudge([0.40])) is True          # >= GUARD_LO (0.35): keep
    assert E.supported_by("f", "t", judge=_FakeJudge([0.20])) is False         # clearly unrelated: drop

def test_supported_by_windows_long_page():
    long = ("nav boilerplate " * 400) + "NEEDLE supporting sentence" + (" more " * 400)
    assert E.supported_by("f", long, judge=_ContentJudge()) is True            # rescued by windowing
    assert E.supported_by("f", ("nav boilerplate " * 800), judge=_ContentJudge()) is False

def test_guard_lo_below_hi():
    assert E.GUARD_LO < E.HI     # guard is deliberately more lenient than presence

def test_oria_positive_control_regression():
    """The exact SC2 failure: this fact was DROPPED by the strict guard + missed by IN_ANSWER.
    Real NLI now KEEPS it (guard) and detects it in our answer. Pins the fix (scores ~0.997)."""
    fact = "Vincent Oria is the Chair of the Department of Computer Science at NJIT."
    elliptical_citation = ("Ying Wu College of Computing. Department of Computer Science. "
                           "Welcome from the Chairperson Vincent Oria. cs.njit.edu")
    assert E.supported_by(fact, elliptical_citation) is True          # guard keeps it (was dropped)
    our_answer = "Vincent Oria is a Professor and the Chair of the Computer Science department at NJIT."
    assert E.text_entails_fact(fact, our_answer) is True              # IN_ANSWER detects it (was missed)
