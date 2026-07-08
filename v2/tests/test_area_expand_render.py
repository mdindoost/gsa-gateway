"""TDD — Task 8: answer rendering — transparent expanded wording (R4).

Task 7 added a "related" verdict to does_person_research_area and an annotated
(name, tag) roster via people_by_research_area_annotated. Both were unrendered:
"related" fell through to a misleading "unknown" message, and an expanded roster
would have asserted the umbrella area on names that only hold a sibling tag
(anti-fabrication violation). This closes both gaps.
Spec: docs/superpowers/specs/2026-06-29-area-expansion (Task 8 brief).
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import v2.core.retrieval.structured_answer as sa


def test_expanded_roster_wording():
    # Expansion fired (Neamtiu only holds the sibling tag "system security") — the header must say
    # "-related areas" and Neamtiu must be annotated with HIS OWN tag, never the queried umbrella.
    result = {"skill": "people_by_research_area", "area": "cyber security", "org": None,
              "rows_annotated": [("Chase Wu", "cyber security"), ("Iulian Neamtiu", "system security")]}
    txt = sa.format_answer(result)
    assert "security-related areas" in txt
    assert "Iulian Neamtiu (system security)" in txt
    assert "Chase Wu (cyber security)" in txt


def test_unexpanded_roster_wording_unchanged():
    # No expansion (every tag == the queried area) — wording must be byte-identical to the
    # pre-Task-8 rendering (no "(tag)" annotation, no "-related areas" wording).
    result = {"skill": "people_by_research_area", "org_name": None, "area": "cyber security",
              "rows": ["Chase Wu"],
              "rows_annotated": [("Chase Wu", "cyber security")]}
    txt = sa.format_answer(result)
    assert txt == '1 faculty work on "cyber security": Chase Wu.'


def test_empty_roster_wording_unchanged():
    result = {"skill": "people_by_research_area", "org_name": None, "area": "underwater basketry",
              "rows": [], "rows_annotated": []}
    txt = sa.format_answer(result)
    assert txt == 'I couldn\'t find anyone working on "underwater basketry".'


def test_related_verdict_wording():
    result = {"skill": "does_person_research_area",
              "answer": "related", "name": "Iulian Neamtiu", "area": "cyber security",
              "matched_area": "system security", "person_areas": ["system security"]}
    txt = sa.format_answer(result)
    assert "system security" in txt and "as such" in txt
    assert "unknown" not in txt.lower()
    assert not txt.lower().startswith("no")
