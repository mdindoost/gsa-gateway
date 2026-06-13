"""Tests for the --all (all-departments) mode of scripts/ingest_faculty.py.

Crawling/parsing themselves are not unit-tested (no network); these cover the
arg-parser guards (via subprocess, which fail before any network) and the
orchestration of `_run_all` with discover/parse/commit/backup monkeypatched —
the blocker fixes from senior review: one backup, per-department org fallback,
0-profiles = failure, continue-past-error, and the exit code.
"""

import importlib.util
import pathlib
import subprocess
import sys
from types import SimpleNamespace

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]


def _load_ingest():
    p = REPO / "scripts" / "ingest_faculty.py"
    spec = importlib.util.spec_from_file_location("ingest_faculty_mod", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _args(**over):
    base = dict(all=True, commit=True, db="g.db", org_id=None,
                changes_log="log", overview=False, web=False, department=None)
    base.update(over)
    return SimpleNamespace(**base)


# ── arg-parser guards (subprocess; fail before network) ───────────────────────

def test_all_with_department_is_rejected():
    r = subprocess.run(
        [sys.executable, "scripts/ingest_faculty.py", "--all", "--department", "cs"],
        capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode != 0
    assert "--all" in (r.stderr + r.stdout)


def test_all_and_limit_are_mutually_exclusive():
    r = subprocess.run(
        [sys.executable, "scripts/ingest_faculty.py", "--all", "--limit", "5"],
        capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 2  # argparse mutually-exclusive-group error


# ── _run_all orchestration ────────────────────────────────────────────────────

def test_run_all_takes_one_backup_and_commits_per_department(monkeypatch):
    mod = _load_ingest()
    backups, commits = [], []
    monkeypatch.setattr(mod, "discover", lambda limit, fl: ["http://x/profile/a"])
    monkeypatch.setattr(mod, "_parse_profiles", lambda urls, a: ([("rec", "items")], 1))
    monkeypatch.setattr(mod, "_auto_backup", lambda db, **k: backups.append(db))
    monkeypatch.setattr(
        mod, "commit",
        lambda parsed, db, org, log, default_org_id=None, backup=True:
            commits.append((default_org_id, backup)))

    rc = mod._run_all(_args())

    assert rc == 0
    assert backups == ["g.db"]                       # exactly one backup for the batch
    assert all(b is False for (_, b) in commits)     # per-dept commits skip the backup
    # each supported department committed with its OWN org fallback
    from v2.core.ingestion.departments import supported
    expected = {d.default_org_id for d in supported()}
    assert {org for (org, _) in commits} == expected


def test_run_all_zero_profiles_is_a_failure(monkeypatch):
    mod = _load_ingest()
    commits = []
    monkeypatch.setattr(mod, "discover", lambda limit, fl: [])  # nobody found
    monkeypatch.setattr(mod, "_auto_backup", lambda db, **k: None)
    monkeypatch.setattr(
        mod, "commit",
        lambda *a, **k: commits.append(a))

    rc = mod._run_all(_args())

    assert rc == 1            # a supported dept with 0 profiles fails the run
    assert commits == []      # nothing committed


def test_run_all_continues_past_a_failing_department(monkeypatch):
    mod = _load_ingest()
    committed_orgs = []

    # Inject two supported departments so "continue past a failure" is exercised
    # regardless of how many are verified in the real registry.
    from v2.core.ingestion import departments as deptmod
    from v2.core.ingestion.departments import Department
    a = Department(key="a", name="Dept A", faculty_list="https://a.njit.edu/faculty",
                   default_org_id=5, discovery="static", verified=True)
    b = Department(key="b", name="Dept B", faculty_list="https://b.njit.edu/faculty",
                   default_org_id=7, discovery="static", verified=True)
    monkeypatch.setattr(deptmod, "supported", lambda: [a, b])

    def disc(limit, fl):
        if "://a." in fl:           # Dept A discovery errors
            raise RuntimeError("boom")
        return ["http://x/profile/a"]

    monkeypatch.setattr(mod, "discover", disc)
    monkeypatch.setattr(mod, "_parse_profiles", lambda urls, ar: ([("rec", "items")], 1))
    monkeypatch.setattr(mod, "_auto_backup", lambda db, **k: None)
    monkeypatch.setattr(
        mod, "commit",
        lambda parsed, db, org, log, default_org_id=None, backup=True:
            committed_orgs.append(default_org_id))

    rc = mod._run_all(_args())

    assert rc == 1                  # Dept A failed → non-zero overall
    assert committed_orgs == [7]    # but Dept B still committed (with its own org)
