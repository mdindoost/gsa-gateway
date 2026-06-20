from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.ingestion.scholar import parse_scholar_interests

_HTML = """
<div id="gsc_prf_int">
  <a class="gsc_prf_inta" href="...">Computing Education</a>
  <a class="gsc_prf_inta" href="...">  Pervasive Computing </a>
  <a class="gsc_prf_inta" href="...">Wearable Computing</a>
  <a class="gsc_prf_inta" href="...">Computing Education</a>
</div>
"""


def test_parses_interest_tags_trimmed_and_deduped():
    assert parse_scholar_interests(_HTML) == [
        "Computing Education", "Pervasive Computing", "Wearable Computing"]


def test_empty_when_no_interests():
    assert parse_scholar_interests("<html><body>no interests</body></html>") == []
    assert parse_scholar_interests("") == []
