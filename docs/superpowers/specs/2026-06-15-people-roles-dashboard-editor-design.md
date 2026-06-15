# People & Roles Dashboard Editor (Manual Org Authoring) — Design

**Date:** 2026-06-15
**Status:** Design — pending implementation plan
**Author:** brainstormed with Mohammad Dindoost (VP Academic Affairs)
**Scope:** "Spec A" of a two-part vision. Spec B (multi-org crawler + dashboard
control page for Graduate Studies etc.) is a SEPARATE, later spec.

## Goal

Give the admin a dashboard editor to **manually add / edit / remove people and their
roles for any org** (GSA, Graduate Studies, PhD Club, future clubs) — with **free-form
titles** (e.g. add a "Sport Officer", remove the "Finance Officer") and an optional
bio that the bot can answer from. This fills the authoring-surface gap noted in the GSA
KG+KB work: the dashboard can already create orgs and KB items, but **cannot create graph
people or roles** — the People (KG) tab is read-only today.

## Current state (verified)

- Graph model: a person is a `Person` node; a role is a `has_role` edge → an `Org` node,
  with the role **category** in `edges.category` (allowed: faculty/staff/admin/advisor/
  joint/emeritus/officer/deprep) and the **free-text title(s)** in `edges.attrs.titles`.
  Org nodes bridge the `organizations` table via `attrs.org_id`.
- Source tagging: crawler rows are `source='crawler'`; manual rows are `source='dashboard'`.
  The crawler reconcile/`--reset` only touches `source='crawler'`, so manual edits survive
  re-crawls. (Established in the GSA work.)
- Reusable helpers (tested): `ensure_org`, `project_appointment` (Person + has_role,
  additive), `upsert_node`/`upsert_edge`, `deactivate_edges`, `officers_in_org`,
  `upsert_doc_items` (doc→KB chunks), `embed_all.py` (resumable embed).
- Dashboard: `local_server.py` already serves `/db` (read) and `POST /orgs`, `/knowledge`,
  `/posts`, `/settings`, plus job endpoints. The People tab (`renderPeople`) is **read-only**.

## Components

### 1. Graph + KB writes (reuse existing helpers)

- **Add / edit person**: upsert the `Person` node (name; attrs hold `email` and any notes)
  and one `has_role` edge → the chosen org, with the **free-text title** in `attrs.titles`
  and the mapped **category**. `source='dashboard'`. This is exactly `project_appointment`
  (already additive/idempotent) plus an attrs merge for email.
- **Optional bio / "About"**: if provided, one `knowledge_item` (`created_by='dashboard'`,
  `type='profile'`, `metadata.entity_id` = the person's node key, filed under the org), then
  embedded so the bot answers questions about them. Reuses the `upsert_doc_items` pattern
  (single item, not chunked unless long).
- **"+ New club/org"**: reuses the existing `POST /orgs` create (no duplication) — the
  editor just calls it, then refreshes the org list.
- Person node **key** convention: `dashboard/<org_slug>/<name-slug>` (matches the GSA roster
  path), so re-edits resolve the same node.

### 2. "Remove" = soft deactivate (never hard delete)

- Removing a role sets that `has_role` edge `is_active=0`. If the person then has **no other
  active roles**, the `Person` node is also deactivated. Any bio `knowledge_item` for them is
  retired (`is_active=0`) too.
- Soft delete is reversible, keeps history/audit, and matches the crawler's M3 departure
  handling. The person disappears from answers and the People tab immediately; the rows
  remain in the DB, hidden.

### 3. Role type → category, default Officer

- The form's **Title** is free text. A small **Role type** dropdown maps to the stored
  category:

  | Role type (UI) | category (stored) |
  |---|---|
  | Officer (default) | `officer` |
  | Dept Rep | `deprep` |
  | Staff | `staff` |
  | Advisor | `advisor` |
  | Admin | `admin` |

  The title ("Sport Officer", "Graduate Advisor") is what's shown; the category buckets the
  person for structured answers.

### 4. Structured answerability for ALL role types — `people_in_org`

- `officers_in_org` (exists) answers "who are the officers" — **officer/deprep only**.
- Add a generic **`people_in_org(conn, org_id)`** skill: same query as `officers_in_org`
  but **without the officer/deprep filter** — returns `(name, title, email)` for **every**
  active role in the org. So a Staff/Advisor/Admin person the editor creates is answerable.
- Router patterns (in `router.py`): "who works at/in `<org>`", "who are the people in
  `<org>`", "`<org>` staff/team" → `people_in_org`. `officers_in_org` stays for
  "officers/e-board". Wire both into `structured_answer.run`/`format_answer`
  (the officer formatter already exists; add a `people_in_org` formatter).

### 5. Backend endpoints (`local_server.py`)

- `POST /people` — create or edit: body = `{org_id|new_org, name, title, role_type, email,
  about}`. Resolves/creates the org, upserts Person + has_role (source='dashboard'),
  writes/updates the bio KB item, and **queues an embed** of the new/changed bio.
- `POST /people/remove` — body = `{person_key, org_id}`: deactivate that role (+ person if
  orphaned, + bio). Returns the updated roster.
- Both reuse the graph helpers; thin glue only. They sit behind the same auth as the other
  `local_server` write endpoints.
- **Offline fallback**: in the dashboard's offline (sql.js) mode, the same actions emit a
  `changes.sql` patch (INSERT/UPDATE nodes/edges/knowledge_items), consistent with how the
  dashboard already exports changes — but the live-endpoint path is primary.

### 6. Dashboard UI (People tab → editor)

Mockup approved. The People (KG) tab gains:
- an **Org picker** (lists active orgs; "+ New club/org" → `/orgs`),
- the existing **people table**, now with **Edit** / **Remove** per row,
- an **Add / Edit form**: Name, Title (free text), Role type (dropdown), Email (optional),
  About/notes (optional textarea → embedded). **Save** writes via `POST /people`.
- Crawler-sourced people (e.g. YWCC faculty) are shown read-only / clearly marked so the
  admin edits only `source='dashboard'` people (editing crawler rows is out of scope —
  they're owned by the crawler).

### 7. Embedding

On a bio create/edit, the endpoint triggers an embed (the existing resumable `embed_all`
path, run as a job) so the new/changed bio is searchable without a manual step. Removal
retires the bio's vector via the existing inactive-filtering (vectors for inactive items are
already ignored by retrieval; an optional cleanup can delete them).

## Crawler coexistence

Everything the editor writes is `source='dashboard'`. The crawler's reconcile and `--reset`
are scoped to `source='crawler'`, so a crawled org (e.g. Graduate Studies, once Spec B
lands) can have both crawler-found people and hand-added ones, and a re-crawl never
overwrites the manual ones.

## Testing

- **Endpoint create**: `POST /people` → expected Person node + has_role edge (category,
  title in attrs) + bio knowledge_item; embed queued.
- **Edit**: second `POST /people` for the same person updates title/email/bio, no duplicate
  node/edge (idempotent on the node key).
- **Remove**: `POST /people/remove` → edge inactive; person inactive iff no other active
  role; bio inactive. Reversible (re-add reactivates).
- **New org path**: "+ New club" creates the org then the person under it.
- **`people_in_org`**: returns all role types (officer, staff, advisor, admin, deprep);
  `officers_in_org` still returns only officer/deprep; router routes "who works at X" →
  `people_in_org`, "who are the officers" → `officers_in_org`.
- **Round-trip**: add via the People tab → appears in the table + in a "who works at X"
  answer; remove → gone from both.

## Gated workflow / safety

These are small, additive, soft-reversible per-edit writes (like the existing dashboard
org/KB creates), so they write to the live DB directly without a per-edit hardened backup —
consistent with current dashboard behavior. (Bulk migrations still use the gated
dry-run+backup scripts.) Soft delete makes every action undoable.

## Out of scope / deferred

- **Spec B**: multi-org crawler + dashboard "run crawler, pick orgs" page (Graduate Studies
  extractor, org→entry-point registry). Separate spec.
- Editing **crawler-owned** people in the dashboard (the crawler owns those; the editor
  manages `source='dashboard'` people).
- Bulk CSV import; per-edit backups; full offline-mode parity beyond the `changes.sql` patch.

## Open risks

- **`local_server` auth**: the existing local_server write endpoints require auth (the test
  suite's `local_server` 403s are a pre-existing token issue) — the new `/people` endpoints
  must work behind the same auth the dashboard already uses; the plan verifies this.
- **Title↔category coupling**: an admin could pick a misleading category for a title; the
  dropdown defaults to Officer and the category only affects grouping, so the blast radius
  is small (and editable).
