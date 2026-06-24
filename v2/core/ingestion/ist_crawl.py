"""IST (Information Services & Technology) office crawler.

IST-specific extractor — a copy of the EOS crawler adapted for ist.njit.edu: host-scoped
whole-site DFS from the homepage, a delimiter-anchored /ist-key-contacts roster parser, and
ingest under the existing 'ist' office. Reuses the web_crawler spine (fetch / clean / link
discovery). Brings data ONLY — fetch → mechanically clean → emit records for the caller to
store in KB/KG. It makes NO serving/gating/staging decisions (2026-06-23 hard line).

Spec: docs/superpowers/specs/2026-06-24-ist-crawl-design.md
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, replace
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment
from v2.core.ingestion.people_editor import _slug
from v2.core.ingestion.web_crawler import clean_text, normalize_url, select_links

logger = logging.getLogger(__name__)

IST_SLUG = "ist"
IST_NAME = "IST / Technology Support"

_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@njit\.edu$", re.I)
_PHONE = re.compile(r"Phone#\s*([0-9][0-9\-]+)", re.I)
# Headings that introduce a staff roster (sites title the block differently).
_ROSTER_ANCHORS = ("department staff", "staff directory", "our staff", "our team",
                   "department contacts", "office staff")
# Site-chrome markers that can follow the staff block in clean_text output.
_BLOCK_END = ("popular searches", "in this section")


@dataclass(frozen=True)
class StaffRecord:
    name: str
    title: str
    phone: str
    email: str


_ASSET_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif")


@dataclass(frozen=True)
class ProsePage:
    title: str
    content: str
    source_url: str
    images: tuple[tuple[str, str], ...] = ()   # (absolute_url, alt) — e.g. the campus map
    files: tuple[tuple[str, str], ...] = ()    # (absolute_url, link_text) — linked pdf/jpg/png


def _main_region(soup: BeautifulSoup):
    """The page's main-content container, falling back progressively. Drupal pages on
    www.njit.edu wrap content in ``div[role=main]``; we strip the surrounding chrome
    (site header/nav/footer + the 'Popular Searches' block) by scoping to it."""
    return (
        soup.find("div", attrs={"role": "main"})
        or soup.find("main")
        or soup.find("div", class_="region-content")
        or soup
    )


@dataclass
class EntryResult:
    seed: str
    staff: list[StaffRecord]
    prose: list[ProsePage]
    skipped: list[str]   # pages with no readable content (flag, never stored)
    truncated: bool = False   # hit the page budget with links still queued


def _canon(url: str) -> str:
    """Canonicalize an njit.edu URL to https. The hub emits absolute http:// links whose
    http→https redirect our fetcher does not follow (they return the home-page stub), so
    folding scheme here removes that whole class of duplicate."""
    return re.sub(r"^http://", "https://", url)


def _in_scope(seed_host: str, url: str) -> bool:
    """IST is ONE subdomain — scope is host-match (every ist.njit.edu page), NOT a path
    prefix (EOS's per-seed path-prefix scope rejected IST's sibling links). select_links
    already host-bounds, but we guard explicitly so a stray off-host link (www / servicedesk
    / myucid / external) is never followed."""
    return urlparse(url).netloc == seed_host


def crawl_entry(seed: str, fetch, max_depth: int = 4, budget: int = 400, stats: dict | None = None):
    """DFS from ``seed``, following EVERY same-host link deep. Yields ``(url, html)``.

    Reuses the web_crawler spine (``select_links`` for asset-dropping link extraction) but keeps
    RAW HTML (which ``crawl_site`` discards) and applies a host-match scope so the whole IST
    subdomain is walked from the homepage seed. Scheme canonicalized to https; depth- and
    budget-bounded, dedup + loop-guarded. ``fetch(url) -> html|None`` is injected. If ``stats`` is
    given, ``stats['truncated']`` is set True when the budget is hit with links still queued."""
    seed = _canon(normalize_url(seed, seed))
    seed_host = urlparse(seed).netloc
    seen = {seed}
    stack: list[tuple[str, int]] = [(seed, 0)]
    while stack and len(seen) <= budget:
        url, depth = stack.pop()                       # DFS (go deep)
        html = fetch(url)
        if not html:
            continue
        yield url, html
        if depth < max_depth:
            follow, _ = select_links(html, url, seed, relevance_gated=False)
            for u in sorted((_canon(u) for u in follow), reverse=True):  # https, deterministic
                if u not in seen and _in_scope(seed_host, u):
                    seen.add(u)
                    stack.append((u, depth + 1))
    if stats is not None:
        stats["truncated"] = bool(stack)               # links left unfetched -> truncated
        if stack:
            logger.warning("crawl_entry: hit budget %d at %s; %d links not followed",
                           budget, seed, len(stack))


def _url_rank(url: str) -> tuple[int, int]:
    """Lower is better when picking the canonical URL among same-content aliases:
    prefer non-.php (clean URL), then the shorter path."""
    return (1 if url.lower().endswith(".php") else 0, len(url))


def extract_entry(seed: str, fetch, max_depth: int = 4, budget: int = 300) -> EntryResult:
    """Crawl one EOS entry point and extract: roster pages -> staff (KG), prose pages ->
    KB, empty shells -> skipped (flagged). Prose is deduped by content hash (collapsing
    .php / clean-URL aliases), keeping the cleanest URL. Brings data only; no DB writes."""
    res = EntryResult(seed=_canon(normalize_url(seed, seed)), staff=[], prose=[], skipped=[])
    seen_emails: set[str] = set()
    by_hash: dict[str, ProsePage] = {}
    order: list[str] = []
    stats: dict = {}
    for url, html in crawl_entry(seed, fetch, max_depth=max_depth, budget=budget, stats=stats):
        # Parse ONCE per page, then branch (roster takes precedence over prose).
        staff = parse_roster(clean_text(html))
        if staff:
            for s in staff:
                if s.email not in seen_emails:
                    seen_emails.add(s.email)
                    res.staff.append(s)
            continue
        page = extract_prose(url, html)
        if page is None:
            res.skipped.append(url)
            continue
        h = hashlib.sha1(page.content.encode("utf-8")).hexdigest()
        if h not in by_hash:
            by_hash[h] = page
            order.append(h)
        elif _url_rank(page.source_url) < _url_rank(by_hash[h].source_url):
            by_hash[h] = page                           # prefer the cleaner alias URL
    res.prose = [by_hash[h] for h in order]
    _strip_recurring_assets(res.prose)
    res.truncated = stats.get("truncated", False)
    return res


def _strip_recurring_assets(pages: list[ProsePage]) -> None:
    """Remove ONLY site-wide near-universal chrome assets (e.g. an announcement PDF stamped
    on nearly every page). Per the 2026-06-23 verbatim hard line, an asset on a MINORITY of
    pages (a real form/rate-sheet shared by a few) must never be dropped — so we strip an
    asset only when it appears on >= n-1 of n pages AND n >= 5 (small crawls strip nothing).
    Mutates ``pages`` in place (frozen dataclasses are replaced)."""
    n = len(pages)
    if n < 5:
        return
    files = Counter(u for p in pages for u, _ in p.files)
    images = Counter(u for p in pages for u, _ in p.images)
    recurring = {
        u for c in (files, images) for u, k in c.items() if k >= n - 1
    }
    if not recurring:
        return
    for i, p in enumerate(pages):
        pages[i] = replace(
            p,
            files=tuple((u, t) for u, t in p.files if u not in recurring),
            images=tuple((u, a) for u, a in p.images if u not in recurring),
        )


def classify_page(html: str) -> str:
    """Decide how a page should be handled: ``staff-roster`` (people → KG),
    ``prose`` (content → KB), or ``skip-empty`` (no readable main content → flag, never
    store). Roster takes precedence — the contacts page also carries address prose, but
    it is the people source."""
    if parse_roster(clean_text(html)):
        return "staff-roster"
    if extract_prose("", html) is not None:
        return "prose"
    return "skip-empty"


def extract_prose(url: str, html: str) -> ProsePage | None:
    """Mechanically clean a service page to VERBATIM main-content text (hard line #3).

    Returns None when the main region has no readable text (e.g. a JS-only SPA shell) so
    the caller can flag+skip rather than store an empty page.
    """
    soup = BeautifulSoup(html, "html.parser")
    region = _main_region(soup)
    content = clean_text(str(region))
    if not content:
        return None
    h1 = soup.find("h1")
    if h1 and h1.get_text(" ", strip=True):
        title = h1.get_text(" ", strip=True)
    elif soup.title and soup.title.get_text(strip=True):
        title = soup.title.get_text(strip=True).split("|")[0].strip()
    else:
        title = url

    # Mechanical figure capture: img src+alt, and linked asset files (pdf/jpg/png).
    # Literal page data only — the image itself is never described (anti-fab). Kept as
    # structured fields, NOT mixed into the verbatim text body.
    images: list[tuple[str, str]] = []
    for im in region.find_all("img"):
        src = im.get("src")
        if src:
            images.append((urljoin(url, src), im.get("alt", "").strip()))
    files: list[tuple[str, str]] = []
    for a in region.find_all("a", href=True):
        href = urljoin(url, a["href"])
        if href.lower().endswith(_ASSET_EXT):
            files.append((href, a.get_text(" ", strip=True)))
    return ProsePage(
        title=title, content=content, source_url=url,
        images=tuple(images), files=tuple(files),
    )


def parse_roster(text: str) -> list[StaffRecord]:
    """Parse a department contacts page (clean_text output) into staff records.

    Structure per person (as clean_text renders it):
        Name / Title / Phone# … / Fax# … / mail to: / email
    An email line terminates each record. Emails that the source splits across a
    line break (``local\\n@njit.edu``) are rejoined first.
    """
    low = text.lower()
    start = -1
    for anchor in _ROSTER_ANCHORS:
        i = low.find(anchor)
        if i != -1:
            start = i + len(anchor)
            break
    if start == -1:
        return []
    block = text[start:]
    for marker in _BLOCK_END:
        i = block.lower().find(marker)
        if i != -1:
            block = block[:i]
    # Rejoin an address split right before the @ (Erixson on the live page).
    block = re.sub(r"\n+\s*@", "@", block)

    records: list[StaffRecord] = []
    buf: list[str] = []
    for raw in block.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _EMAIL.match(line):
            fields = [b for b in buf if b.lower() != "mail to:"]
            if len(fields) >= 2:
                phone = ""
                for f in fields:
                    m = _PHONE.search(f)
                    if m:
                        phone = m.group(1)
                        break
                records.append(
                    StaffRecord(name=fields[0], title=fields[1], phone=phone, email=line)
                )
            buf = []
        else:
            buf.append(line)
    return records


def _merge_person_attrs(conn, pid: int, updates: dict) -> None:
    """Merge contact fields into a Person node's attrs (preserving anything already set)."""
    row = conn.execute("SELECT attrs FROM nodes WHERE id=?", (pid,)).fetchone()
    attrs = json.loads(row[0]) if row and row[0] else {}
    for k, v in updates.items():
        if v:
            attrs[k] = v
    conn.execute("UPDATE nodes SET attrs=?, updated_at=datetime('now') WHERE id=?",
                 (json.dumps(attrs), pid))


def ingest_eos(conn, result: EntryResult, source: str = "crawler") -> dict:
    """Write an EntryResult into the DB under ONE 'eos' org (under njit):
      - staff -> Person + has_role(category='staff') + contact attrs (KG)
      - prose -> knowledge_items type='policy' (IN the served corpus, NOT office_page),
        keyed by source_url, content-hash for recrawl change detection, figures in metadata.
    Idempotent: unchanged pages are skipped; changed pages version-bump (old deactivated).
    Does NOT commit (caller owns the transaction)."""
    org_id = ensure_org(conn, EOS_SLUG, EOS_NAME, parent_slug="njit", type="office")
    sync_org_nodes(conn)

    for s in result.staff:
        key = f"{source}/{EOS_SLUG}/{_slug(s.name)}"
        pid = project_appointment(
            conn, person_key=key, name=s.name, org_id=org_id, category="staff",
            titles=[s.title], source_section="contacts", source=source)
        _merge_person_attrs(conn, pid, {"email": s.email, "phone": s.phone})

    inserted = updated = unchanged = 0
    for p in result.prose:
        ch = hashlib.sha1(p.content.encode("utf-8")).hexdigest()
        meta = {
            "natural_key": p.source_url,
            "content_hash": ch,
            "images": [list(i) for i in p.images],
            "files": [list(f) for f in p.files],
            "source": "eos_crawl",
        }
        row = conn.execute(
            "SELECT id, json_extract(metadata,'$.content_hash') FROM knowledge_items "
            "WHERE is_active=1 AND org_id=? AND json_extract(metadata,'$.natural_key')=? "
            "AND created_by=?", (org_id, p.source_url, source)).fetchone()
        if row and row[1] == ch:
            unchanged += 1
            continue
        if row:
            conn.execute("UPDATE knowledge_items SET is_active=0, updated_at=datetime('now') "
                         "WHERE id=?", (row[0],))
            updated += 1
        else:
            inserted += 1
        conn.execute(
            "INSERT INTO knowledge_items(org_id,type,title,content,metadata,source_url,"
            "version,is_active,created_by) VALUES(?,?,?,?,?,?,1,1,?)",
            (org_id, "policy", p.title, p.content, json.dumps(meta), p.source_url, source))

    return {"org_id": org_id, "staff": len(result.staff), "prose_inserted": inserted,
            "prose_updated": updated, "prose_unchanged": unchanged,
            "skipped": len(result.skipped)}
