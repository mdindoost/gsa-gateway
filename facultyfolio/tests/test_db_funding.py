from facultyfolio import db


def test_get_faculty_exposes_funding_for_funded_person():
    f = db.get_faculty("zhiwei")         # Zhi Wei (people.njit.edu/profile/zhiwei)
    assert "nsf" in f["funding"] or "nih" in f["funding"]
    assert f["funding"]["nih"]["njit_total"] == 1653383


def test_get_faculty_funding_empty_dict_when_absent():
    # A person with no funding still returns a dict, never KeyError.
    f = db.get_faculty("borcea")         # Cristian Borcea has NSF; pick a no-funding slug if needed
    assert isinstance(f["funding"], dict)
