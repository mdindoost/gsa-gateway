"""explore(start, depth, aspect): bounded BFS over anchored entry points.

hub -> child listings; listing -> people (one appointment each, category from the section,
org inherited) -> their profile URLs; profile -> enrich (attrs + research + home appointment).
Saves raw at each hop, records unexplored next-steps in `frontier`, links page->node in
`page_nodes`, and skips re-extraction when a page's struct_hash is unchanged. Deterministic
only (LLM-on-prose is Phase 2). Each page is processed in its own transaction."""
from __future__ import annotations
import sqlite3
import urllib.request
from collections import deque
from dataclasses import dataclass

from v2.core.graph.orgs import ensure_org, org_node_id
from v2.core.graph.project import project_appointment, project_entity
from v2.core.graph.raw import save_raw_page, struct_hash
from v2.core.ingestion import entry_points as ep
from v2.core.ingestion.discovery import category_for_section, hub_children, parse_listing
from v2.core.ingestion.njit_adapter import entity_id_from_url, parse_entity


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
    except Exception:  # noqa: BLE001 - any fetch failure is just a non-ok read
        return url, "", "error"


@dataclass
class ExploreStats:
    fetched: int = 0
    skipped_unchanged: int = 0
    appointments: int = 0
    frontier_added: int = 0
    errors: int = 0


def _record_frontier(conn, from_node_id, url, aspect, depth):
    conn.execute("INSERT OR IGNORE INTO frontier(from_node_id,url,aspect,depth_discovered) "
                 "VALUES(?,?,?,?)", (from_node_id, url, aspect, depth))


def _unchanged(conn, url, html) -> bool:
    row = conn.execute("SELECT struct_hash FROM raw_pages WHERE url=?", (url,)).fetchone()
    return row is not None and row[0] == struct_hash(html)


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
            conn.execute("UPDATE frontier SET status='error' WHERE url=?", (node.url,))
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
                for p in parse_listing(html):
                    purl = "https://people.njit.edu/profile/" + p.slug
                    pkey = entity_id_from_url(purl)
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
            elif node.kind == "profile":
                if unchanged:
                    continue
                rec = parse_entity(final_url, html)
                # Profiles ENRICH only (attrs + research). Listings own appointments — the
                # section is the authoritative role — and always run before profiles, so the
                # profile must not create/clobber a has_role (e.g. turn a 'Staff' person into
                # 'admin' off an '…Office of the Dean' title suffix).
                org_id = ensure_org(conn, node.org_slug, node.org_name, node.parent_slug)
                pid = project_entity(conn, rec, org_id, home_appointment=False)
                conn.execute("INSERT OR IGNORE INTO page_nodes(raw_url,node_id) VALUES(?,?)",
                             (final_url, pid))
                site = rec.links.get("website")
                if site:
                    _record_frontier(conn, pid, site, aspect, d - 1)
                    st.frontier_added += 1
    return st
