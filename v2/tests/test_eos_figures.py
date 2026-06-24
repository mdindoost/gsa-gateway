"""TDD: figure/asset capture so image-only pages (e.g. the campus parking MAP) aren't
lost. Mechanical capture only — image src + its alt text + linked asset files, all
literal page data; the map itself is never described/interpreted (anti-fab).
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.ingestion.eos_crawl import extract_prose

FIXTURE = Path(__file__).parent / "fixtures" / "eos" / "campus_map.html"
URL = "https://www.njit.edu/parking/campus-parking-map-0"


def _page():
    return extract_prose(URL, FIXTURE.read_text())


def test_captures_map_image_with_alt():
    imgs = _page().images
    assert any(alt == "Parking Map" and "Preview" in url for url, alt in imgs)


def test_image_urls_are_absolute():
    assert all(u.startswith("https://www.njit.edu/") for u, _ in _page().images)


def test_captures_enlarged_map_asset_link():
    files = _page().files
    assert any(url.lower().endswith(".jpg") and "MAP" in url.upper() for url, _ in files)
