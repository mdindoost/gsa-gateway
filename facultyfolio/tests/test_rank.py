from facultyfolio import rank, config


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
