"""explore(start, depth, aspect): bounded BFS over anchored entry points.

hub -> child listings; listing -> people (one appointment each, category from the section,
org inherited) -> their profile URLs; profile -> enrich (attrs + research + home appointment).
Saves raw at each hop, records unexplored next-steps in `frontier`, links page->node in
`page_nodes`, and skips re-extraction when a page's struct_hash is unchanged. Deterministic
only (LLM-on-prose is Phase 2). Each page is processed in its own transaction."""
from __future__ import annotations
import json
import sqlite3
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass

from v2.core.graph.orgs import ensure_org, org_node_id
from v2.core.graph.project import project_appointment
from v2.core.graph.raw import page_text, save_raw_page, struct_hash
from v2.core.ingestion import entry_points as ep
from v2.core.ingestion.decompose import decompose
from v2.core.ingestion.discovery import category_for_section, hub_children, parse_listing
from v2.core.ingestion.njit_adapter import entity_id_from_url, parse_entity
from v2.core.ingestion.reconcile import reconcile_entity


_UA = "GSA-Gateway-Bot/1.0 (+https://github.com/mdindoost/gsa-gateway)"


def http_fetch(url: str, timeout: int = 25) -> tuple[str, str, str]:
    """Real fetcher for production runs: (final_url, html, status). Follows redirects
    (urllib does) and reports the FINAL url so `web.njit.edu/~x` → `x.github.io` is
    keyed correctly. Never raises — a failure returns ("", "error") so explore() marks
    the frontier item 'error' and moves on. Tests inject their own fetcher instead."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.geturl(), r.read().decode("utf-8", "ignore"), "ok"
    except urllib.error.HTTPError as e:
        return url, "", f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return url, "", f"URLError: {e.reason}"
    except Exception as e:  # noqa: BLE001 - any other failure is still just a non-ok read
        return url, "", f"{type(e).__name__}: {e}"


@dataclass
class ExploreStats:
    fetched: int = 0
    skipped_unchanged: int = 0
    appointments: int = 0
    frontier_added: int = 0
    errors: int = 0
    departed: int = 0          # appointments retired by the M3 section-scoped sweep


def _record_frontier(conn, from_node_id, url, aspect, depth):
    conn.execute("INSERT OR IGNORE INTO frontier(from_node_id,url,aspect,depth_discovered) "
                 "VALUES(?,?,?,?)", (from_node_id, url, aspect, depth))


def _unchanged(conn, url, html) -> bool:
    row = conn.execute("SELECT struct_hash FROM raw_pages WHERE url=?", (url,)).fetchone()
    return row is not None and row[0] == struct_hash(html)


def _home_dept_org_id(conn, person_node_id) -> int | None:
    """The organizations.id of the person's DEPARTMENT appointment — listings are the
    authoritative source of where someone belongs (a profile page's text can mention the
    wrong dept, e.g. Amy Hoover's page says 'Computer Science' but she's Informatics).
    Prefers a faculty / primary role. None when they have no department appointment (pure
    admin/staff), so the caller falls back to the path org."""
    row = conn.execute(
        "SELECT json_extract(o.attrs,'$.org_id') AS oid "
        "FROM edges e JOIN nodes o ON o.id=e.dst_id "
        "WHERE e.src_id=? AND e.type='has_role' AND e.is_active=1 "
        "AND json_extract(o.attrs,'$.org_id') IN "
        "    (SELECT id FROM organizations WHERE type='department') "
        "ORDER BY (e.category='faculty') DESC, "
        "         (json_extract(e.attrs,'$.is_primary')=1) DESC LIMIT 1",
        (person_node_id,)).fetchone()
    return row[0] if row else None


def explore(conn: sqlite3.Connection, fetch, start: ep.EntryPoint | None = None,
            depth: int = 2, aspect: str = "people") -> ExploreStats:
    start = start or ep.ROOT
    st = ExploreStats()
    # queue items: (EntryPoint, from_node_id, depth_remaining). A child reached with a
    # remaining budget of >0 hops is fetched; one that would land at 0 is deferred to the
    # frontier instead. So depth=2 walks hub->listing and defers the profiles.
    q: deque = deque([(start, None, depth)])
    visited: set[str] = set()
    while q:
        node, from_node, d = q.popleft()
        if node.url in visited:
            continue
        visited.add(node.url)
        final_url, html, status = fetch(node.url)
        if status != "ok":
            conn.execute("UPDATE frontier SET status='error', error=? WHERE url=?",
                         (status, node.url))
            st.errors += 1
            continue
        unchanged = _unchanged(conn, final_url, html)
        save_raw_page(conn, final_url, html, status)
        st.fetched += 1
        if unchanged:
            st.skipped_unchanged += 1
        # Whether the structure changed or not we still traverse it so the BFS can reach
        # deeper pages; only the *extraction* (projecting nodes/appointments) is skipped
        # when the struct_hash is unchanged.
        with conn:
            if node.kind == "hub":
                for label, curl in hub_children(html, base=final_url):
                    child = ep.child_for(label, curl)
                    if not child:
                        continue
                    if d - 1 > 0:
                        q.append((child, None, d - 1))
                    else:
                        _record_frontier(conn, None, curl, aspect, d - 1)
                        st.frontier_added += 1
            elif node.kind == "listing":
                org_id = ensure_org(conn, node.org_slug, node.org_name, node.parent_slug)
                present: set[str] = set()
                for p in parse_listing(html):
                    purl = "https://people.njit.edu/profile/" + p.slug
                    pkey = entity_id_from_url(purl)
                    present.add(pkey)
                    if unchanged:
                        pid = conn.execute(
                            "SELECT id FROM nodes WHERE type='Person' AND key=?",
                            (pkey,)).fetchone()
                        pid = pid[0] if pid else None
                    else:
                        pid = project_appointment(
                            conn, person_key=pkey, name=p.name, org_id=org_id,
                            category=category_for_section(p.section),
                            titles=p.titles, source_section=p.section)
                        conn.execute(
                            "INSERT OR IGNORE INTO page_nodes(raw_url,node_id) VALUES(?,?)",
                            (final_url, pid))
                        st.appointments += 1
                    prof = ep.EntryPoint(purl, node.org_slug, node.org_name, "profile",
                                         node.parent_slug)
                    if d - 1 > 0:
                        q.append((prof, pid, d - 1))
                    else:
                        _record_frontier(conn, pid, purl, aspect, d - 1)
                        st.frontier_added += 1
                # M3 — section-scoped deactivation: people who were on THIS listing before
                # but aren't now (departed or moved to another dept) lose their appointment
                # to THIS org. Scoped to this org + crawler-source, and only when we actually
                # re-parsed a non-empty listing (a failed/empty fetch must never deactivate).
                if not unchanged and present:
                    ph = ",".join("?" * len(present))
                    onode = org_node_id(conn, org_id)
                    for (eid,) in conn.execute(
                            f"SELECT e.id FROM edges e JOIN nodes p ON p.id=e.src_id "
                            f"WHERE e.type='has_role' AND e.dst_id=? AND e.is_active=1 "
                            f"AND e.source='crawler' AND p.key NOT IN ({ph})",
                            [onode, *present]).fetchall():
                        conn.execute("UPDATE edges SET is_active=0, "
                                     "updated_at=datetime('now') WHERE id=?", (eid,))
                        st.departed += 1
            elif node.kind == "profile":
                if unchanged:
                    continue
                rec = parse_entity(final_url, html)
                # Populate BOTH layers in one reconcile transaction (B1): decomposed
                # knowledge_items (the text/semantic layer → KB tab + RAG) AND the graph
                # (attrs + research). home_appointment=False because listings own the roles
                # (section = authoritative); the profile must not create/clobber one (e.g.
                # turn a 'Staff' person into 'admin' off an '…Office of the Dean' suffix).
                # knowledge_items are filed under the person's HOME dept (rec.org), not the
                # path we reached them through.
                prow = conn.execute("SELECT id FROM nodes WHERE type='Person' AND key=?",
                                    (rec.entity_id,)).fetchone()
                home = _home_dept_org_id(conn, prow[0]) if prow else None
                ki_org = home or ensure_org(conn, node.org_slug, node.org_name, node.parent_slug)
                reconcile_entity(conn, ki_org, rec.entity_id, decompose(rec),
                                 created_by="crawler", rec=rec, home_appointment=False)
                pid = conn.execute("SELECT id FROM nodes WHERE type='Person' AND key=?",
                                   (rec.entity_id,)).fetchone()[0]
                conn.execute("INSERT OR IGNORE INTO page_nodes(raw_url,node_id) VALUES(?,?)",
                             (final_url, pid))
                site = rec.links.get("website")
                if site:
                    _record_frontier(conn, pid, site, aspect, d - 1)
                    st.frontier_added += 1
    return st


def _upsert_site_item(conn, org_id, entity_id, name, url, text):
    """Insert-or-version-bump ONE 'webpage' knowledge_item for a personal site, keyed by a
    stable natural_key so a re-run dedups (no duplicate). Leaves the person's other items
    untouched (unlike full reconcile). Title carries the person's NAME so person-specific
    queries rank their site (the title is part of the embedded/searched text)."""
    nk = entity_id + ":site"
    title = f"{name} — personal website"
    row = conn.execute(
        "SELECT id, content, title FROM knowledge_items WHERE is_active=1 AND org_id=? "
        "AND json_extract(metadata,'$.natural_key')=?", (org_id, nk)).fetchone()
    if row and row[1] == text and row[2] == title:
        return                                    # unchanged (content + title)
    if row:
        conn.execute("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
                     "WHERE id=?", (row[0],))
    conn.execute(
        "INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,"
        "is_active,created_by) VALUES(?,?,?,?,?,?,1,'crawler')",
        (org_id, "webpage", title, text,
         json.dumps({"entity_id": entity_id, "natural_key": nk}), url))


def process_frontier(conn: sqlite3.Connection, fetch, limit: int | None = None) -> ExploreStats:
    """Explore pending frontier next-steps (personal sites): fetch → save raw → add the page
    text as a 'webpage' knowledge_item under the person's home dept (semantic-searchable; the
    structured extraction — students/advises, awards — is Phase 2/LLM) → mark fetched.
    Idempotent. This is the unit a controllable Job will invoke."""
    st = ExploreStats()
    sql = "SELECT id, from_node_id, url FROM frontier WHERE status='pending'"
    params: tuple = ()
    if limit:
        sql += " LIMIT ?"
        params = (limit,)
    for fid, person_node, url in conn.execute(sql, params).fetchall():
        final_url, html, status = fetch(url)
        if status != "ok" or not person_node:
            conn.execute("UPDATE frontier SET status='error', error=? WHERE id=?",
                         (status if status != "ok" else "no person node", fid))
            conn.commit()
            st.errors += 1
            continue
        with conn:
            save_raw_page(conn, final_url, html, status)
            prow = conn.execute("SELECT key, name FROM nodes WHERE id=?", (person_node,)).fetchone()
            org = conn.execute(
                "SELECT org_id FROM knowledge_items WHERE is_active=1 AND created_by='crawler' "
                "AND json_extract(metadata,'$.entity_id')=? LIMIT 1",
                (prow[0],)).fetchone() if prow else None
            if org:
                _upsert_site_item(conn, org[0], prow[0], prow[1], final_url,
                                  page_text(html)[:6000])
                conn.execute("INSERT OR IGNORE INTO page_nodes(raw_url,node_id) VALUES(?,?)",
                             (final_url, person_node))
            conn.execute("UPDATE frontier SET status='fetched' WHERE id=?", (fid,))
            st.fetched += 1
    return st


def reconcile_departures(conn: sqlite3.Connection) -> dict:
    """Post-gather cleanup for people who left or moved depts (M3). For each crawler Person:
      * no active appointment at all  -> fully departed: deactivate the node, its edges, and
        its crawler knowledge_items (+ drop their vectors).
      * crawler knowledge_items filed under an org that is NOT their current home department
        -> stale from a move (the profile pass re-filed under the new dept): deactivate them.
    Returns counts. Idempotent — a no-departures run changes nothing."""
    out = {"departed_people": 0, "items_retired": 0}

    def _drop_items(item_ids):
        if not item_ids:
            return
        conn.executemany("DELETE FROM knowledge_vectors WHERE item_id=?", [(i,) for i in item_ids])
        conn.executemany("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
                         "WHERE id=?", [(i,) for i in item_ids])
        out["items_retired"] += len(item_ids)

    with conn:
        for pid, key in conn.execute(
                "SELECT id, key FROM nodes WHERE type='Person' AND is_active=1 "
                "AND source='crawler'").fetchall():
            appts = conn.execute(
                "SELECT COUNT(*) FROM edges WHERE src_id=? AND type='has_role' AND is_active=1",
                (pid,)).fetchone()[0]
            ki = conn.execute(
                "SELECT id, org_id FROM knowledge_items WHERE is_active=1 AND created_by='crawler' "
                "AND json_extract(metadata,'$.entity_id')=?", (key,)).fetchall()
            if appts == 0:                                   # fully departed
                _drop_items([i for i, _ in ki])
                conn.execute("UPDATE edges SET is_active=0 WHERE src_id=? AND is_active=1", (pid,))
                conn.execute("UPDATE nodes SET is_active=0, updated_at=datetime('now') WHERE id=?", (pid,))
                out["departed_people"] += 1
                continue
            home = _home_dept_org_id(conn, pid)              # moved: KB under a non-home dept is stale
            if home is not None:
                _drop_items([i for i, org in ki if org != home])
    return out
