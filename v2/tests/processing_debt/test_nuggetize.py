import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from eval.processing_debt.types import OracleAnswer
from eval.processing_debt.nuggetize import nuggetize

def test_nuggetize_maps_vital_and_text():
    gen = lambda system, prompt, schema: {"nuggets": [
        {"text": "MS CS requires a 4-year computing degree.", "vital": True},
        {"text": "The building has a nice lobby.", "vital": False}]}
    oa = OracleAnswer(question="admission?", answer="... long answer ...", citations=[])
    out = nuggetize(oa, gen=gen)
    assert len(out) == 2
    assert out[0].vital is True and out[1].vital is False
    assert out[0].text.startswith("MS CS requires")

def test_nuggetize_empty_on_failure():
    gen = lambda system, prompt, schema: None
    oa = OracleAnswer(question="q", answer="a", citations=[])
    assert nuggetize(oa, gen=gen) == []
