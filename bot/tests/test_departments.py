"""Tests for the department registry helper used by the 'Refresh NJIT KB' button."""

from v2.core.ingestion.departments import DEPARTMENTS, supported


def test_supported_equals_the_verified_set():
    keys = {d.key for d in supported()}
    assert keys == {k for k, d in DEPARTMENTS.items() if d.verified}


def test_supported_includes_a_verified_js_department(monkeypatch):
    # verified is the gate; discovery method (static/js) is just a dispatch detail.
    from v2.core.ingestion import departments as dm
    from v2.core.ingestion.departments import Department
    fake = {
        "cs": dm.DEPARTMENTS["cs"],
        "js1": Department(key="js1", name="JS Dept", faculty_list="https://x/p",
                          default_org_id=99, discovery="js", verified=True),
        "un": Department(key="un", name="Unverified", faculty_list="https://y/p",
                         default_org_id=98, discovery="static", verified=False),
    }
    monkeypatch.setattr(dm, "DEPARTMENTS", fake)
    assert {d.key for d in dm.supported()} == {"cs", "js1"}


def test_supported_is_cs_only_today():
    keys = {d.key for d in supported()}
    assert "cs" in keys                 # verified by a real crawl
    assert "ds" not in keys             # verified=False — not yet validated
    assert "informatics" not in keys    # verified=False — never crawled
