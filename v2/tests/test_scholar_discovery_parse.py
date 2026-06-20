"""scholar_discovery pure parsing/matching — no DB, no network.

parse_profile_identity reads name + verified-email domain + affiliation, and flags a captcha
'blocked' page (no #gsc_prf_in). name_matches is the STRICT name check (full first+last, accent/
case/order-insensitive, conflicting middle initial = mismatch). name_plausible is the looser
'last name + first initial' check that routes near-misses to the review queue (never strict).
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.ingestion.scholar_discovery import (
    parse_profile_identity, name_matches, name_plausible,
)

PROFILE_NJIT = """<html><body>
<div id="gsc_prf_in">Nirwan Ansari</div>
<div id="gsc_prf_ila">Distinguished Professor of Electrical and Computer Engineering, New Jersey Institute of Technology</div>
<div id="gsc_prf_ivh">Verified email at njit.edu - <a href="#">Homepage</a></div>
<div id="gsc_prf_int"><a class="gsc_prf_inta">Cloud computing</a><a class="gsc_prf_inta">Internet of things</a></div>
</body></html>"""

PROFILE_NONNJIT = """<html><body>
<div id="gsc_prf_in">Mark Lyon</div>
<div id="gsc_prf_ila">University of New Hampshire</div>
<div id="gsc_prf_ivh">Verified email at unh.edu</div>
</body></html>"""

PROFILE_NOEMAIL = """<html><body>
<div id="gsc_prf_in">Jane Doe</div>
<div id="gsc_prf_ila">New Jersey Institute of Technology</div>
</body></html>"""

BLOCKED = "<html><body>Please show you're not a robot. Sorry...</body></html>"


def test_parse_njit_profile():
    p = parse_profile_identity(PROFILE_NJIT)
    assert p["name"] == "Nirwan Ansari"
    assert p["verified_email_domain"] == "njit.edu"
    assert "Electrical and Computer Engineering" in p["affiliation"]
    assert p["blocked"] is False


def test_parse_nonnjit_domain():
    assert parse_profile_identity(PROFILE_NONNJIT)["verified_email_domain"] == "unh.edu"


def test_parse_no_verified_email():
    assert parse_profile_identity(PROFILE_NOEMAIL)["verified_email_domain"] is None


def test_parse_blocked_page():
    p = parse_profile_identity(BLOCKED)
    assert p["blocked"] is True


# ── name matching ─────────────────────────────────────────────────────────────
def test_name_matches_reordered():
    assert name_matches("Ghosh, Arnob", "Arnob Ghosh")
    assert name_matches("Nirwan Ansari", "Nirwan Ansari")


def test_name_matches_accent_insensitive():
    assert name_matches("Jose Morel", "José Morel")


def test_name_matches_missing_initial_is_neutral():
    assert name_matches("David Lee", "David J. Lee")


def test_name_matches_conflicting_initial_is_mismatch():
    assert not name_matches("David J. Lee", "David K. Lee")


def test_name_matches_different_person_false():
    assert not name_matches("Arnob Ghosh", "Nirwan Ansari")


def test_bare_initial_first_name_is_not_strict_but_plausible():
    assert not name_matches("Catalin Turc", "C. Turc")     # bare initial → not strict
    assert name_plausible("Catalin Turc", "C. Turc")        # but plausible → review queue


def test_plausible_requires_same_surname():
    assert not name_plausible("Catalin Turc", "C. Wang")
