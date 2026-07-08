import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from eval.processing_debt.types import Nugget, PresenceResult, FactRecord

def test_nugget_defaults():
    n = Nugget(text="Pan Xu is an Assistant Professor.", vital=True)
    assert n.vital is True and n.text.startswith("Pan Xu")

def test_presence_result_absent_default():
    p = PresenceResult(present=False, probes_hit=[], evidence=[])
    assert p.present is False and p.probes_hit == []

def test_factrecord_roundtrips_to_dict():
    fr = FactRecord(question="q", stratum="rag", fact_text="f", vital=True,
                    guard_verdict="supported", in_answer=False,
                    presence=PresenceResult(True, ["fts_probe"], []),
                    fact_class="OWNED_NOT_SURFACED", stage="POOL", xray_ref="q")
    d = fr.as_dict()
    assert d["fact_class"] == "OWNED_NOT_SURFACED" and d["presence"]["present"] is True
