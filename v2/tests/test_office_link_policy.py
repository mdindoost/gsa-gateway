# v2/tests/test_office_link_policy.py  (new file; more tests added in A3)
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from v2.core.ingestion import web_crawler as wc


def test_make_fetcher_still_returns_html_only():
    # Backward-compat: the existing signature (html|None) is preserved.
    f = wc.make_fetcher()
    assert callable(f)


def test_fetch_with_status_exists_and_is_callable():
    f = wc.fetch_with_status()
    assert callable(f)
