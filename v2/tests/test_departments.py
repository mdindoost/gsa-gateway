"""Department registry — the pipeline is reusable across departments via config."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2.core.ingestion.departments import DEPARTMENTS, get


def test_known_departments_have_required_config():
    for key, d in DEPARTMENTS.items():
        assert d.key == key
        assert d.name and d.faculty_list.startswith("http")
        assert d.default_org_id > 0
        assert d.discovery in {"static", "js"}


def test_get_is_case_insensitive():
    assert get("CS").default_org_id == 5
    assert get("cs").name == "Computer Science"


def test_ds_is_registered_but_flagged_js():
    ds = get("ds")
    assert ds.default_org_id == 6 and ds.discovery == "js" and ds.note


def test_unknown_department_exits_with_message():
    with pytest.raises(SystemExit) as e:
        get("astrophysics")
    assert "unknown department" in str(e.value)
