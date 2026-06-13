"""Tests for the department registry helper used by the 'Refresh NJIT KB' button."""

from v2.core.ingestion.departments import DEPARTMENTS, supported


def test_supported_returns_only_static_and_verified_departments():
    keys = {d.key for d in supported()}
    assert keys == {k for k, d in DEPARTMENTS.items()
                    if d.discovery == "static" and d.verified}


def test_supported_is_cs_only_today():
    keys = {d.key for d in supported()}
    assert "cs" in keys                 # verified by a real crawl
    assert "ds" not in keys             # JS-rendered — not statically discoverable
    assert "informatics" not in keys    # static but unverified (never crawled)
