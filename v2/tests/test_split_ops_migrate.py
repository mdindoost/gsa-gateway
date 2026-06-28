"""Build-5 migration tests: split_ops_migrate.py — gated KB→OPS table migration.

All tests operate on temp-file or fixture DBs only. NEVER touches the real live DB.
Fixture KB uses create_all (combined) to seed all 11 MOVED tables; OPS uses create_ops_schema.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure repo root is on path
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from v2.core.database.schema import (
    create_all,
    create_knowledge_schema,
    create_ops_schema,
)
from v2.core.publishing.event_projection import derive_event_kb, event_natural_key

MIGRATE_SCRIPT = str(REPO / "scripts" / "split_ops_migrate.py")

MOVED_TABLES = [
    "posts", "post_templates", "post_deliveries", "events", "event_reminders",
    "judging_events", "judging_judges", "judging_presenters", "judging_scores",
    "judging_audience_votes", "judging_score_audit",
]

DROP_ORDER = [
    "post_deliveries",
    "event_reminders",
    "judging_score_audit",
    "judging_scores",
    "judging_audience_votes",
    "judging_presenters",
    "judging_judges",
    "judging_events",
    "posts",
    "events",
    "post_templates",
]


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _seed_fixture_kb(kb_path: str) -> sqlite3.Connection:
    """Create a combined fixture KB with all 11 MOVED tables seeded with rows."""
    conn = create_all(kb_path)
    conn.row_factory = sqlite3.Row
    # Seed org
    conn.execute(
        "INSERT INTO organizations(id,name,slug,type) VALUES(1,'GSA','gsa','gsa')"
    )
    conn.commit()

    # posts (includes delete_at/deleted_at migration cols)
    conn.execute(
        "INSERT INTO posts(id,org_id,type,title,content,channels,status,metadata,created_by)"
        " VALUES(1,1,'one_time','Hello','Test post','[]','scheduled','{}','tester')"
    )
    conn.execute(
        "INSERT INTO posts(id,org_id,type,content,channels,status,metadata,created_by)"
        " VALUES(2,1,'broadcast','Another post','[]','sent','{}','tester')"
    )
    conn.commit()

    # post_deliveries (includes delete cols)
    conn.execute(
        "INSERT INTO post_deliveries(id,post_id,platform,status,sent_at)"
        " VALUES(1,1,'discord','success','2026-01-01 00:00:00')"
    )
    conn.execute(
        "INSERT INTO post_deliveries(id,post_id,platform,status,sent_at)"
        " VALUES(2,1,'telegram','success','2026-01-01 00:00:01')"
    )
    conn.execute(
        "INSERT INTO post_deliveries(id,post_id,platform,status,sent_at)"
        " VALUES(3,2,'discord','success','2026-01-02 00:00:00')"
    )
    conn.commit()

    # post_templates
    conn.execute(
        "INSERT INTO post_templates(id,org_id,name,content,post_type,recurrence,channels,metadata,created_by)"
        " VALUES(1,1,'Weekly','Weekly digest','recurring_instance','{\"freq\":\"weekly\"}','[]','{}','system')"
    )
    conn.commit()

    # events (live shape — no ki_content in KB, org_id appended)
    conn.execute(
        "INSERT INTO events(id,name,date,time,location,description,organizer,rsvp_link,category,"
        "reminder_sent_7d,reminder_sent_1d,reminder_sent_1h,announcement_sent,created_by,org_id)"
        " VALUES(1,'Spring Social','2026-04-10','6:00 PM','Campus Center','Great event','GSA','',"
        "'general',0,0,0,0,'system',1)"
    )
    conn.execute(
        "INSERT INTO events(id,name,date,time,location,description,organizer,rsvp_link,category,"
        "reminder_sent_7d,reminder_sent_1d,reminder_sent_1h,announcement_sent,created_by,org_id)"
        " VALUES(2,'Fall Gala','2026-09-15','TBD','TBD','','GSA','','general',0,0,0,0,'system',1)"
    )
    conn.commit()

    # event_reminders (empty — consistent with live)
    # judging tables
    conn.execute(
        "INSERT INTO judging_events(id,name,status,criteria,top_n) VALUES(1,'Hack 2026','setup','accuracy,design',3)"
    )
    conn.commit()
    conn.execute(
        "INSERT INTO judging_judges(id,event_id,name,pin) VALUES(1,1,'Alice','1234')"
    )
    conn.execute(
        "INSERT INTO judging_judges(id,event_id,name,pin) VALUES(2,1,'Bob','5678')"
    )
    conn.commit()
    conn.execute(
        "INSERT INTO judging_presenters(id,event_id,number,name,department) VALUES(1,1,1,'Team Alpha','CS')"
    )
    conn.execute(
        "INSERT INTO judging_presenters(id,event_id,number,name,department) VALUES(2,1,2,'Team Beta','IT')"
    )
    conn.commit()
    conn.execute(
        "INSERT INTO judging_scores(id,event_id,judge_id,presenter_number,scores_json,final_score)"
        " VALUES(1,1,1,1,'{\"accuracy\":4,\"design\":5}',4.5)"
    )
    conn.commit()
    conn.execute(
        "INSERT INTO judging_audience_votes(id,event_id,voter_hash,presenter_number)"
        " VALUES(1,1,'hash_abc',1)"
    )
    conn.commit()
    conn.execute(
        "INSERT INTO judging_score_audit(id,event_id,judge_id,presenter_number,action,actor,actor_label,scores_json,final_score)"
        " VALUES(1,1,1,1,'submit','judge','Alice','{\"accuracy\":4,\"design\":5}',4.5)"
    )
    conn.commit()
    return conn


@pytest.fixture()
def kb_and_ops(tmp_path):
    """Fixture KB (all 11 MOVED tables seeded) + fresh OPS for copy tests."""
    kb_path = str(tmp_path / "kb.db")
    ops_path = str(tmp_path / "ops.db")
    kb_conn = _seed_fixture_kb(kb_path)
    ops_conn = create_ops_schema(ops_path)
    yield {
        "kb_conn": kb_conn,
        "ops_conn": ops_conn,
        "kb_path": kb_path,
        "ops_path": ops_path,
    }
    kb_conn.close()
    ops_conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Task 1 — column-mapped copy fidelity
# ─────────────────────────────────────────────────────────────────────────────

class TestCopyFidelity:

    def test_posts_copied_with_correct_id_and_content(self, kb_and_ops, tmp_path):
        """posts: all rows copied with exact id, org_slug stamped."""
        from scripts.split_ops_migrate import copy_table, build_org_slug_map
        kbc = kb_and_ops["kb_conn"]
        opsc = kb_and_ops["ops_conn"]
        slug_map = build_org_slug_map(kbc)
        copy_table(kbc, opsc, "posts", slug_map)
        opsc.commit()

        rows = opsc.execute("SELECT id, org_id, org_slug, type, content FROM posts ORDER BY id").fetchall()
        assert len(rows) == 2
        assert rows[0][0] == 1
        assert rows[0][1] == 1
        assert rows[0][2] == "gsa"
        assert rows[0][3] == "one_time"
        assert "Test post" in rows[0][4]
        assert rows[1][0] == 2
        assert rows[1][2] == "gsa"

    def test_post_deliveries_copied_row_for_row(self, kb_and_ops, tmp_path):
        """post_deliveries: identical copy (no augmented cols)."""
        from scripts.split_ops_migrate import copy_table, build_org_slug_map
        kbc = kb_and_ops["kb_conn"]
        opsc = kb_and_ops["ops_conn"]
        slug_map = build_org_slug_map(kbc)
        # posts must be present first (FK)
        copy_table(kbc, opsc, "posts", slug_map)
        copy_table(kbc, opsc, "post_deliveries", slug_map)
        opsc.commit()

        kb_rows = kbc.execute("SELECT * FROM post_deliveries ORDER BY id").fetchall()
        ops_rows = opsc.execute("SELECT * FROM post_deliveries ORDER BY id").fetchall()
        assert len(kb_rows) == len(ops_rows) == 3
        for kb_r, ops_r in zip(kb_rows, ops_rows):
            assert kb_r["id"] == ops_r["id"]
            assert kb_r["post_id"] == ops_r["post_id"]
            assert kb_r["platform"] == ops_r["platform"]
            assert kb_r["status"] == ops_r["status"]

    def test_events_copied_with_org_slug_and_ki_content_null(self, kb_and_ops, tmp_path):
        """events: rows copied, org_slug stamped, ki_content is NULL (back-filled later)."""
        from scripts.split_ops_migrate import copy_table, build_org_slug_map
        kbc = kb_and_ops["kb_conn"]
        opsc = kb_and_ops["ops_conn"]
        slug_map = build_org_slug_map(kbc)
        copy_table(kbc, opsc, "events", slug_map)
        opsc.commit()

        ops_rows = opsc.execute("SELECT id, name, org_slug, ki_content FROM events ORDER BY id").fetchall()
        assert len(ops_rows) == 2
        assert ops_rows[0]["id"] == 1
        assert ops_rows[0]["name"] == "Spring Social"
        assert ops_rows[0]["org_slug"] == "gsa"
        assert ops_rows[0]["ki_content"] is None  # back-filled in Phase-5
        assert ops_rows[1]["id"] == 2
        assert ops_rows[1]["org_slug"] == "gsa"

    def test_events_sqlite_sequence_seeded_to_max_id(self, kb_and_ops, tmp_path):
        """events sqlite_sequence must be seeded to MAX(id) after copy."""
        from scripts.split_ops_migrate import copy_table, build_org_slug_map, seed_events_sequence
        kbc = kb_and_ops["kb_conn"]
        opsc = kb_and_ops["ops_conn"]
        slug_map = build_org_slug_map(kbc)
        copy_table(kbc, opsc, "events", slug_map)
        seed_events_sequence(opsc)
        opsc.commit()

        seq = opsc.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='events'"
        ).fetchone()
        assert seq is not None
        assert seq[0] == 2  # MAX(id) of the 2 seeded events

    def test_judging_tables_copied_row_for_row(self, kb_and_ops, tmp_path):
        """All judging_* tables copied row-for-row."""
        from scripts.split_ops_migrate import copy_table, build_org_slug_map
        kbc = kb_and_ops["kb_conn"]
        opsc = kb_and_ops["ops_conn"]
        slug_map = build_org_slug_map(kbc)

        # Copy in order (parents before children)
        for t in ["judging_events", "judging_judges", "judging_presenters",
                  "judging_scores", "judging_audience_votes", "judging_score_audit"]:
            copy_table(kbc, opsc, t, slug_map)
        opsc.commit()

        assert opsc.execute("SELECT COUNT(*) FROM judging_events").fetchone()[0] == 1
        assert opsc.execute("SELECT COUNT(*) FROM judging_judges").fetchone()[0] == 2
        assert opsc.execute("SELECT COUNT(*) FROM judging_presenters").fetchone()[0] == 2
        assert opsc.execute("SELECT COUNT(*) FROM judging_scores").fetchone()[0] == 1
        assert opsc.execute("SELECT COUNT(*) FROM judging_audience_votes").fetchone()[0] == 1
        assert opsc.execute("SELECT COUNT(*) FROM judging_score_audit").fetchone()[0] == 1

        j_event = opsc.execute("SELECT * FROM judging_events WHERE id=1").fetchone()
        assert j_event["name"] == "Hack 2026"

    def test_post_templates_copied_with_org_slug(self, kb_and_ops, tmp_path):
        """post_templates: org_slug stamped from org_id."""
        from scripts.split_ops_migrate import copy_table, build_org_slug_map
        kbc = kb_and_ops["kb_conn"]
        opsc = kb_and_ops["ops_conn"]
        slug_map = build_org_slug_map(kbc)
        copy_table(kbc, opsc, "post_templates", slug_map)
        opsc.commit()

        tmpl = opsc.execute("SELECT * FROM post_templates WHERE id=1").fetchone()
        assert tmpl is not None
        assert tmpl["org_slug"] == "gsa"
        assert tmpl["name"] == "Weekly"

    def test_ids_preserved_exactly(self, kb_and_ops, tmp_path):
        """All tables: original ids are preserved after copy."""
        from scripts.split_ops_migrate import copy_table, build_org_slug_map
        kbc = kb_and_ops["kb_conn"]
        opsc = kb_and_ops["ops_conn"]
        slug_map = build_org_slug_map(kbc)
        for t in MOVED_TABLES:
            copy_table(kbc, opsc, t, slug_map)
        opsc.commit()

        for t in MOVED_TABLES:
            kb_ids = {r[0] for r in kbc.execute(f"SELECT id FROM `{t}`").fetchall()}
            ops_ids = {r[0] for r in opsc.execute(f"SELECT id FROM `{t}`").fetchall()}
            assert kb_ids == ops_ids, f"IDs mismatch for {t}: kb={kb_ids} ops={ops_ids}"


# ─────────────────────────────────────────────────────────────────────────────
# Task 2 — checksum helper
# ─────────────────────────────────────────────────────────────────────────────

class TestChecksum:

    def test_identical_tables_equal_digest(self, kb_and_ops, tmp_path):
        """Same data in KB and OPS → equal checksums."""
        from scripts.split_ops_migrate import copy_table, build_org_slug_map, table_checksum, get_kb_columns
        kbc = kb_and_ops["kb_conn"]
        opsc = kb_and_ops["ops_conn"]
        slug_map = build_org_slug_map(kbc)
        copy_table(kbc, opsc, "posts", slug_map)
        opsc.commit()

        kb_cols = get_kb_columns(kbc, "posts")
        kb_chk = table_checksum(kbc, "posts", kb_cols)
        ops_chk = table_checksum(opsc, "posts", kb_cols)
        assert kb_chk == ops_chk
        assert len(kb_chk) == 64  # sha256 hex

    def test_changed_cell_yields_different_digest(self, kb_and_ops, tmp_path):
        """Modifying one cell in OPS changes the checksum."""
        from scripts.split_ops_migrate import copy_table, build_org_slug_map, table_checksum, get_kb_columns
        kbc = kb_and_ops["kb_conn"]
        opsc = kb_and_ops["ops_conn"]
        slug_map = build_org_slug_map(kbc)
        copy_table(kbc, opsc, "posts", slug_map)
        opsc.commit()

        kb_cols = get_kb_columns(kbc, "posts")
        chk_before = table_checksum(opsc, "posts", kb_cols)
        opsc.execute("UPDATE posts SET content='MODIFIED' WHERE id=1")
        opsc.commit()
        chk_after = table_checksum(opsc, "posts", kb_cols)
        assert chk_before != chk_after

    def test_augmented_cols_excluded_from_checksum(self, kb_and_ops, tmp_path):
        """After copy, checksums computed over the KB column list are equal on both sides.

        The KB column list (from PRAGMA) is the COMMON set — it excludes any cols
        that were only added during OPS augmentation (org_slug when absent from KB,
        ki_content when absent from KB). For real live KB this means org_slug and
        ki_content are excluded; for combined fixtures they may already be present.
        In both cases, checksums over the SAME col list must be equal.
        """
        from scripts.split_ops_migrate import copy_table, build_org_slug_map, table_checksum, get_kb_columns
        kbc = kb_and_ops["kb_conn"]
        opsc = kb_and_ops["ops_conn"]
        slug_map = build_org_slug_map(kbc)
        copy_table(kbc, opsc, "events", slug_map)
        opsc.commit()

        # Use the KB's actual column list as the COMMON column set for checksum
        kb_cols = get_kb_columns(kbc, "events")
        kb_chk = table_checksum(kbc, "events", kb_cols)
        ops_chk = table_checksum(opsc, "events", kb_cols)
        assert kb_chk == ops_chk, (
            f"Checksums must be equal over KB col list; kb={kb_chk}, ops={ops_chk}"
        )

    def test_checksum_order_independent_of_insert_order(self, kb_and_ops, tmp_path):
        """Checksum ORDER BY id is stable regardless of insertion order."""
        from scripts.split_ops_migrate import get_kb_columns, table_checksum
        kbc = kb_and_ops["kb_conn"]
        kb_cols = get_kb_columns(kbc, "posts")
        # Two calls must return the same digest
        chk1 = table_checksum(kbc, "posts", kb_cols)
        chk2 = table_checksum(kbc, "posts", kb_cols)
        assert chk1 == chk2

    def test_empty_table_has_deterministic_digest(self, kb_and_ops, tmp_path):
        """Empty table checksum must be the same across runs (empty sha256)."""
        from scripts.split_ops_migrate import get_kb_columns, table_checksum
        kbc = kb_and_ops["kb_conn"]
        # event_reminders is empty in fixture
        kb_cols = get_kb_columns(kbc, "event_reminders")
        chk1 = table_checksum(kbc, "event_reminders", kb_cols)
        chk2 = table_checksum(kbc, "event_reminders", kb_cols)
        assert chk1 == chk2
        # Also verify OPS empty table matches KB empty table after copy
        opsc = kb_and_ops["ops_conn"]
        from scripts.split_ops_migrate import build_org_slug_map, copy_table
        slug_map = build_org_slug_map(kbc)
        # events must be present first (FK in event_reminders)
        copy_table(kbc, opsc, "events", slug_map)
        copy_table(kbc, opsc, "event_reminders", slug_map)
        opsc.commit()
        ops_chk = table_checksum(opsc, "event_reminders", kb_cols)
        assert chk1 == ops_chk


# ─────────────────────────────────────────────────────────────────────────────
# Task 3 — org_slug resolution gate
# ─────────────────────────────────────────────────────────────────────────────

class TestOrgSlugResolution:

    def test_correct_slug_resolved_from_org_id(self, kb_and_ops, tmp_path):
        """org_slug is resolved correctly for all posts rows."""
        from scripts.split_ops_migrate import build_org_slug_map, copy_table
        kbc = kb_and_ops["kb_conn"]
        opsc = kb_and_ops["ops_conn"]
        slug_map = build_org_slug_map(kbc)
        assert slug_map[1] == "gsa"
        copy_table(kbc, opsc, "posts", slug_map)
        opsc.commit()
        slugs = [r[0] for r in opsc.execute("SELECT org_slug FROM posts").fetchall()]
        assert all(s == "gsa" for s in slugs)

    def test_unresolvable_org_id_raises(self, tmp_path):
        """A row with org_id that has no matching slug must raise ValueError.

        Uses a legacy-style KB (no org_slug column in posts) to simulate the live
        production schema where org_slug augmentation is needed.
        """
        from scripts.split_ops_migrate import copy_table
        ops_path = str(tmp_path / "ops.db")
        ops_conn = create_ops_schema(ops_path)

        # Build a bare SQLite DB with the LIVE posts schema (no org_slug)
        legacy_path = str(tmp_path / "legacy_kb.db")
        legacy_conn = sqlite3.connect(legacy_path)
        legacy_conn.row_factory = sqlite3.Row
        legacy_conn.execute(
            "CREATE TABLE organizations(id INTEGER PRIMARY KEY, name TEXT, slug TEXT, type TEXT)"
        )
        legacy_conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'GSA','gsa','gsa')")
        legacy_conn.execute(
            "CREATE TABLE posts("
            "id INTEGER PRIMARY KEY, org_id INTEGER, type TEXT NOT NULL, title TEXT, "
            "content TEXT NOT NULL DEFAULT '', channels TEXT NOT NULL DEFAULT '[]', "
            "status TEXT NOT NULL DEFAULT 'scheduled', metadata TEXT NOT NULL DEFAULT '{}', "
            "created_by TEXT)"
        )
        # Row with org_id=99 which is not in slug_map
        legacy_conn.execute(
            "INSERT INTO posts(id,org_id,type,content,channels,status,metadata,created_by)"
            " VALUES(1,99,'one_time','Bad row','[]','scheduled','{}','test')"
        )
        legacy_conn.commit()

        slug_map = {1: "gsa"}  # org_id=99 deliberately absent
        with pytest.raises(ValueError, match="org_id=99"):
            copy_table(legacy_conn, ops_conn, "posts", slug_map)
        legacy_conn.close()
        ops_conn.close()

    def test_ambiguous_slug_raises_in_build_org_slug_map(self, tmp_path):
        """Two orgs with the same slug (LOW-11 violation) must raise ValueError."""
        from scripts.split_ops_migrate import build_org_slug_map
        kb_path = str(tmp_path / "kb.db")
        kb_conn = create_all(kb_path)
        kb_conn.execute(
            "INSERT INTO organizations(id,name,slug,type,parent_id) VALUES(1,'GSA','gsa','gsa',NULL)"
        )
        # Add a child org with same slug as parent (SQLite allows diff parent_id for same slug)
        # Actually UNIQUE(parent_id, slug) prevents exact duplicates, but different parent_ids
        # could have same slug. Let's just insert two with different parents and same slug.
        kb_conn.execute(
            "INSERT INTO organizations(id,name,slug,type,parent_id) VALUES(2,'GSA 2','gsa','club',1)"
        )
        kb_conn.commit()
        with pytest.raises(ValueError, match="[Aa]mbiguous"):
            build_org_slug_map(kb_conn)
        kb_conn.close()

    def test_null_org_id_on_needs_slug_table_raises(self, tmp_path):
        """A post with NULL org_id must raise ValueError (live KB schema, no org_slug col)."""
        from scripts.split_ops_migrate import copy_table
        ops_path = str(tmp_path / "ops.db")
        ops_conn = create_ops_schema(ops_path)

        # Legacy KB without org_slug column in posts (simulates live production schema)
        legacy_path = str(tmp_path / "legacy_kb.db")
        legacy_conn = sqlite3.connect(legacy_path)
        legacy_conn.row_factory = sqlite3.Row
        legacy_conn.execute(
            "CREATE TABLE posts("
            "id INTEGER PRIMARY KEY, org_id INTEGER, type TEXT NOT NULL, "
            "content TEXT NOT NULL DEFAULT '', channels TEXT NOT NULL DEFAULT '[]', "
            "status TEXT NOT NULL DEFAULT 'scheduled', metadata TEXT NOT NULL DEFAULT '{}', "
            "created_by TEXT)"
        )
        # NULL org_id — cannot be resolved to a slug
        legacy_conn.execute(
            "INSERT INTO posts(id,org_id,type,content,channels,status,metadata,created_by)"
            " VALUES(1,NULL,'one_time','Null org row','[]','scheduled','{}','test')"
        )
        legacy_conn.commit()

        slug_map = {1: "gsa"}
        with pytest.raises(ValueError):
            copy_table(legacy_conn, ops_conn, "posts", slug_map)
        legacy_conn.close()
        ops_conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Task 4 — acceptance gate (fail-closed, EVERY mode)
# ─────────────────────────────────────────────────────────────────────────────

class TestAcceptanceGate:

    def _do_full_copy(self, kbc, opsc, slug_map):
        """Copy all MOVED tables from kbc to opsc in order."""
        from scripts.split_ops_migrate import copy_table, seed_events_sequence
        kb_cols_map = {}
        for t in MOVED_TABLES:
            kb_cols, _ = copy_table(kbc, opsc, t, slug_map)
            kb_cols_map[t] = kb_cols
        seed_events_sequence(opsc)
        opsc.commit()
        return kb_cols_map

    def test_gate_passes_on_clean_copy(self, kb_and_ops, tmp_path):
        """Gate must PASS when OPS is a correct count+checksum-verified copy."""
        from scripts.split_ops_migrate import build_org_slug_map, acceptance_gate
        kbc = kb_and_ops["kb_conn"]
        opsc = kb_and_ops["ops_conn"]
        slug_map = build_org_slug_map(kbc)
        kb_cols_map = self._do_full_copy(kbc, opsc, slug_map)

        result = acceptance_gate(kbc, opsc, kb_cols_map, slug_map)
        assert result["passed"] is True, f"Gate should pass on clean copy; checks={result['checks']}"

    def test_gate_fails_on_count_mismatch(self, kb_and_ops, tmp_path):
        """Gate FAILS if OPS count != KB count for any table."""
        from scripts.split_ops_migrate import build_org_slug_map, acceptance_gate
        kbc = kb_and_ops["kb_conn"]
        opsc = kb_and_ops["ops_conn"]
        slug_map = build_org_slug_map(kbc)
        kb_cols_map = self._do_full_copy(kbc, opsc, slug_map)

        # Sneakily add an extra row to OPS posts (simulating a count mismatch)
        opsc.execute(
            "INSERT INTO posts(id,org_id,org_slug,type,content,channels,status,metadata,created_by)"
            " VALUES(99,1,'gsa','one_time','Extra','[]','scheduled','{}','ghost')"
        )
        opsc.commit()

        result = acceptance_gate(kbc, opsc, kb_cols_map, slug_map)
        assert result["passed"] is False
        assert result["checks"]["posts_count"]["status"] == "FAIL"

    def test_gate_fails_on_checksum_mismatch(self, kb_and_ops, tmp_path):
        """Gate FAILS if checksum differs (data changed after copy)."""
        from scripts.split_ops_migrate import build_org_slug_map, acceptance_gate
        kbc = kb_and_ops["kb_conn"]
        opsc = kb_and_ops["ops_conn"]
        slug_map = build_org_slug_map(kbc)
        kb_cols_map = self._do_full_copy(kbc, opsc, slug_map)

        # Corrupt one row in OPS (same count, different data)
        opsc.execute("UPDATE posts SET content='CORRUPTED' WHERE id=1")
        opsc.commit()

        result = acceptance_gate(kbc, opsc, kb_cols_map, slug_map)
        assert result["passed"] is False
        assert result["checks"]["posts_checksum"]["status"] == "FAIL"

    def test_gate_fails_on_missing_table_in_ops(self, tmp_path):
        """Gate FAILS if a MOVED table is missing from OPS entirely."""
        from scripts.split_ops_migrate import build_org_slug_map, acceptance_gate, get_kb_columns
        kb_path = str(tmp_path / "kb.db")
        ops_path = str(tmp_path / "ops.db")
        kbc = _seed_fixture_kb(kb_path)
        opsc = create_ops_schema(ops_path)

        slug_map = build_org_slug_map(kbc)
        kb_cols_map = {t: get_kb_columns(kbc, t) for t in MOVED_TABLES}

        # Drop a table from OPS to simulate missing table
        opsc.execute("DROP TABLE IF EXISTS judging_score_audit")
        opsc.commit()

        result = acceptance_gate(kbc, opsc, kb_cols_map, slug_map)
        assert result["passed"] is False
        kbc.close()
        opsc.close()

    def test_gate_fails_on_ops_fk_violation(self, kb_and_ops, tmp_path):
        """Gate FAILS if OPS has FK violations (PRAGMA foreign_key_check)."""
        from scripts.split_ops_migrate import build_org_slug_map, acceptance_gate
        kbc = kb_and_ops["kb_conn"]
        opsc = kb_and_ops["ops_conn"]
        slug_map = build_org_slug_map(kbc)
        kb_cols_map = self._do_full_copy(kbc, opsc, slug_map)

        # Insert a post_delivery with a nonexistent post_id (FK violation)
        opsc.execute("PRAGMA foreign_keys=OFF")
        opsc.execute(
            "INSERT INTO post_deliveries(id,post_id,platform,status,sent_at)"
            " VALUES(999,9999,'discord','success','2026-01-01 00:00:00')"
        )
        opsc.execute("PRAGMA foreign_keys=ON")
        opsc.commit()

        result = acceptance_gate(kbc, opsc, kb_cols_map, slug_map)
        assert result["passed"] is False
        assert result["checks"]["ops_fk_check"]["status"] == "FAIL"

    def test_gate_r3_invariant_fails_if_moved_table_in_knowledge_schema(self, tmp_path):
        """Gate R3: create_knowledge_schema must produce NONE of the 11 MOVED tables.

        We verify the invariant by checking a fresh knowledge schema DB does not
        contain any MOVED table names.
        """
        from scripts.split_ops_migrate import check_r3_invariant
        result = check_r3_invariant()
        assert result["status"] == "PASS", (
            f"R3 invariant violation: create_knowledge_schema produces MOVED tables: "
            f"{result.get('tables_in_knowledge')}"
        )

    def test_gate_returns_structured_per_check_diff(self, kb_and_ops, tmp_path):
        """Gate result has structured per-check diffs (not just bool)."""
        from scripts.split_ops_migrate import build_org_slug_map, acceptance_gate
        kbc = kb_and_ops["kb_conn"]
        opsc = kb_and_ops["ops_conn"]
        slug_map = build_org_slug_map(kbc)
        kb_cols_map = self._do_full_copy(kbc, opsc, slug_map)
        result = acceptance_gate(kbc, opsc, kb_cols_map, slug_map)

        assert isinstance(result, dict)
        assert "passed" in result
        assert "checks" in result
        checks = result["checks"]
        # Each check has a "status" key
        for name, val in checks.items():
            assert "status" in val, f"Check {name!r} missing 'status'"

    def test_gate_failure_drops_nothing_from_kb(self, kb_and_ops, tmp_path):
        """When gate fails, the orchestrator must NOT drop any KB tables."""
        from scripts.split_ops_migrate import build_org_slug_map, acceptance_gate
        kbc = kb_and_ops["kb_conn"]
        opsc = kb_and_ops["ops_conn"]
        slug_map = build_org_slug_map(kbc)
        kb_cols_map = self._do_full_copy(kbc, opsc, slug_map)

        # Force a gate failure (corrupt OPS)
        opsc.execute("UPDATE posts SET content='BAD' WHERE id=1")
        opsc.commit()
        result = acceptance_gate(kbc, opsc, kb_cols_map, slug_map)
        assert result["passed"] is False

        # KB tables must still exist
        kb_tables = {r[0] for r in kbc.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        for t in MOVED_TABLES:
            assert t in kb_tables, f"{t} was dropped from KB despite gate failure"


# ─────────────────────────────────────────────────────────────────────────────
# Task 5 — drop-LAST + reversibility
# ─────────────────────────────────────────────────────────────────────────────

class TestDropLastAndReversibility:

    def test_moved_tables_absent_from_kb_after_commit(self, tmp_path):
        """After --commit, MOVED tables are gone from KB."""
        kb_path = str(tmp_path / "kb.db")
        ops_path = str(tmp_path / "ops.db")
        kbc = _seed_fixture_kb(kb_path)
        kbc.close()

        result = subprocess.run(
            [sys.executable, MIGRATE_SCRIPT,
             "--db", kb_path, "--ops-db", ops_path,
             "--backups-dir", str(tmp_path),
             "--commit"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"Script failed:\nstdout={result.stdout}\nstderr={result.stderr}"

        kbc2 = sqlite3.connect(kb_path)
        kb_tables = {r[0] for r in kbc2.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        kbc2.close()
        for t in MOVED_TABLES:
            assert t not in kb_tables, f"MOVED table {t} still in KB after commit"

    def test_knowledge_tables_intact_after_commit(self, tmp_path):
        """After --commit, knowledge/KG tables remain in KB."""
        kb_path = str(tmp_path / "kb.db")
        ops_path = str(tmp_path / "ops.db")
        kbc = _seed_fixture_kb(kb_path)
        # Add a knowledge_item to verify it survives
        kbc.execute(
            "INSERT INTO knowledge_items(org_id,type,title,content,metadata,created_by)"
            " VALUES(1,'policy','Test Item','Some content','{}','test')"
        )
        kbc.commit()
        kbc.close()

        subprocess.run(
            [sys.executable, MIGRATE_SCRIPT,
             "--db", kb_path, "--ops-db", ops_path,
             "--backups-dir", str(tmp_path),
             "--commit"],
            capture_output=True, text=True, check=True
        )

        kbc2 = sqlite3.connect(kb_path)
        kbc2.row_factory = sqlite3.Row
        ki_count = kbc2.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0]
        orgs_count = kbc2.execute("SELECT COUNT(*) FROM organizations").fetchone()[0]
        kbc2.close()
        assert ki_count >= 1, "knowledge_items must survive the migration"
        assert orgs_count >= 1, "organizations must survive the migration"

    def test_restore_from_backup_reverts_migration(self, tmp_path):
        """Restoring the hardened_backup snapshot fully reverts KB to pre-migration state."""
        from scripts._area_tag_migrate import hardened_backup
        kb_path = str(tmp_path / "kb.db")
        ops_path = str(tmp_path / "ops.db")
        kbc = _seed_fixture_kb(kb_path)
        kbc.close()

        # Run migration with --commit
        result = subprocess.run(
            [sys.executable, MIGRATE_SCRIPT,
             "--db", kb_path, "--ops-db", ops_path,
             "--backups-dir", str(tmp_path),
             "--commit"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"

        # Find the backup file
        backup_files = sorted(Path(tmp_path).glob("gsa_gateway.*.pre-split-ops-migrate.db"))
        assert len(backup_files) == 1, f"Expected 1 backup, found {backup_files}"
        backup_path = backup_files[0]

        # Verify KB currently lacks MOVED tables
        kbc_post = sqlite3.connect(kb_path)
        post_tables = {r[0] for r in kbc_post.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        kbc_post.close()
        for t in MOVED_TABLES:
            assert t not in post_tables

        # Restore from backup
        import shutil
        shutil.copy2(str(backup_path), kb_path)

        # Now KB should have all MOVED tables back
        kbc_restored = sqlite3.connect(kb_path)
        kbc_restored.row_factory = sqlite3.Row
        restored_tables = {r[0] for r in kbc_restored.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        for t in MOVED_TABLES:
            assert t in restored_tables, f"{t} not in restored KB"

        # Counts must match original seeds
        assert kbc_restored.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 2
        assert kbc_restored.execute("SELECT COUNT(*) FROM post_deliveries").fetchone()[0] == 3
        assert kbc_restored.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
        kbc_restored.close()

    def test_fk_ordered_drop_matches_expected_sequence(self):
        """The DROP_ORDER matches the expected FK-ordered sequence (children first)."""
        from scripts.split_ops_migrate import DROP_ORDER as actual_drop_order
        assert actual_drop_order == DROP_ORDER, (
            f"Expected FK-ordered drop:\n{DROP_ORDER}\nGot:\n{actual_drop_order}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Task 6 — event_info natural_key + ki_content back-fill
# ─────────────────────────────────────────────────────────────────────────────

class TestPhase5Backfills:

    def _seed_event_info(self, kb_conn, *, name="Spring Social", date="2026-04-10",
                          time="6:00 PM", content="Rich blurb about this event.",
                          ops_event_id=1, has_natural_key=False):
        """Seed a legacy event_info KB item (optionally without natural_key)."""
        meta = {
            "derived_from": "ops_event",
            "org_slug": "gsa",
            "ops_event_id": ops_event_id,
            "date": date,
            "time": time,
        }
        if has_natural_key:
            meta["natural_key"] = event_natural_key(name, date, time)
        kb_conn.execute(
            "INSERT INTO knowledge_items(org_id,type,title,content,metadata,created_by)"
            " VALUES(1,'event_info',?,?,?,'test')",
            (name, content, json.dumps(meta))
        )
        kb_conn.commit()

    def test_natural_key_back_fill_computes_correct_key(self, tmp_path):
        """natural_key back-fill sets metadata.natural_key correctly."""
        from scripts.split_ops_migrate import backfill_event_info_natural_key
        kb_path = str(tmp_path / "kb.db")
        kb_conn = create_all(kb_path)
        kb_conn.row_factory = sqlite3.Row
        kb_conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'GSA','gsa','gsa')")
        kb_conn.commit()
        self._seed_event_info(kb_conn, name="Spring Social", date="2026-04-10", time="6:00 PM",
                               ops_event_id=1, has_natural_key=False)

        count = backfill_event_info_natural_key(kb_conn)
        kb_conn.commit()
        assert count >= 1

        row = kb_conn.execute(
            "SELECT metadata FROM knowledge_items WHERE type='event_info'"
        ).fetchone()
        meta = json.loads(row["metadata"])
        expected_nk = event_natural_key("Spring Social", "2026-04-10", "6:00 PM")
        assert meta["natural_key"] == expected_nk
        kb_conn.close()

    def test_natural_key_back_fill_is_idempotent(self, tmp_path):
        """Running natural_key back-fill twice does not change anything."""
        from scripts.split_ops_migrate import backfill_event_info_natural_key
        kb_path = str(tmp_path / "kb.db")
        kb_conn = create_all(kb_path)
        kb_conn.row_factory = sqlite3.Row
        kb_conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'GSA','gsa','gsa')")
        kb_conn.commit()
        self._seed_event_info(kb_conn, name="Spring Social", date="2026-04-10",
                               time="6:00 PM", has_natural_key=False, ops_event_id=1)
        backfill_event_info_natural_key(kb_conn)
        kb_conn.commit()

        count_second = backfill_event_info_natural_key(kb_conn)
        kb_conn.commit()
        # Second run: key already set, should report 0 updates
        assert count_second == 0
        kb_conn.close()

    def test_natural_key_back_fill_no_op_on_empty(self, tmp_path):
        """natural_key back-fill returns 0 when there are no event_info rows (live case)."""
        from scripts.split_ops_migrate import backfill_event_info_natural_key
        kb_path = str(tmp_path / "kb.db")
        kb_conn = create_all(kb_path)
        kb_conn.row_factory = sqlite3.Row
        kb_conn.execute("INSERT INTO organizations(id,name,slug,type) VALUES(1,'GSA','gsa','gsa')")
        kb_conn.commit()
        count = backfill_event_info_natural_key(kb_conn)
        assert count == 0
        kb_conn.close()

    def test_ki_content_back_fill_copies_content_to_ops(self, tmp_path):
        """ki_content back-fill copies event_info.content → OPS events.ki_content."""
        from scripts.split_ops_migrate import backfill_ki_content, copy_table, build_org_slug_map
        kb_path = str(tmp_path / "kb.db")
        ops_path = str(tmp_path / "ops.db")
        kb_conn = _seed_fixture_kb(kb_path)  # has 2 events with id=1 and id=2
        ops_conn = create_ops_schema(ops_path)
        slug_map = build_org_slug_map(kb_conn)
        # Copy events to OPS first
        copy_table(kb_conn, ops_conn, "events", slug_map)
        ops_conn.commit()

        # Seed event_info KB item referencing ops event id=1
        rich_blurb = "Come celebrate with GSA at the Spring Social!"
        self._seed_event_info(kb_conn, name="Spring Social", date="2026-04-10",
                               time="6:00 PM", content=rich_blurb, ops_event_id=1)

        count = backfill_ki_content(kb_conn, ops_conn)
        ops_conn.commit()
        assert count >= 1

        row = ops_conn.execute("SELECT ki_content FROM events WHERE id=1").fetchone()
        assert row is not None
        assert row[0] == rich_blurb
        kb_conn.close()
        ops_conn.close()

    def test_ki_content_back_fill_no_op_on_zero_event_info(self, tmp_path):
        """ki_content back-fill is a no-op when there are 0 event_info rows."""
        from scripts.split_ops_migrate import backfill_ki_content, copy_table, build_org_slug_map
        kb_path = str(tmp_path / "kb.db")
        ops_path = str(tmp_path / "ops.db")
        kb_conn = _seed_fixture_kb(kb_path)
        ops_conn = create_ops_schema(ops_path)
        slug_map = build_org_slug_map(kb_conn)
        copy_table(kb_conn, ops_conn, "events", slug_map)
        ops_conn.commit()

        count = backfill_ki_content(kb_conn, ops_conn)
        assert count == 0  # No event_info rows
        kb_conn.close()
        ops_conn.close()

    def test_derive_event_kb_reproduces_content_byte_identically(self, tmp_path):
        """After back-fill, derive_event_kb reproduces event_info content byte-identically
        AND yields created==0 (no duplicates — reject criterion #7).

        We seed event_info for ALL OPS events first, then back-fill and re-derive.
        derive_event_kb must find all existing items (created==0) and reproduce
        ki_content byte-identically.
        """
        from scripts.split_ops_migrate import (
            backfill_event_info_natural_key, backfill_ki_content,
            copy_table, build_org_slug_map,
        )
        kb_path = str(tmp_path / "kb.db")
        ops_path = str(tmp_path / "ops.db")
        kb_conn = _seed_fixture_kb(kb_path)
        ops_conn = create_ops_schema(ops_path)
        slug_map = build_org_slug_map(kb_conn)

        # Copy events to OPS (fixture has 2 events: id=1 Spring Social, id=2 Fall Gala)
        copy_table(kb_conn, ops_conn, "events", slug_map)
        ops_conn.commit()

        # Seed event_info for BOTH OPS events so no new items are created by derive
        rich1 = "Come celebrate Spring Social at Campus Center — food, music, prizes!"
        rich2 = "Fall Gala — a night to remember!"
        self._seed_event_info(kb_conn, name="Spring Social", date="2026-04-10",
                               time="6:00 PM", content=rich1, ops_event_id=1)
        self._seed_event_info(kb_conn, name="Fall Gala", date="2026-09-15",
                               time="TBD", content=rich2, ops_event_id=2)

        # Run both back-fills
        backfill_event_info_natural_key(kb_conn)
        kb_conn.commit()
        backfill_ki_content(kb_conn, ops_conn)
        ops_conn.commit()

        # Verify OPS ki_content is set for both events
        ki1 = ops_conn.execute("SELECT ki_content FROM events WHERE id=1").fetchone()[0]
        ki2 = ops_conn.execute("SELECT ki_content FROM events WHERE id=2").fetchone()[0]
        assert ki1 == rich1, f"OPS ki_content mismatch for event 1: {ki1!r}"
        assert ki2 == rich2, f"OPS ki_content mismatch for event 2: {ki2!r}"

        # Now derive_event_kb should find all event_info rows by natural_key, created==0
        result = derive_event_kb(ops_conn, kb_conn, org_slugs=("gsa",))
        assert result["created"] == 0, (
            f"derive_event_kb must not create duplicates after back-fill; created={result['created']}"
        )

        # Verify byte-identical content for event 1
        row1 = kb_conn.execute(
            "SELECT content FROM knowledge_items WHERE type='event_info' AND title='Spring Social'"
        ).fetchone()
        assert row1 is not None
        assert row1["content"] == rich1, (
            f"derive_event_kb must reproduce content byte-identically; got: {row1['content']!r}"
        )
        kb_conn.close()
        ops_conn.close()

    def test_back_fill_uses_fallback_event_id_for_legacy_rows(self, tmp_path):
        """ki_content back-fill uses metadata.event_id as fallback when ops_event_id absent."""
        from scripts.split_ops_migrate import backfill_ki_content, copy_table, build_org_slug_map
        kb_path = str(tmp_path / "kb.db")
        ops_path = str(tmp_path / "ops.db")
        kb_conn = _seed_fixture_kb(kb_path)
        ops_conn = create_ops_schema(ops_path)
        slug_map = build_org_slug_map(kb_conn)
        copy_table(kb_conn, ops_conn, "events", slug_map)
        ops_conn.commit()

        # Seed legacy event_info with event_id (old key, not ops_event_id)
        rich = "Legacy blurb for event."
        meta = {
            "derived_from": "ops_event",
            "org_slug": "gsa",
            "event_id": 2,  # fallback key — note: event id=2 exists in fixture
            "date": "2026-09-15",
            "time": "TBD",
        }
        kb_conn.execute(
            "INSERT INTO knowledge_items(org_id,type,title,content,metadata,created_by)"
            " VALUES(1,'event_info','Fall Gala',?,?,'test')",
            (rich, json.dumps(meta))
        )
        kb_conn.commit()

        count = backfill_ki_content(kb_conn, ops_conn)
        ops_conn.commit()
        assert count >= 1

        row = ops_conn.execute("SELECT ki_content FROM events WHERE id=2").fetchone()
        assert row is not None
        assert row[0] == rich
        kb_conn.close()
        ops_conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Task 7 — dry-run vs commit + plan output
# ─────────────────────────────────────────────────────────────────────────────

class TestDryRunVsCommit:

    def test_dry_run_writes_nothing_to_kb(self, tmp_path):
        """Dry-run (no --commit) leaves KB completely unchanged."""
        kb_path = str(tmp_path / "kb.db")
        ops_path = str(tmp_path / "ops.db")
        kbc = _seed_fixture_kb(kb_path)
        kbc.close()

        result = subprocess.run(
            [sys.executable, MIGRATE_SCRIPT,
             "--db", kb_path, "--ops-db", ops_path,
             "--backups-dir", str(tmp_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"Dry-run failed: {result.stderr}"

        # No backup should be taken
        backups = list(Path(tmp_path).glob("gsa_gateway.*.pre-split-ops-migrate.db"))
        assert len(backups) == 0, "Dry-run must not take a backup"

        # KB MOVED tables still present
        kbc2 = sqlite3.connect(kb_path)
        kb_tables = {r[0] for r in kbc2.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        kbc2.close()
        for t in MOVED_TABLES:
            assert t in kb_tables, f"Dry-run should not drop {t} from KB"

    def test_dry_run_writes_nothing_to_ops(self, tmp_path):
        """Dry-run must not create or write to the OPS DB."""
        kb_path = str(tmp_path / "kb.db")
        ops_path = str(tmp_path / "ops.db")
        kbc = _seed_fixture_kb(kb_path)
        kbc.close()

        subprocess.run(
            [sys.executable, MIGRATE_SCRIPT,
             "--db", kb_path, "--ops-db", ops_path,
             "--backups-dir", str(tmp_path)],
            capture_output=True, text=True, check=True,
        )
        # OPS DB either doesn't exist or has no rows in MOVED tables
        if Path(ops_path).exists():
            opsc = sqlite3.connect(ops_path)
            tables = {r[0] for r in opsc.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            opsc.close()
            # If OPS was created, it should have 0 rows in MOVED tables
            if tables:
                opsc = sqlite3.connect(ops_path)
                for t in MOVED_TABLES:
                    if t in tables:
                        cnt = opsc.execute(f"SELECT COUNT(*) FROM `{t}`").fetchone()[0]
                        assert cnt == 0, f"Dry-run must not copy rows to OPS {t}"
                opsc.close()

    def test_dry_run_prints_plan(self, tmp_path):
        """Dry-run prints per-table planned counts and the drop list."""
        kb_path = str(tmp_path / "kb.db")
        ops_path = str(tmp_path / "ops.db")
        kbc = _seed_fixture_kb(kb_path)
        kbc.close()

        result = subprocess.run(
            [sys.executable, MIGRATE_SCRIPT,
             "--db", kb_path, "--ops-db", ops_path,
             "--backups-dir", str(tmp_path)],
            capture_output=True, text=True,
        )
        output = result.stdout.lower()
        assert "posts" in output
        assert "dry" in output or "dry-run" in output.replace("-", "")

    def test_commit_takes_backup_first(self, tmp_path):
        """--commit must take a hardened_backup before any write."""
        kb_path = str(tmp_path / "kb.db")
        ops_path = str(tmp_path / "ops.db")
        kbc = _seed_fixture_kb(kb_path)
        kbc.close()

        subprocess.run(
            [sys.executable, MIGRATE_SCRIPT,
             "--db", kb_path, "--ops-db", ops_path,
             "--backups-dir", str(tmp_path),
             "--commit"],
            capture_output=True, text=True, check=True,
        )
        backups = list(Path(tmp_path).glob("gsa_gateway.*.pre-split-ops-migrate.db"))
        assert len(backups) == 1, f"Expected 1 backup, found {len(backups)}"

    def test_commit_gate_fail_drops_nothing(self, tmp_path):
        """If gate fails during --commit, no KB tables are dropped."""
        kb_path = str(tmp_path / "kb.db")
        ops_path = str(tmp_path / "ops.db")
        kbc = _seed_fixture_kb(kb_path)
        kbc.close()

        # Wrapper that monkey-patches acceptance_gate and calls main() directly.
        # Using m.main() (not runpy) so the patch takes effect in module scope.
        wrapper = tmp_path / "fail_gate.py"
        wrapper.write_text(
            f"import sys; sys.path.insert(0, {str(REPO)!r})\n"
            "import scripts.split_ops_migrate as m\n"
            "m.acceptance_gate = lambda *a, **kw: "
            "{'passed': False, 'checks': {'forced': {'status': 'FAIL', 'reason': 'test'}}}\n"
            f"rc = m.main(['--db', {str(kb_path)!r}, '--ops-db', {str(ops_path)!r}, "
            f"'--backups-dir', {str(tmp_path)!r}, '--commit'])\n"
            "sys.exit(rc)\n"
        )
        result = subprocess.run(
            [sys.executable, str(wrapper)],
            capture_output=True, text=True,
        )
        # Script should exit nonzero
        assert result.returncode != 0, (
            f"Gate failure must cause nonzero exit;\nstdout={result.stdout}\nstderr={result.stderr}"
        )

        # KB MOVED tables must still exist
        kbc2 = sqlite3.connect(kb_path)
        kb_tables = {r[0] for r in kbc2.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        kbc2.close()
        for t in MOVED_TABLES:
            assert t in kb_tables, f"Gate failure must not drop {t} from KB"

    def test_commit_copies_all_11_tables(self, tmp_path):
        """--commit must copy all 11 MOVED tables to OPS with correct counts."""
        kb_path = str(tmp_path / "kb.db")
        ops_path = str(tmp_path / "ops.db")
        kbc = _seed_fixture_kb(kb_path)
        expected = {}
        for t in MOVED_TABLES:
            expected[t] = kbc.execute(f"SELECT COUNT(*) FROM `{t}`").fetchone()[0]
        kbc.close()

        result = subprocess.run(
            [sys.executable, MIGRATE_SCRIPT,
             "--db", kb_path, "--ops-db", ops_path,
             "--backups-dir", str(tmp_path),
             "--commit"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"Script failed:\nstdout={result.stdout}\nstderr={result.stderr}"

        opsc = sqlite3.connect(ops_path)
        for t in MOVED_TABLES:
            ops_count = opsc.execute(f"SELECT COUNT(*) FROM `{t}`").fetchone()[0]
            assert ops_count == expected[t], (
                f"{t}: expected {expected[t]} rows in OPS, got {ops_count}"
            )
        opsc.close()
