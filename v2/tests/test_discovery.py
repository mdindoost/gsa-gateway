from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.ingestion.discovery import (
    category_for_section, hub_children, parse_listing)

LISTING = """
<h4>Professors</h4>
<a href="//people.njit.edu/profile/oria" class="column">
  <h1 class="name">Oria, Vincent</h1><p class="title">Professor</p></a>
<a href="//people.njit.edu/profile/mili" class="column">
  <h1 class="name">Mili, Ali</h1>
  <p class="title">Professor</p><p class="title">Associate Dean for Academic Affairs</p></a>
<h4>Senior Lecturers</h4>
<a href="//people.njit.edu/profile/ba62" class="column">
  <h1 class="name">Arafeh, Bassel</h1><p class="title">Senior University Lecturer</p></a>
"""

HUB = """
<a href="/administration">College Administration Learn More</a>
<a href="https://cs.njit.edu/faculty">Computer Science Learn More</a>
<a href="/about">About</a>
"""


def test_parse_listing_extracts_people_with_section_and_dual_titles():
    ppl = parse_listing(LISTING)
    assert len(ppl) == 3
    by = {p.slug: p for p in ppl}
    assert by["oria"].name == "Oria, Vincent" and by["oria"].section == "Professors"
    assert by["mili"].titles == ["Professor", "Associate Dean for Academic Affairs"]
    assert by["ba62"].section == "Senior Lecturers"


def test_category_for_section():
    assert category_for_section("Professors") == "faculty"
    assert category_for_section("Senior Lecturers") == "faculty"
    assert category_for_section("Associate Deans") == "admin"
    assert category_for_section("Staff") == "staff"
    assert category_for_section("Faculty Emeriti") == "emeritus"
    assert category_for_section("Academic Advisors") == "advisor"
    assert category_for_section("Joint Appointments") == "joint"
    assert category_for_section("Mystery Section") is None


def test_hub_children_extracts_learn_more_links():
    kids = dict(hub_children(HUB))
    assert kids["College Administration"] == "/administration"
    assert kids["Computer Science"] == "https://cs.njit.edu/faculty"
    assert "About" not in kids  # not a Learn-More card
