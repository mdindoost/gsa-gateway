"""Tests for department (org) resolution in the NJIT profile parser.

Bug: org was resolved by first-match over the WHOLE page (title + bio), with
"Computer Science" first in the list. A Data Science professor whose bio mentions
"Computer Science" (very common — CS backgrounds / joint history) was mis-filed
under CS. Real example: James Geller (title "Professor, Data Science", bio full of
"Department of Computer Science") resolved to Computer Science.

Fix: resolve from the title/position lines first (authoritative department), only
falling back to the page body if the title names no known department.
"""

from v2.core.ingestion.njit_adapter import _resolve_org


def test_title_department_wins_over_bio_mentions():
    titles = ["Professor, Data Science"]
    bio_html = ("Chair of the Department of Data Science … cofounded SABOC at the "
                "Department of Computer Science at NJIT … PhD in Computer Science.")
    assert _resolve_org(titles, bio_html, "") == "Data Science"


def test_plain_cs_professor_resolves_to_cs():
    assert _resolve_org(["Professor, Computer Science"], "some bio", "") == "Computer Science"


def test_falls_back_to_body_when_title_has_no_department():
    # No department in the title → use the page body.
    assert _resolve_org(["Professor"], "works in Data Science", "") == "Data Science"


def test_returns_default_when_nothing_matches():
    assert _resolve_org(["Lecturer"], "no department here", "Ying Wu") == "Ying Wu"
