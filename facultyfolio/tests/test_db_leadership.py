from facultyfolio import db


def _ywcc():
    return db.org_node_by_slug("ywcc")


def test_leadership_dean_and_assoc_deans_names_and_titles():
    lead = db.college_leadership(_ywcc())
    assert [d["name"] for d in lead["dean"]] == ["Jamie Payton"]
    assert lead["dean"][0]["title"] == "Dean, Ying Wu College of Computing"
    names = [a["name"] for a in lead["assoc_deans"]]
    assert names == ["David Bader", "Brook Wu"]          # normalized + surname-sorted
    titles = {a["name"]: a["title"] for a in lead["assoc_deans"]}
    assert titles["Brook Wu"] == "Associate Dean for Academic Affairs"
    assert titles["David Bader"] == "Associate Dean"     # role title, not "Distinguished Professor"
    assert all(", " not in a["name"] for a in lead["assoc_deans"])   # never raw "Surname, Given"


def test_leadership_empty_for_a_department_node():
    lead = db.college_leadership(16)                      # CS Org node: no admin@ edges
    assert lead == {"dean": [], "assoc_deans": []}
