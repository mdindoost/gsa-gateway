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


def _row(slug, name, title, citations=None, h=None, areas=None):
    idx, label = rank.rank_of(title)
    return {"slug": slug, "name": name, "title": title, "rank_index": idx,
            "rank_label": label, "citations": citations, "h_index": h, "areas": areas or []}


_FIXTURE = [
    _row("chair1", "Cathy Chair", "Professor, Department Chair", 500),
    _row("dean1", "Dan Dean", "Dean, Ying Wu College of Computing", 900),
    _row("prof_b", "Bob Prof", "Professor", 300),
    _row("li2", "Jing Li", "Associate Professor"),                     # no Scholar
    _row("li1", "Ann Li", "Associate Professor", 100),
    _row("asst1", "Amy Assistant", "Assistant Professor", 50),
    _row("lect1", "Leo Lecturer", "University Lecturer"),              # no Scholar, catch-less
    _row("dir1", "Della Director", "First Year CS Education, Director"),  # -> Faculty
]


def test_by_rank_chair_first_dean_in_professor_group():
    groups = rank.by_rank(_FIXTURE)
    assert groups[0]["label"] == "Department Chair"                    # Chair heads
    prof = next(g for g in groups if g["label"] == "Professor")
    assert {m["slug"] for m in prof["members"]} == {"dean1", "prof_b"}  # Dean folded into Professor
    # Professor group members sorted by surname: Dean(D) before Prof(P)
    assert [m["slug"] for m in prof["members"]] == ["dean1", "prof_b"]
    assert groups[-1]["label"] == "Faculty"                            # catch-all last


def test_by_rank_slug_tiebreak_within_group():
    assoc = next(g for g in rank.by_rank(_FIXTURE) if g["label"] == "Associate Professor")
    # both surname "Li" -> tie broken by full name then slug: Ann Li (li1) before Jing Li (li2)
    assert [m["slug"] for m in assoc["members"]] == ["li1", "li2"]


def test_by_citations_ranked_then_tail():
    lst = rank.by_citations(_FIXTURE)
    ranked = [r for r in lst if r["rank_num"] is not None]
    tail = [r for r in lst if r["rank_num"] is None]
    # ranked in citation-desc order, contiguous 1..N
    assert [r["slug"] for r in ranked] == ["dean1", "chair1", "prof_b", "li1", "asst1"]
    assert [r["rank_num"] for r in ranked] == [1, 2, 3, 4, 5]
    # no-Scholar tail A–Z by FULL name, all rank_num None, sorted after ranked
    assert [r["slug"] for r in tail] == ["dir1", "li2", "lect1"]       # Della/Jing/Leo by name
    assert lst[-1]["rank_num"] is None                                 # None never crashes, sorts last


def test_by_name_surname_order():
    lst = rank.by_name(_FIXTURE)
    assert [r["slug"] for r in lst] == [
        "asst1", "chair1", "dean1", "dir1", "lect1", "li1", "li2", "prof_b",
    ]  # Assistant, Chair, Dean, Director, Lecturer, Li, Li, Prof — surname A–Z, slug breaks Li tie


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
