from facultyfolio import db


def test_role_title_generalized_selection():
    assert db._role_title(["Distinguished Professor", "Associate Dean"]) == "Associate Dean"
    assert db._role_title(["Professor", "Interim Chair"]) == "Interim Chair"
    # compound single string containing a role word -> whole string, verbatim
    assert db._role_title(["Professor and Chair, Biomedical Engineering"]) == "Professor and Chair, Biomedical Engineering"
    # no role word -> fallback to last entry
    assert db._role_title(["Associate Professor"]) == "Associate Professor"
    assert db._role_title([]) == ""


def test_get_faculty_surfaces_leadership_for_bader():
    f = db.get_faculty("bader")
    assert f["leadership"] == [{"title": "Associate Dean", "org": "Ying Wu College of Computing"}]


def test_get_faculty_no_leadership_for_plain_faculty():
    f = db.get_faculty("ikoutis")          # single-role professor
    assert f["leadership"] == []
