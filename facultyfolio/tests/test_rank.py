from facultyfolio import rank, config, db


def test_rank_of_buckets_real_titles():
    r = rank.rank_of
    assert r("Professor, Department Chair") == (0, "Department Chair")          # Chair heads it
    assert r("Distinguished Professor, Associate Dean of Research")[1] == "Distinguished Professor"
    assert r("Associate Professor")[1] == "Associate Professor"                 # not bare Professor
    assert r("Assistant Professor")[1] == "Assistant Professor"
    assert r("Professor")[1] == "Professor"
    assert r("Senior University Lecturer")[1] == "Senior University Lecturer"    # not bare University Lecturer
    assert r("University Lecturer")[1] == "University Lecturer"
    assert r("Dean, Ying Wu College of Computing") == (config.RANK_LADDER.index("Professor"), "Professor")
    assert r("First Year Computer Science Education, Director")[1] == "Faculty"
    assert r("")[1] == "Faculty" and r(None)[1] == "Faculty"


def test_rank_of_strict_ordering():
    order = [rank.rank_of(t)[0] for t in [
        "Professor, Department Chair", "Distinguished Professor", "Professor",
        "Associate Professor", "Assistant Professor", "Senior University Lecturer",
        "University Lecturer", "Director",
    ]]
    assert order == sorted(order) and len(set(order)) == 8      # strictly increasing, all distinct


def test_roster_full_department():
    r = rank.roster(config.CS_ORG_ID)
    assert len(r) == len(db.cs_faculty_slugs())            # ALL home faculty, not just Scholar
    keys = {"slug", "name", "title", "rank_index", "rank_label", "citations", "h_index", "areas"}
    assert all(keys <= set(row) for row in r)
    by_slug = {row["slug"]: row for row in r}
    # Zaidenberg has no Scholar -> present with citations None (the multi-view raison d'être)
    assert "acz6" in by_slug and by_slug["acz6"]["citations"] is None
    # at least one Scholar person carries an int citation count
    assert any(isinstance(row["citations"], int) for row in r)
    # rank fields are consistent with rank_of on the title
    for row in r:
        assert rank.rank_of(row["title"]) == (row["rank_index"], row["rank_label"])


def test_cs_coverage():
    N, M = rank.coverage(config.CS_ORG_ID)
    assert (N, M) == (39, 57)


def test_ranked_list():
    lst = rank.ranked_list(config.CS_ORG_ID)
    assert len(lst) == 39
    assert lst[0]["rank"] == 1
    assert lst[0]["citations"] >= lst[1]["citations"]     # descending
    assert all("slug" in r and "name" in r for r in lst)
    # ranks are 1..N contiguous
    assert [r["rank"] for r in lst] == list(range(1, 40))
