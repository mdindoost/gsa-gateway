"""GSA Gateway v2.0 — database schema.

Single source of truth for every v2 table. Creating the schema is additive and
idempotent: it only ever creates NEW v2 tables and never touches v1 tables. The
v1 ``ALTER TABLE ... ADD COLUMN org_id`` work lives in the migration script
(Step 2), not here, so this module can build a clean standalone database.

Design decisions (approved 2026-06-08):
  * New v2 tables are STRICT (SQLite 3.37+). v1 tables stay flexibly typed.
    STRICT primary keys use plain ``INTEGER PRIMARY KEY`` (rowid alias,
    auto-incrementing) rather than ``AUTOINCREMENT`` — avoids version quirks and
    is the recommended form for STRICT tables.
  * Vectors live in a native ``sqlite-vec`` vec0 virtual table. Bot only.
  * BM25 keyword search uses FTS5 over a generated ``search_text`` column, kept
    in sync by AFTER INSERT/UPDATE/DELETE triggers. Works in the WASM dashboard.
  * Knowledge versioning: ``root_id`` groups all versions of a logical item;
    ``parent_id`` points at the immediate previous version. A trigger sets
    ``root_id = id`` for originals; the app sets it to the parent's root_id for
    revisions.
  * Idempotent migrations are tracked in ``schema_migrations``.

Timestamps are UTC text (``datetime('now')`` -> ``YYYY-MM-DD HH:MM:SS``), matching
v1 conventions.
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = "001_initial"

try:  # sqlite-vec is required for the vec0 virtual table.
    import sqlite_vec
except ImportError:  # pragma: no cover - surfaced clearly at runtime
    sqlite_vec = None


# ─────────────────────────────────────────────────────────────────────────────
# Group A — core tables (STRICT)
# ─────────────────────────────────────────────────────────────────────────────

ORGANIZATIONS = """
CREATE TABLE IF NOT EXISTS organizations (
    id          INTEGER PRIMARY KEY,
    parent_id   INTEGER REFERENCES organizations(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL,
    slug        TEXT    NOT NULL,
    type        TEXT    NOT NULL,              -- university|gsa|council|college|department|
                                               -- lab|club|person|office|custom (open set)
    description TEXT,
    metadata    TEXT    NOT NULL DEFAULT '{}', -- JSON
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(parent_id, slug)
) STRICT;
"""

KNOWLEDGE_ITEMS = """
CREATE TABLE IF NOT EXISTS knowledge_items (
    id          INTEGER PRIMARY KEY,
    org_id      INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    type        TEXT    NOT NULL,              -- faq|policy|contact|resource|event_info|
                                               -- announcement|custom (open set)
    title       TEXT,
    content     TEXT    NOT NULL,              -- PLAIN TEXT, never markdown
    -- Combined field FTS5 indexes; STORED so external-content rebuild can read it.
    search_text TEXT    GENERATED ALWAYS AS (COALESCE(title, '') || ' ' || content) STORED,
    metadata    TEXT    NOT NULL DEFAULT '{}', -- JSON (email/phone/url/category…)
    version     INTEGER NOT NULL DEFAULT 1,
    root_id     INTEGER REFERENCES knowledge_items(id),  -- groups all versions; =id for originals
    parent_id   INTEGER REFERENCES knowledge_items(id),  -- previous version (NULL = original)
    source_url  TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1,    -- 1 = current version
    created_by  TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT
) STRICT;
"""

POSTS = """
CREATE TABLE IF NOT EXISTS posts (
    id              INTEGER PRIMARY KEY,
    org_id          INTEGER NOT NULL REFERENCES organizations(id),
    type            TEXT    NOT NULL,          -- one_time|recurring_instance|event_announcement|
                                               -- event_reminder|mathcafe|worldcup|broadcast|digest
    title           TEXT,
    content         TEXT    NOT NULL,
    channels        TEXT    NOT NULL DEFAULT '[]',  -- JSON: ["discord","telegram"]
    discord_channel TEXT,
    scheduled_for   TEXT,                      -- UTC; NULL = send asap
    sent_at         TEXT,
    status          TEXT    NOT NULL DEFAULT 'scheduled'
                    CHECK (status IN ('scheduled','sending','sent','failed','cancelled')),
    source_type     TEXT,                      -- template|event_reminder|manual|mathcafe|worldcup…
    source_id       INTEGER,                   -- e.g. post_templates.id / events.id
    signature       TEXT,                      -- NULL = use org default signature
    metadata        TEXT    NOT NULL DEFAULT '{}',
    created_by      TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
) STRICT;
"""

POST_TEMPLATES = """
CREATE TABLE IF NOT EXISTS post_templates (
    id              INTEGER PRIMARY KEY,
    org_id          INTEGER NOT NULL REFERENCES organizations(id),
    name            TEXT    NOT NULL,
    content         TEXT    NOT NULL,
    post_type       TEXT    NOT NULL DEFAULT 'recurring_instance', -- type stamped on emitted posts
    recurrence      TEXT    NOT NULL,          -- JSON: {freq, interval, days_of_week, time, start, end}
    channels        TEXT    NOT NULL DEFAULT '[]',
    discord_channel TEXT,
    signature       TEXT,
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_run_at     TEXT,
    next_run_at     TEXT,
    metadata        TEXT    NOT NULL DEFAULT '{}',
    created_by      TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
) STRICT;
"""

POST_DELIVERIES = """
CREATE TABLE IF NOT EXISTS post_deliveries (
    id         INTEGER PRIMARY KEY,
    post_id    INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    platform   TEXT    NOT NULL,              -- discord|telegram|…
    channel    TEXT,                          -- per-platform: discord=channel NAME, telegram=resolved
                                               -- chat_id, groupme=setting (used as-is by that platform's
                                               -- delete_message; the deleter never interprets it)
    message_id TEXT,                          -- platform message id (resend/audit/unsend)
    status     TEXT    NOT NULL CHECK (status IN ('success','failed','skipped')),
    error      TEXT,
    sent_at    TEXT    NOT NULL DEFAULT (datetime('now'))
) STRICT;
"""

# v1-column-compatible `events` so v2 is self-sufficient on GREENFIELD deployments
# (other universities with no v1). Matches the exact column layout v1 creates, plus
# org_id. On the NJIT migration path the v1 table already exists, so IF NOT EXISTS
# is a harmless no-op and there is ZERO divergence between NJIT and greenfield — all
# existing code (scheduler, migrate_events) works on both. A cleaner
# start_datetime/tags schema is deferred until v1 is retired (it cannot change the
# live table without breaking the running v1 bot, which reads date/time/category).
EVENTS = """
CREATE TABLE IF NOT EXISTS events (
    id               INTEGER PRIMARY KEY,
    org_id           INTEGER REFERENCES organizations(id),
    name             TEXT    NOT NULL,
    date             TEXT    NOT NULL,
    time             TEXT    NOT NULL DEFAULT 'TBD',
    location         TEXT    NOT NULL DEFAULT 'TBD',
    description      TEXT    NOT NULL DEFAULT '',
    organizer        TEXT    NOT NULL DEFAULT 'GSA',
    rsvp_link        TEXT    NOT NULL DEFAULT '',
    category         TEXT    NOT NULL DEFAULT 'general',
    reminder_sent_7d INTEGER NOT NULL DEFAULT 0,
    reminder_sent_1d INTEGER NOT NULL DEFAULT 0,
    reminder_sent_1h INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    created_by       TEXT    NOT NULL DEFAULT 'system'
) STRICT;
"""

EVENT_REMINDERS = """
CREATE TABLE IF NOT EXISTS event_reminders (
    id           INTEGER PRIMARY KEY,
    event_id     INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    offset_value INTEGER NOT NULL,
    offset_unit  TEXT    NOT NULL CHECK (offset_unit IN ('minutes','hours','days','weeks')),
    channels     TEXT    NOT NULL DEFAULT '[]',  -- JSON
    template     TEXT,                         -- custom message override
    enabled      INTEGER NOT NULL DEFAULT 1,
    post_id      INTEGER REFERENCES posts(id), -- NULL until the reminder fires
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
) STRICT;
"""

SETTINGS = """
CREATE TABLE IF NOT EXISTS settings (
    id          INTEGER PRIMARY KEY,
    org_id      INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    key         TEXT    NOT NULL,
    value       TEXT,                          -- always text; `type` says how to parse
    type        TEXT    NOT NULL DEFAULT 'string'
                CHECK (type IN ('string','int','bool','json')),
    description TEXT,
    updated_by  TEXT,
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(org_id, key)
) STRICT;
"""

SCHEMA_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
) STRICT;
"""

# ─────────────────────────────────────────────────────────────────────────────
# Group B — search tables (virtual; cannot be STRICT)
# ─────────────────────────────────────────────────────────────────────────────

KNOWLEDGE_VECTORS = """
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_vectors USING vec0(
    item_id   INTEGER PRIMARY KEY,            -- = knowledge_items.id
    embedding FLOAT[768]                      -- nomic-embed-text
);
"""

KNOWLEDGE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    search_text,
    content='knowledge_items',
    content_rowid='id'
);
"""

# ─────────────────────────────────────────────────────────────────────────────
# Group C — knowledge graph (STRICT)
# ─────────────────────────────────────────────────────────────────────────────

RAW_PAGES = """
CREATE TABLE IF NOT EXISTS raw_pages (
    url          TEXT PRIMARY KEY,
    content      TEXT NOT NULL,
    struct_hash  TEXT NOT NULL,
    status       TEXT NOT NULL,
    fetched_at   TEXT NOT NULL DEFAULT (datetime('now'))
) STRICT;
"""

NODES = """
CREATE TABLE IF NOT EXISTS nodes (
    id               INTEGER PRIMARY KEY,
    type             TEXT NOT NULL,
    key              TEXT NOT NULL,
    name             TEXT NOT NULL,
    attrs            TEXT NOT NULL DEFAULT '{}',
    source           TEXT NOT NULL,
    source_doc_id    INTEGER,
    ontology_version INTEGER NOT NULL DEFAULT 1,
    is_active        INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
) STRICT;
"""

EDGES = """
CREATE TABLE IF NOT EXISTS edges (
    id               INTEGER PRIMARY KEY,
    src_id           INTEGER NOT NULL REFERENCES nodes(id),
    type             TEXT NOT NULL,
    dst_id           INTEGER NOT NULL REFERENCES nodes(id),
    category         TEXT,
    area_source      TEXT,
    source_section   TEXT,
    attrs            TEXT NOT NULL DEFAULT '{}',
    source           TEXT NOT NULL,
    source_doc_id    INTEGER,
    ontology_version INTEGER NOT NULL DEFAULT 1,
    is_active        INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (category IS NULL OR category IN
           ('faculty','staff','admin','advisor','joint','emeritus','officer','deprep'))
) STRICT;
"""

# Phase 1b — the explore() engine: per-node pending next-steps (the frontier) and the
# many-to-many link from a raw page to the node(s) it informs.
FRONTIER = """
CREATE TABLE IF NOT EXISTS frontier (
    id               INTEGER PRIMARY KEY,
    from_node_id     INTEGER REFERENCES nodes(id),   -- NULL for a root entry point
    url              TEXT NOT NULL,
    aspect           TEXT NOT NULL DEFAULT 'people',
    status           TEXT NOT NULL DEFAULT 'pending',
    error            TEXT,                          -- failure reason when status='error'
    depth_discovered INTEGER NOT NULL DEFAULT 0,
    discovered_at    TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (status IN ('pending','fetched','error'))
) STRICT;
"""

CRAWL_ENTRY_POINTS = """
CREATE TABLE IF NOT EXISTS crawl_entry_points (
    id              INTEGER PRIMARY KEY,
    url             TEXT    NOT NULL UNIQUE,
    scope_prefix    TEXT    NOT NULL DEFAULT '',
    aspect          TEXT    NOT NULL DEFAULT 'office',
    org_slug        TEXT,
    parent_slug     TEXT,
    org_type        TEXT    NOT NULL DEFAULT 'office',
    status          TEXT    NOT NULL DEFAULT 'candidate',
    source          TEXT    NOT NULL DEFAULT 'discovered',
    discovered_from_url TEXT,
    last_crawled_at TEXT,
    crawl_interval_days INTEGER,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
) STRICT;
"""

OFFICE_PAGE_STATE = """
CREATE TABLE IF NOT EXISTS office_page_state (
    url             TEXT    PRIMARY KEY,
    entry_point_id  INTEGER,
    content_hash    TEXT    NOT NULL,
    last_seen_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
) STRICT;
"""

PAGE_NODES = """
CREATE TABLE IF NOT EXISTS page_nodes (
    raw_url   TEXT NOT NULL REFERENCES raw_pages(url),
    node_id   INTEGER NOT NULL REFERENCES nodes(id),
    PRIMARY KEY (raw_url, node_id)
) STRICT;
"""

# ─────────────────────────────────────────────────────────────────────────────
# Group D — judging system (STRICT)
# ─────────────────────────────────────────────────────────────────────────────

JUDGING_EVENTS = """
CREATE TABLE IF NOT EXISTS judging_events (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'setup'
                CHECK (status IN ('setup', 'open', 'closed')),
    criteria    TEXT NOT NULL,
    top_n       INTEGER NOT NULL DEFAULT 3,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
) STRICT;
"""

JUDGING_JUDGES = """
CREATE TABLE IF NOT EXISTS judging_judges (
    id               INTEGER PRIMARY KEY,
    event_id         INTEGER NOT NULL REFERENCES judging_events(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    pin              TEXT NOT NULL,
    telegram_id_hash TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(event_id, pin)
) STRICT;
"""

JUDGING_PRESENTERS = """
CREATE TABLE IF NOT EXISTS judging_presenters (
    id          INTEGER PRIMARY KEY,
    event_id    INTEGER NOT NULL REFERENCES judging_events(id) ON DELETE CASCADE,
    number      INTEGER NOT NULL,
    name        TEXT NOT NULL,
    department  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(event_id, number)
) STRICT;
"""

JUDGING_SCORES = """
CREATE TABLE IF NOT EXISTS judging_scores (
    id               INTEGER PRIMARY KEY,
    event_id         INTEGER NOT NULL REFERENCES judging_events(id) ON DELETE CASCADE,
    judge_id         INTEGER NOT NULL REFERENCES judging_judges(id) ON DELETE CASCADE,
    presenter_number INTEGER NOT NULL,
    scores_json      TEXT NOT NULL,
    final_score      REAL NOT NULL,
    submitted_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(event_id, judge_id, presenter_number),
    FOREIGN KEY (event_id, presenter_number)
        REFERENCES judging_presenters(event_id, number) ON DELETE CASCADE
) STRICT;
"""

JUDGING_AUDIENCE_VOTES = """
CREATE TABLE IF NOT EXISTS judging_audience_votes (
    id               INTEGER PRIMARY KEY,
    event_id         INTEGER NOT NULL REFERENCES judging_events(id) ON DELETE CASCADE,
    voter_hash       TEXT NOT NULL,
    presenter_number INTEGER NOT NULL,
    voted_at         TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(event_id, voter_hash)
) STRICT;
"""

# Append-only audit log of EVERY score mutation (judge submit / admin enter / admin
# edit / admin delete). judging_scores keeps only the current value; this is the
# permanent trail "in case needed" — written in the SAME transaction as the mutation
# so the two can never disagree. No FK to judging_presenters on purpose: the audit
# must survive a presenter delete. actor_label carries NO Telegram IDs (judge name/id
# or 'admin' only) — consistent with the hash-before-write rule.
JUDGING_SCORE_AUDIT = """
CREATE TABLE IF NOT EXISTS judging_score_audit (
    id               INTEGER PRIMARY KEY,
    event_id         INTEGER NOT NULL REFERENCES judging_events(id) ON DELETE CASCADE,
    judge_id         INTEGER NOT NULL REFERENCES judging_judges(id) ON DELETE CASCADE,
    presenter_number INTEGER NOT NULL,
    action           TEXT NOT NULL
                       CHECK (action IN ('submit','admin_enter','admin_edit','admin_delete')),
    actor            TEXT NOT NULL CHECK (actor IN ('judge','admin')),
    actor_label      TEXT NOT NULL DEFAULT '',
    scores_json      TEXT,            -- new state after the mutation; NULL for a delete
    final_score      REAL,            -- mean, mirrors judging_scores.final_score; NULL for delete
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
) STRICT;
"""

# ─────────────────────────────────────────────────────────────────────────────
# Indexes
# ─────────────────────────────────────────────────────────────────────────────

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_org_parent   ON organizations(parent_id);",
    "CREATE INDEX IF NOT EXISTS idx_org_type     ON organizations(type);",
    "CREATE INDEX IF NOT EXISTS idx_org_slug     ON organizations(slug);",
    "CREATE INDEX IF NOT EXISTS idx_ki_org       ON knowledge_items(org_id);",
    "CREATE INDEX IF NOT EXISTS idx_ki_type      ON knowledge_items(type);",
    "CREATE INDEX IF NOT EXISTS idx_ki_active    ON knowledge_items(is_active);",
    "CREATE INDEX IF NOT EXISTS idx_ki_retrieval ON knowledge_items(org_id, type, is_active);",
    "CREATE INDEX IF NOT EXISTS idx_ki_root      ON knowledge_items(root_id, version);",
    "CREATE INDEX IF NOT EXISTS idx_ki_parent    ON knowledge_items(parent_id);",
    "CREATE INDEX IF NOT EXISTS idx_ki_natural_key ON knowledge_items(json_extract(metadata,'$.natural_key'));",
    "CREATE INDEX IF NOT EXISTS idx_posts_due    ON posts(status, scheduled_for);",
    "CREATE INDEX IF NOT EXISTS idx_posts_org    ON posts(org_id);",
    "CREATE INDEX IF NOT EXISTS idx_posts_type   ON posts(type);",
    "CREATE INDEX IF NOT EXISTS idx_posts_source ON posts(source_type, source_id);",
    "CREATE INDEX IF NOT EXISTS idx_tmpl_due     ON post_templates(enabled, next_run_at);",
    "CREATE INDEX IF NOT EXISTS idx_deliv_post     ON post_deliveries(post_id);",
    "CREATE INDEX IF NOT EXISTS idx_deliv_platform ON post_deliveries(platform, status);",
    "CREATE INDEX IF NOT EXISTS idx_events_org   ON events(org_id);",
    "CREATE INDEX IF NOT EXISTS idx_events_date  ON events(date);",
    "CREATE INDEX IF NOT EXISTS idx_remind_event ON event_reminders(event_id);",
    "CREATE INDEX IF NOT EXISTS idx_remind_due   ON event_reminders(enabled, post_id);",
    "CREATE INDEX IF NOT EXISTS idx_settings_org ON settings(org_id);",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_nodes_key   ON nodes(type, key);",
    "CREATE INDEX        IF NOT EXISTS idx_nodes_type  ON nodes(type, is_active);",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_triple ON edges(src_id, type, dst_id);",
    "CREATE INDEX        IF NOT EXISTS idx_edges_src   ON edges(src_id, is_active);",
    "CREATE INDEX        IF NOT EXISTS idx_edges_dst   ON edges(dst_id, type, is_active);",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_frontier_uniq ON frontier(from_node_id, url);",
    # NULL from_node_id (root entry points): SQLite treats NULLs as distinct in the
    # composite unique index, so dedup those by url with a partial unique index.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_frontier_root ON frontier(url) WHERE from_node_id IS NULL;",
    "CREATE INDEX        IF NOT EXISTS idx_frontier_status ON frontier(status);",
    "CREATE INDEX        IF NOT EXISTS idx_page_nodes_node ON page_nodes(node_id);",
    "CREATE INDEX IF NOT EXISTS idx_jscores_event      ON judging_scores(event_id);",
    "CREATE INDEX IF NOT EXISTS idx_jscores_presenter  ON judging_scores(event_id, presenter_number);",
    "CREATE INDEX IF NOT EXISTS idx_judges_event       ON judging_judges(event_id);",
    "CREATE INDEX IF NOT EXISTS idx_jpresenters_event  ON judging_presenters(event_id);",
    "CREATE INDEX IF NOT EXISTS idx_jvotes_event       ON judging_audience_votes(event_id);",
    "CREATE INDEX IF NOT EXISTS idx_jvotes_presenter   ON judging_audience_votes(event_id, presenter_number);",
    "CREATE INDEX IF NOT EXISTS idx_jaudit_event       ON judging_score_audit(event_id);",
    "CREATE INDEX IF NOT EXISTS idx_jaudit_cell        ON judging_score_audit(event_id, judge_id, presenter_number);",
    # L4: enforce at most one open event at a time
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_judging_one_open ON judging_events(status) WHERE status='open';",
]

# ─────────────────────────────────────────────────────────────────────────────
# Triggers
# ─────────────────────────────────────────────────────────────────────────────

# root_id self-population: originals (root_id IS NULL on insert) point at
# themselves. Revisions arrive with root_id already set by the app, so the WHEN
# clause skips them.
TRIGGER_ROOT_ID = """
CREATE TRIGGER IF NOT EXISTS knowledge_items_set_root
AFTER INSERT ON knowledge_items
WHEN new.root_id IS NULL
BEGIN
    UPDATE knowledge_items SET root_id = new.id WHERE id = new.id;
END;
"""

# FTS5 external-content sync. The 'delete' command needs OLD indexed values to
# remove the right row before re-inserting on update.
TRIGGER_FTS_INSERT = """
CREATE TRIGGER IF NOT EXISTS knowledge_items_fts_ai
AFTER INSERT ON knowledge_items
BEGIN
    INSERT INTO knowledge_fts(rowid, search_text) VALUES (new.id, new.search_text);
END;
"""

TRIGGER_FTS_DELETE = """
CREATE TRIGGER IF NOT EXISTS knowledge_items_fts_ad
AFTER DELETE ON knowledge_items
BEGIN
    INSERT INTO knowledge_fts(knowledge_fts, rowid, search_text)
    VALUES ('delete', old.id, old.search_text);
END;
"""

TRIGGER_FTS_UPDATE = """
CREATE TRIGGER IF NOT EXISTS knowledge_items_fts_au
AFTER UPDATE ON knowledge_items
BEGIN
    INSERT INTO knowledge_fts(knowledge_fts, rowid, search_text)
    VALUES ('delete', old.id, old.search_text);
    INSERT INTO knowledge_fts(rowid, search_text) VALUES (new.id, new.search_text);
END;
"""

# Order matters: parents before children, content tables before their FTS/triggers.
_TABLE_DDL = [
    SCHEMA_MIGRATIONS,
    ORGANIZATIONS,
    KNOWLEDGE_ITEMS,
    KNOWLEDGE_VECTORS,
    KNOWLEDGE_FTS,
    POSTS,
    POST_TEMPLATES,
    POST_DELIVERIES,
    EVENTS,
    EVENT_REMINDERS,
    SETTINGS,
    RAW_PAGES,
    NODES,
    EDGES,
    FRONTIER,
    PAGE_NODES,
    CRAWL_ENTRY_POINTS,
    OFFICE_PAGE_STATE,
    JUDGING_EVENTS,
    JUDGING_JUDGES,
    JUDGING_PRESENTERS,
    JUDGING_SCORES,
    JUDGING_AUDIENCE_VOTES,
    JUDGING_SCORE_AUDIT,
]

_TRIGGER_DDL = [
    TRIGGER_ROOT_ID,
    TRIGGER_FTS_INSERT,
    TRIGGER_FTS_DELETE,
    TRIGGER_FTS_UPDATE,
]

# Additive column migrations for already-created v2 tables (ALTER ... ADD COLUMN is
# idempotent here via try/except — SQLite has no "ADD COLUMN IF NOT EXISTS").
_COLUMN_MIGRATIONS = [
    ("frontier",           "error",           "TEXT"),
    # judging_events — new fields (safe to add on existing DBs)
    ("judging_events",     "score_min",        "INTEGER NOT NULL DEFAULT 1"),
    ("judging_events",     "score_max",        "INTEGER NOT NULL DEFAULT 5"),
    ("judging_events",     "min_coverage",     "INTEGER NOT NULL DEFAULT 3"),
    # judging_presenters — presence tracking
    ("judging_presenters", "telegram_id_hash", "TEXT"),
    ("judging_presenters", "is_present",       "INTEGER NOT NULL DEFAULT 0"),
    # judging_events — audience voting
    ("judging_events",     "audience_voting",  "TEXT NOT NULL DEFAULT 'closed'"),
    ("judging_events",     "audience_top_n",   "INTEGER NOT NULL DEFAULT 1"),
    # scheduled post-deletion (2026-06-23): platform unsend + per-delivery outcome. delete_status
    # values written by code: 'deleted'|'delete_unsupported'|'delete_failed'|'not_applicable' (CHECK
    # omitted — SQLite ALTER ADD COLUMN can't add it to an existing table; PostDeleter is the sole writer).
    ("posts",           "delete_at",       "TEXT"),
    ("posts",           "deleted_at",      "TEXT"),
    ("post_deliveries", "delete_status",   "TEXT"),
    ("post_deliveries", "deleted_at",      "TEXT"),
    ("post_deliveries", "delete_error",    "TEXT"),
    ("post_deliveries", "delete_attempts", "INTEGER NOT NULL DEFAULT 0"),
]

# Indexes that reference migration-added columns (run AFTER _COLUMN_MIGRATIONS in create_all).
_POST_MIGRATION_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_posts_delete_due ON posts(delete_at) "
    "WHERE delete_at IS NOT NULL AND deleted_at IS NULL",
]


def load_sqlite_vec(conn: sqlite3.Connection) -> None:
    """Load the sqlite-vec extension into a connection (needed for vec0)."""
    if sqlite_vec is None:
        raise RuntimeError(
            "sqlite-vec is not installed. Run: pip install sqlite-vec"
        )
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)


def get_connection(db_path: str) -> sqlite3.Connection:
    """Open a connection with FK enforcement and sqlite-vec loaded.

    Reused by the migration script and the v2 retriever so every code path gets
    the same configured connection.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 5000;")  # wait out the live bot's WAL writes
    load_sqlite_vec(conn)
    return conn


def create_all(db_path: str) -> sqlite3.Connection:
    """Create every v2 table, index and trigger in ``db_path`` (idempotent).

    Only creates NEW v2 objects — never alters or drops v1 tables. Safe to run
    against the live ``gsa_gateway.db`` or a fresh standalone file. Records the
    schema version in ``schema_migrations``.
    """
    conn = get_connection(db_path)
    try:
        for ddl in _TABLE_DDL:
            conn.execute(ddl)
        for ddl in INDEXES:
            conn.execute(ddl)
        for ddl in _TRIGGER_DDL:
            conn.execute(ddl)
        for table, col, coltype in _COLUMN_MIGRATIONS:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
            except sqlite3.OperationalError:
                pass                                   # column already exists — idempotent
        # Indexes over migration-added columns — MUST run after the ALTERs above (the columns
        # don't exist yet when the main INDEXES loop runs). Partial index keeps the deletion
        # poll (delete_at<=now AND deleted_at IS NULL) cheap on a growing posts table.
        for ddl in _POST_MIGRATION_INDEXES:
            conn.execute(ddl)
        # Idempotent seed: the auto-delete default must exist on the ROOT org so the dashboard
        # Settings tab can edit it (the live DB was migrated before this key existed, and
        # `seed_settings` only runs at v1→v2 migration). The NOT EXISTS guard makes it idempotent on
        # every startup (settings DOES have UNIQUE(org_id,key), so this also can't duplicate). Code
        # readers still fall back to 24 via get_setting regardless of whether the row exists.
        conn.execute(
            "INSERT INTO settings(org_id,key,value,type,description,updated_by) "
            "SELECT o.id, 'default.auto_delete_hours', '24', 'int', "
            "'Auto-delete window (hours, 1-48) when a post opts in', 'system' "
            "FROM organizations o WHERE o.parent_id IS NULL AND NOT EXISTS "
            "(SELECT 1 FROM settings s WHERE s.org_id=o.id AND s.key='default.auto_delete_hours')")
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?);",
            (SCHEMA_VERSION,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return conn


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "v2_test.db"
    c = create_all(target)
    tables = [
        r[0]
        for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
        )
    ]
    print(f"Created v2 schema in {target!r}")
    print("Tables:", ", ".join(tables))
    c.close()
