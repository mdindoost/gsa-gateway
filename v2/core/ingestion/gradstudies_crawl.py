"""Graduate Studies (Office of Graduate Studies / GSO) crawler.

GSO-specific extractor — a copy of the EOS crawler adapted for www.njit.edu/graduatestudies/:
the same path-prefix DFS and verbatim prose ingest, with a unit-header-aware contact.php roster
parser. Reuses the web_crawler spine (fetch / clean / link discovery). Brings data ONLY — fetch →
mechanically clean → emit records for the caller to store in KB/KG. It makes NO serving/gating/
staging decisions (2026-06-23 hard line).

Spec: docs/superpowers/specs/2026-06-24-graduate-studies-crawl-design.md
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.graph.project import project_appointment
from v2.core.ingestion.people_editor import _slug
from v2.core.ingestion.web_crawler import clean_text, normalize_url, select_links

logger = logging.getLogger(__name__)

GRAD_SLUG = "graduate-studies"
GRAD_NAME = "Graduate Studies"

_EMAIL = re.compile(r"^[A-Za-z0-9._%+'-]+@njit\.edu$", re.I)
_PHONE = re.compile(r"\b(\d{3}-\d{3}-\d{4})\b")
_ROSTER_ANCHOR = "personnel"            # the GSO contact.php Personnel block header
# Site-chrome markers that can follow the staff block in clean_text output.
_BLOCK_END = ("popular searches", "in this section", "appointments")
# Role words that mark a line as a TITLE (vs a name or a section header).
_TITLE_CUES = ("provost", "dean", "director", "coordinator", "manager", "assistant",
               "associate", "professor", "officer", "specialist", "administrator",
               "advisor", "vice president", "office", "chair", "analyst", "secretary")


@dataclass(frozen=True)
class StaffRecord:
    name: str
    title: str
    phone: str
    email: str
    unit: str = ""        # functional sub-section header on contact.php (e.g. "Graduate Student Awards")


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
    warnings: list[str] = field(default_factory=list)   # unparseable roster rows (flag, never drop silently)


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


def crawl_entry(seed: str, fetch, max_depth: int = 4, budget: int = 300, stats: dict | None = None):
    """DFS from ``seed``, following links UNDER THE SEED'S OWN PATH deep. Yields ``(url, html)``.

    Reuses the web_crawler spine (``select_links`` for asset-dropping link extraction) but keeps
    RAW HTML (which ``crawl_site`` discards) and applies an EOS-specific seed-prefix scope so a
    landing-page seed can't wander the whole site. Scheme canonicalized to https; depth- and
    budget-bounded, dedup + loop-guarded. ``fetch(url) -> html|None`` is injected. If ``stats`` is
    given, ``stats['truncated']`` is set True when the budget is hit with links still queued."""
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
    """Crawl the GSO entry point and extract: contact.php -> staff (KG), every page -> prose
    (KB), empty shells -> skipped (flagged). COVERAGE RULE: unlike EOS, a roster page is NOT
    dropped from prose — contact.php's office-contact prose (email/phone/hours/appointment
    steps) is kept too. Prose deduped by content hash (collapsing .php / clean-URL aliases),
    keeping the cleanest URL. Brings data only; no DB writes."""
    res = EntryResult(seed=_canon(normalize_url(seed, seed)), staff=[], prose=[], skipped=[])
    seen_emails: set[str] = set()
    by_hash: dict[str, ProsePage] = {}
    order: list[str] = []
    stats: dict = {}
    for url, html in crawl_entry(seed, fetch, max_depth=max_depth, budget=budget, stats=stats):
        staff, warns = parse_roster(clean_text(html))
        res.warnings.extend(warns)
        for s in staff:
            if s.email not in seen_emails:
                seen_emails.add(s.email)
                res.staff.append(s)
        # COVERAGE RULE: do NOT `continue` on a roster page — keep its prose too.
        page = extract_prose(url, html)
        if page is None:
            if not staff:
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
    store). Roster takes precedence for the LABEL — but note the contacts page also yields
    prose (kept), see extract_entry. ``parse_roster`` now returns ``(records, warnings)``."""
    if parse_roster(clean_text(html))[0]:
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


def _is_title(line: str) -> bool:
    low = line.lower()
    return "(" in line or any(c in low for c in _TITLE_CUES)


def parse_roster(text: str) -> tuple[list[StaffRecord], list[str]]:
    """Parse the GSO contact.php 'Personnel' block. Each person renders as
        [section header?] / Name / Title(+more titles) / bare-phone / email
    Phone-anchored: a (bare-phone, email) adjacent pair marks a record tail; the lines
    above the phone are name + title(s), optionally preceded by a section header. Titles
    are detected by role cues so the name (no cue) and a leading section header (no cue,
    not a title) are told apart — the header becomes ``unit``, never the name. Anti-fab:
    a chunk that can't yield (name, >=1 title, email) is a WARNING, never dropped/invented.
    Returns ([], []) for any non-Personnel page (anchor absent) so it falls through to prose."""
    low = text.lower()
    i = low.find(_ROSTER_ANCHOR)
    if i == -1:
        return [], []
    block = text[i + len(_ROSTER_ANCHOR):]
    for marker in _BLOCK_END:
        j = block.lower().find(marker)
        if j != -1:
            block = block[:j]
    block = re.sub(r"\n+\s*@", "@", block)               # rejoin emails split before @
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]

    records: list[StaffRecord] = []
    warnings: list[str] = []
    seen: set[str] = set()
    chunk: list[str] = []          # lines accumulated since the previous record's email
    current_unit = ""              # section header persists across its members (header shown once)
    for ln in lines:
        if _EMAIL.match(ln):
            email = ln
            # phone = the last chunk line matching a bare phone
            phone, head = "", []
            for k in range(len(chunk) - 1, -1, -1):
                m = _PHONE.search(chunk[k])
                if m:
                    phone = m.group(1)
                    head = chunk[:k]                     # everything above the phone
                    break
            if not head:
                warnings.append(f"no phone/name above email {email!r}")
                chunk = []
                continue
            # trailing title lines; the line just above them is the name; rest above = unit header
            t = len(head)
            while t > 0 and _is_title(head[t - 1]):
                t -= 1
            if t == 0 or t >= len(head):                 # no name, or no title
                warnings.append(f"unparseable record near {email!r}: {head!r}")
                chunk = []
                continue
            name = head[t - 1]
            if t - 2 >= 0:                               # a section header leads this chunk
                current_unit = head[t - 2]               # update; persists to later members
            title = head[t]                              # first title line
            if name not in seen:
                seen.add(name)
                records.append(StaffRecord(name=name, title=title, phone=phone,
                                           email=email, unit=current_unit))
            chunk = []
        else:
            chunk.append(ln)
    return records, warnings


def _merge_person_attrs(conn, pid: int, updates: dict) -> None:
    """Merge contact fields into a Person node's attrs (preserving anything already set)."""
    row = conn.execute("SELECT attrs FROM nodes WHERE id=?", (pid,)).fetchone()
    attrs = json.loads(row[0]) if row and row[0] else {}
    for k, v in updates.items():
        if v:
            attrs[k] = v
    conn.execute("UPDATE nodes SET attrs=?, updated_at=datetime('now') WHERE id=?",
                 (json.dumps(attrs), pid))


def ingest_gradstudies(conn, result: EntryResult, source: str = "crawler") -> dict:
    """Write an EntryResult into the DB under ONE 'eos' org (under njit):
      - staff -> Person + has_role(category='staff') + contact attrs (KG)
      - prose -> knowledge_items type='policy' (IN the served corpus, NOT office_page),
        keyed by source_url, content-hash for recrawl change detection, figures in metadata.
    Idempotent: unchanged pages are skipped; changed pages version-bump (old deactivated).
    Does NOT commit (caller owns the transaction)."""
    org_id = ensure_org(conn, GRAD_SLUG, GRAD_NAME, parent_slug="njit", type="office")
    sync_org_nodes(conn)

    for s in result.staff:
        key = f"{source}/{GRAD_SLUG}/{_slug(s.name)}"
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
            "source": "gradstudies_crawl",
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
