"""EOS (Environmental & Operational Services) department crawler.

EOS-specific extractor modeled on the YWCC crawler (explore.py), reusing the
web_crawler spine (fetch / clean / link discovery). Brings data ONLY — fetch →
mechanically clean → emit records for the caller to store in KB/KG. It makes NO
serving/gating/staging decisions (2026-06-23 hard line).

Spec: docs/superpowers/specs/2026-06-23-eos-crawl-design.md
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, replace
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment
from v2.core.ingestion.people_editor import _slug
from v2.core.ingestion.web_crawler import clean_text, normalize_url, select_links

EOS_SLUG = "eos"
EOS_NAME = "Environmental & Operational Services"

_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@njit\.edu$", re.I)
_PHONE = re.compile(r"Phone#\s*([0-9][0-9\-]+)", re.I)
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


def _canon(url: str) -> str:
    """Canonicalize an njit.edu URL to https. The hub emits absolute http:// links whose
    http→https redirect our fetcher does not follow (they return the home-page stub), so
    folding scheme here removes that whole class of duplicate."""
    return re.sub(r"^http://", "https://", url)


def _in_scope(seed_path: str, url_path: str) -> bool:
    """An EOS office site is bounded by the SEED's OWN path prefix — NOT the shared crawler's
    parent-dir scope (which resolves a non-directory seed like /environmentalsafety to "/" and
    crawls the whole university). Follow the seed page itself and its subtree only."""
    sp = seed_path.rstrip("/")
    return url_path.rstrip("/") == sp or url_path.startswith(sp + "/")


def crawl_entry(seed: str, fetch, max_depth: int = 4, budget: int = 300):
    """DFS from ``seed``, following links UNDER THE SEED'S OWN PATH deep. Yields ``(url, html)``.

    Reuses the web_crawler spine (``select_links`` for asset-dropping link extraction) but keeps
    RAW HTML (which ``crawl_site`` discards) and applies an EOS-specific seed-prefix scope so a
    landing-page seed can't wander the whole site. Scheme canonicalized to https; depth- and
    budget-bounded, dedup + loop-guarded. ``fetch(url) -> html|None`` is injected.
    """
    seed = _canon(normalize_url(seed, seed))
    seed_path = urlparse(seed).path
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
                if u not in seen and _in_scope(seed_path, urlparse(u).path):
                    seen.add(u)
                    stack.append((u, depth + 1))


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
    for url, html in crawl_entry(seed, fetch, max_depth=max_depth, budget=budget):
        kind = classify_page(html)
        if kind == "staff-roster":
            for s in parse_roster(clean_text(html)):
                if s.email not in seen_emails:
                    seen_emails.add(s.email)
                    res.staff.append(s)
        elif kind == "prose":
            page = extract_prose(url, html)
            if page is None:
                continue
            h = hashlib.sha1(page.content.encode("utf-8")).hexdigest()
            if h not in by_hash:
                by_hash[h] = page
                order.append(h)
            elif _url_rank(page.source_url) < _url_rank(by_hash[h].source_url):
                by_hash[h] = page                       # prefer the cleaner alias URL
        else:
            res.skipped.append(url)
    res.prose = [by_hash[h] for h in order]
    _strip_recurring_assets(res.prose)
    return res


def _strip_recurring_assets(pages: list[ProsePage]) -> None:
    """Remove site-wide RECURRING assets (e.g. an announcement PDF that appears on most
    pages) — they're chrome, not page content. An asset is recurring when it appears on
    >= 3 pages AND on more than half of them; page-specific assets (the campus map) stay.
    Mutates ``pages`` in place (frozen dataclasses are replaced)."""
    n = len(pages)
    if n < 3:
        return
    files = Counter(u for p in pages for u, _ in p.files)
    images = Counter(u for p in pages for u, _ in p.images)
    recurring = {
        u for c in (files, images) for u, k in c.items() if k >= 3 and k > n / 2
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
    start = low.find("department staff")
    if start == -1:
        return []
    block = text[start + len("department staff"):]
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
