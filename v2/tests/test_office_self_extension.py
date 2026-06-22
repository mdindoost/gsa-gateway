# v2/tests/test_office_self_extension.py
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.ingestion.office_ingest import discover_candidate_hubs

SEED = "https://www.njit.edu/parking/"
HTML = """
<a href="/bursar/">Bursar</a>
<a href="https://www.njit.edu/global/">Global</a>
<a href="/parking/visitor-parking">Visitor Parking (deep, in scope)</a>
<a href="/parking/">self</a>
<a href="/registrar/deadlines/spring">deep, not a section root</a>
<a href="https://external.example.com/dining/">external host</a>
<a href="/style.css">asset</a>
"""


def test_discovers_unregistered_section_roots_only():
    out = set(discover_candidate_hubs(SEED, HTML, registered_urls={"https://www.njit.edu/global/"}))
    assert "https://www.njit.edu/bursar/" in out          # new top-level section root
    assert "https://www.njit.edu/global/" not in out      # already registered
    assert not any("visitor-parking" in u for u in out)   # deep / in-scope
    assert not any("/parking/" == u.split("njit.edu")[-1] for u in out)  # seed self
    assert not any("registrar/deadlines" in u for u in out)  # deep, not a section root
    assert not any("external.example.com" in u for u in out)
    assert not any(u.endswith(".css") for u in out)
