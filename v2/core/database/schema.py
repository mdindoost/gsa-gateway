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

Schema split (Phase 1, 2026-06-28):
  * ``create_knowledge_schema`` — creates ONLY knowledge/KG tables + FTS + triggers
    + settings + schema_migrations. No moved (OPS) tables. Loads sqlite-vec.
  * ``create_ops_schema`` — creates ONLY the publishing cluster + judging tables
    + their indexes + column migrations. Does NOT load sqlite-vec.
  * ``get_ops_connection`` — like ``get_connection`` but without sqlite-vec.
  * ``create_all`` — thin back-compat wrapper that calls both builders against the
    same path (used only by tests/fixtures that want one combined DB).
  * MOVED table set (must never appear in the knowledge schema):
    posts, post_templates, post_deliveries, events, event_reminders,
    judging_events, judging_judges, judging_presenters, judging_scores,
    judging_audience_votes, judging_score_audit.
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = "001_initial"

try:  # sqlite-vec is required for the vec0 virtual table.
    import sqlite_vec
except ImportError:  # pragma: no cover - surfaced clearly at runtime
    sqlite_vec = None


# ─────────────────────────────────────────────────────────────────────────────
# Group A — Knowledge/KG tables (STRICT) — stay in gsa_gateway.db
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

KNOWLEDGE_CHUNKS = """
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id           INTEGER PRIMARY KEY,
    parent_id    INTEGER NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
    source_key   TEXT    NOT NULL,              -- stable per-parent key for invalidation
    ordinal      INTEGER NOT NULL,              -- chunk position within the parent (0-based)
    text         TEXT    NOT NULL,              -- verbatim slice of the parent content
    content_hash TEXT    NOT NULL,              -- hash of (chunk text + model_id) for change-detect
    model_id     TEXT    NOT NULL,              -- embedding-model descriptor id that chunked this
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(parent_id, ordinal)
) STRICT;
"""

# ─── OPS: Publishing cluster DDL constants ───────────────────────────────────
# These carry org_slug TEXT NOT NULL (durable cross-DB join key) and retain
# org_id as a plain informational INTEGER (NO FK to organizations — different DB).

OPS_POSTS = """
CREATE TABLE IF NOT EXISTS posts (
    id              INTEGER PRIMARY KEY,
    org_id          INTEGER,
    org_slug        TEXT    NOT NULL DEFAULT 'gsa',
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

OPS_POST_TEMPLATES = """
CREATE TABLE IF NOT EXISTS post_templates (
    id              INTEGER PRIMARY KEY,
    org_id          INTEGER,
    org_slug        TEXT    NOT NULL DEFAULT 'gsa',
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

# OPS events: the LIVE v1 shape (INTEGER PRIMARY KEY AUTOINCREMENT, two legacy columns
# announcement_sent + channel_posted, org_id as plain INTEGER) plus org_slug.
# This is NOT the dead STRICT v2 DDL. The DEFAULT 'gsa' on org_slug is a convenience
# for fresh inserts; the migration sets it explicitly per existing row.
OPS_EVENTS = """
CREATE TABLE IF NOT EXISTS events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    date              TEXT    NOT NULL,
    time              TEXT    NOT NULL DEFAULT 'TBD',
    location          TEXT    NOT NULL DEFAULT 'TBD',
    description       TEXT    NOT NULL DEFAULT '',
    ki_content        TEXT,                          -- custom KB blurb (B3-1); NULL → one-liner
    organizer         TEXT    NOT NULL DEFAULT 'GSA',
    rsvp_link         TEXT    NOT NULL DEFAULT '',
    category          TEXT    NOT NULL DEFAULT 'general',
    reminder_sent_7d  INTEGER NOT NULL DEFAULT 0,
    reminder_sent_1d  INTEGER NOT NULL DEFAULT 0,
    reminder_sent_1h  INTEGER NOT NULL DEFAULT 0,
    announcement_sent INTEGER NOT NULL DEFAULT 0,
    channel_posted    TEXT,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    created_by        TEXT    NOT NULL DEFAULT 'system',
    org_id            INTEGER,
    org_slug          TEXT    NOT NULL DEFAULT 'gsa'
);
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

# ─── Knowledge-only tables (continued) ───────────────────────────────────────

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
# Group B — search tables (virtual; cannot be STRICT) — knowledge only
# ─────────────────────────────────────────────────────────────────────────────

# vec0 tables: the embedding width is descriptor-driven (`{dim}` filled from the active
# ModelDescriptor at build time — nomic 768, qwen 1024). Use vector_table_ddl()/
# recreate_vector_tables() rather than executing these templates directly.
KNOWLEDGE_VECTORS_TMPL = """
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_vectors USING vec0(
    item_id   INTEGER PRIMARY KEY,            -- = knowledge_items.id
    embedding FLOAT[{dim}]                     -- active embedding model (descriptor-driven)
);
"""

KNOWLEDGE_CHUNK_VECTORS_TMPL = """
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunk_vectors USING vec0(
    chunk_id  INTEGER PRIMARY KEY,             -- = knowledge_chunks.id
    embedding FLOAT[{dim}],                      -- active embedding model (descriptor-driven)
    org_id    INTEGER partition key,            -- in-engine filter for org-scoped queries (ARCH R3)
    type      TEXT,                             -- metadata column (filterable)
    +parent_id INTEGER                          -- auxiliary: collapse chunk -> parent item
);
"""


def vector_table_ddl(descriptor=None) -> list[str]:
    """The two vec0 CREATE statements with the embedding width taken from `descriptor`
    (default: the active ModelDescriptor). One source of truth for the vector dimension."""
    from v2.core.retrieval.model_descriptor import active_descriptor
    dim = (descriptor or active_descriptor()).dim
    return [KNOWLEDGE_VECTORS_TMPL.format(dim=dim),
            KNOWLEDGE_CHUNK_VECTORS_TMPL.format(dim=dim)]


def recreate_vector_tables(conn: sqlite3.Connection, descriptor=None) -> None:
    """DROP + recreate both vec0 tables at `descriptor`'s dim (default: active). Used to
    migrate a DB whose vectors were embedded at a different width (e.g. nomic 768 -> qwen
    1024) BEFORE re-embedding. Destroys existing vectors — the caller re-embeds after."""
    conn.execute("DROP TABLE IF EXISTS knowledge_vectors")
    conn.execute("DROP TABLE IF EXISTS knowledge_chunk_vectors")
    for ddl in vector_table_ddl(descriptor):
        conn.execute(ddl)

KNOWLEDGE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    search_text,
    content='knowledge_items',
    content_rowid='id'
);
"""

# ─────────────────────────────────────────────────────────────────────────────
# Group C — knowledge graph (STRICT) — knowledge only
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

PAGE_NODES = """
CREATE TABLE IF NOT EXISTS page_nodes (
    raw_url   TEXT NOT NULL REFERENCES raw_pages(url),
    node_id   INTEGER NOT NULL REFERENCES nodes(id),
    PRIMARY KEY (raw_url, node_id)
) STRICT;
"""

# ─────────────────────────────────────────────────────────────────────────────
# Group D — judging system (STRICT) — OPS only
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
    voter_hash       TEXT    NOT NULL,
    presenter_number INTEGER NOT NULL,
    voted_at         TEXT    NOT NULL DEFAULT (datetime('now')),
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
# Indexes — split into knowledge vs ops
# ─────────────────────────────────────────────────────────────────────────────

_KNOWLEDGE_INDEXES = [
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
]

_OPS_INDEXES = [
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

# Back-compat alias so existing code that imports INDEXES still works.
INDEXES = _KNOWLEDGE_INDEXES + _OPS_INDEXES

# ─────────────────────────────────────────────────────────────────────────────
# Triggers — knowledge only (FTS + root_id)
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

# ─────────────────────────────────────────────────────────────────────────────
# DDL lists (order matters: parents before children)
# ─────────────────────────────────────────────────────────────────────────────

_KNOWLEDGE_TABLE_DDL = [
    SCHEMA_MIGRATIONS,
    ORGANIZATIONS,
    KNOWLEDGE_ITEMS,
    KNOWLEDGE_CHUNKS,
    # vec0 tables (knowledge_vectors, knowledge_chunk_vectors) are created via
    # vector_table_ddl() so their embedding width follows the active descriptor.
    KNOWLEDGE_FTS,
    SETTINGS,
    RAW_PAGES,
    NODES,
    EDGES,
    FRONTIER,
    PAGE_NODES,
]

_OPS_TABLE_DDL = [
    OPS_POSTS,
    OPS_POST_TEMPLATES,
    POST_DELIVERIES,
    OPS_EVENTS,
    EVENT_REMINDERS,
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

# Additive column migrations split by DB.
# Knowledge: only frontier.error (already present in DDL above, kept for idempotence on old DBs).
_KNOWLEDGE_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    ("frontier", "error", "TEXT"),
]

# OPS: judging + posts + post_deliveries column additions (safe on old DBs via try/except).
_OPS_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    # judging_events — new fields
    ("judging_events",     "score_min",        "INTEGER NOT NULL DEFAULT 1"),
    ("judging_events",     "score_max",        "INTEGER NOT NULL DEFAULT 5"),
    ("judging_events",     "min_coverage",     "INTEGER NOT NULL DEFAULT 3"),
    # judging_presenters — presence tracking
    ("judging_presenters", "telegram_id_hash", "TEXT"),
    ("judging_presenters", "is_present",       "INTEGER NOT NULL DEFAULT 0"),
    # judging_events — audience voting
    ("judging_events",     "audience_voting",  "TEXT NOT NULL DEFAULT 'closed'"),
    ("judging_events",     "audience_top_n",   "INTEGER NOT NULL DEFAULT 1"),
    # scheduled post-deletion: platform unsend + per-delivery outcome.
    ("posts",           "delete_at",       "TEXT"),
    ("posts",           "deleted_at",      "TEXT"),
    ("post_deliveries", "delete_status",   "TEXT"),
    ("post_deliveries", "deleted_at",      "TEXT"),
    ("post_deliveries", "delete_error",    "TEXT"),
    ("post_deliveries", "delete_attempts", "INTEGER NOT NULL DEFAULT 0"),
    # B3-1: KB blurb stored on OPS event so derive_event_kb can reproduce it.
    ("events",          "ki_content",      "TEXT"),
]

# Back-compat: callers that imported _COLUMN_MIGRATIONS still work.
_COLUMN_MIGRATIONS = _KNOWLEDGE_COLUMN_MIGRATIONS + _OPS_COLUMN_MIGRATIONS

# Indexes that reference migration-added columns (run AFTER _COLUMN_MIGRATIONS).
# These are OPS-only (deletion index on posts).
_POST_MIGRATION_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_posts_delete_due ON posts(delete_at) "
    "WHERE delete_at IS NOT NULL AND deleted_at IS NULL",
]

# Back-compat alias for the original combined _TABLE_DDL (used by create_all).
_TABLE_DDL = _KNOWLEDGE_TABLE_DDL + _OPS_TABLE_DDL


# ─────────────────────────────────────────────────────────────────────────────
# Connection helpers
# ─────────────────────────────────────────────────────────────────────────────

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


def get_ops_connection(db_path: str) -> sqlite3.Connection:
    """Open an OPS-DB connection with FK enforcement but WITHOUT sqlite-vec.

    The OPS DB has no vectors, so we skip the extension to keep the connection
    lean and avoid requiring sqlite-vec wherever only ops data is touched.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn


# ─────────────────────────────────────────────────────────────────────────────
# Schema builders
# ─────────────────────────────────────────────────────────────────────────────

def create_knowledge_schema(db_path: str) -> sqlite3.Connection:
    """Create ONLY the knowledge/KG tables, indexes, triggers, FTS, and settings seed.

    Loads sqlite-vec (required for the vec0 virtual table). Does NOT create any
    of the MOVED tables (posts, events, judging_*, …). This is the correct startup
    call for the Knowledge DB path; it enforces the HIGH-3 invariant.
    """
    conn = get_connection(db_path)
    try:
        for ddl in _KNOWLEDGE_TABLE_DDL:
            conn.execute(ddl)
        for ddl in vector_table_ddl():          # descriptor-driven vec0 width
            conn.execute(ddl)
        for ddl in _KNOWLEDGE_INDEXES:
            conn.execute(ddl)
        for ddl in _TRIGGER_DDL:
            conn.execute(ddl)
        for table, col, coltype in _KNOWLEDGE_COLUMN_MIGRATIONS:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
            except sqlite3.OperationalError:
                pass  # column already exists — idempotent
        # Idempotent settings seed: ensure the auto-delete default exists on the ROOT org.
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


def create_ops_schema(db_path: str) -> sqlite3.Connection:
    """Create ONLY the OPS tables (publishing cluster + judging) + their indexes.

    Does NOT load sqlite-vec (OPS has no vectors). Safe to call at server startup
    against gsa_gateway_ops.db. Column migrations (judging + deletion columns) run
    here for OPS tables; idx_posts_delete_due is created after the migrations.
    """
    conn = get_ops_connection(db_path)
    try:
        for ddl in _OPS_TABLE_DDL:
            conn.execute(ddl)
        for ddl in _OPS_INDEXES:
            conn.execute(ddl)
        for table, col, coltype in _OPS_COLUMN_MIGRATIONS:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
            except sqlite3.OperationalError:
                pass  # column already exists — idempotent
        for ddl in _POST_MIGRATION_INDEXES:
            conn.execute(ddl)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return conn


def create_all(db_path: str) -> sqlite3.Connection:
    """Create every v2 table, index and trigger in ``db_path`` (idempotent).

    Back-compat wrapper that runs BOTH knowledge and OPS schemas in ONE connection.
    Used only by tests/fixtures that want a combined DB (greenfield + test setups,
    incl. ``:memory:`` databases). Production startup uses the two separate builders
    against their respective file paths.

    Only creates NEW v2 objects — never alters or drops v1 tables. Safe to run
    against the live ``gsa_gateway.db`` or a fresh standalone file.
    """
    conn = get_connection(db_path)
    try:
        for ddl in _TABLE_DDL:
            conn.execute(ddl)
        for ddl in vector_table_ddl():          # descriptor-driven vec0 width
            conn.execute(ddl)
        for ddl in INDEXES:
            conn.execute(ddl)
        for ddl in _TRIGGER_DDL:
            conn.execute(ddl)
        for table, col, coltype in _COLUMN_MIGRATIONS:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
            except sqlite3.OperationalError:
                pass   # column already exists — idempotent
        for ddl in _POST_MIGRATION_INDEXES:
            conn.execute(ddl)
        # Idempotent settings seed: ensure the auto-delete default exists on the ROOT org.
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
