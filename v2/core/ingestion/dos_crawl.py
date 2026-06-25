"""Dean of Students (DOS) crawler (Crawling 2.1, office rollout #5).

A copy of the OGI / Admissions office crawler adapted for www.njit.edu/dos/: same path-prefix DFS +
verbatim prose ingest reusing the web_crawler spine. Brings data ONLY (2026-06-23 hard line).

DELTA vs OGI (the only new code): the roster (on /dos/contact.php) has a THIRD shape — each person is
a block that ENDS with a 'View Profile' marker:
    <role/section header>  /  Surname, Given  /  <title line(s)>  /  View Profile
There is NO per-person email (only the departmental dos@ mailbox), and names render 'Surname, Given'
(reordered to 'Given Surname' for KG consistency). So:
  * parse_roster splits the roster on 'View Profile' (the per-person END delimiter) and, within each
    block, the name is the 'Surname, Given' line that carries no role keyword; the line(s) after it
    (up to the marker) are the title; the line before it is the section header (persisted to the next
    headerless block — the last person reuses the prior 'Administrative Staff' header).
  * People are URL-gated to /dos/contact.php. Names may carry diacritics/middle initials.
  * No email/phone is published per person → StaffRecord carries name + title(s) only.
Anti-fab: a block with no 'Surname, Given' (no-role-keyword) name is skipped (preamble/non-person); a
block with a name but no title WARNS; a recount warning fires if people parsed != 'View Profile' count.
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

DOS_SLUG = "dean-of-students"
DOS_NAME = "Dean of Students"

# The contact page (and ONLY this page) carries the office roster. Matched by EXACT path so a
# hypothetical nested */contact.php (e.g. /dos/titleix/contact.php) can't open the people gate.
_CONTACT_PATH = "/dos/contact.php"
_VIEW_PROFILE = "view profile"          # the per-person END marker

_LCAP = r"[A-ZÀ-ÖØ-Þ]"                   # an uppercase letter (Latin-1 incl. accented)
_LCH = r"(?:[^\W\d_]|['’.-])"            # a name body char: any Unicode letter, or ' ’ . -
# A roster NAME line is 'Surname[, multi], Given [Initial]' — Title-case tokens both sides of the comma.
_SURNAME_GIVEN = re.compile(
    rf"^{_LCAP}{_LCH}+(?:[ -]{_LCAP}{_LCH}+)*,\s+{_LCAP}{_LCH}+(?: +{_LCAP}{_LCH}*)*$", re.UNICODE)
# Role keywords mark a TITLE / section header (vs a name). A name line must contain none.
_ROLE_KEYWORDS = (
    "dean", "director", "manager", "coordinator", "assistant", "associate", "provost",
    "officer", "specialist", "counselor", "counsel", "advisor", "adviser", "president",
    "vice", "administrative", "administrator", "staff", "executive", "hearing", "development",
    "analyst", "representative", "secretary", "chair", "title ix", "affairs",
)
_ASSET_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif")


def _has_role_keyword(line: str) -> bool:
    low = line.lower()
    return any(k in low for k in _ROLE_KEYWORDS)


def _reorder(surname_given: str) -> str:
    """'Boger, Marybeth' -> 'Marybeth Boger' (mechanical reorder, verbatim tokens)."""
    last, given = surname_given.split(",", 1)
    return f"{given.strip()} {last.strip()}"


@dataclass(frozen=True)
class StaffRecord:
    name: str
    title: str
    phone: str
    email: str
    unit: str = ""
    titles: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProsePage:
    title: str
    content: str
    source_url: str
    images: tuple[tuple[str, str], ...] = ()
    files: tuple[tuple[str, str], ...] = ()


@dataclass
class EntryResult:
    seed: str
    staff: list[StaffRecord]
    prose: list[ProsePage]
    skipped: list[str]
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)


def _main_region(soup: BeautifulSoup):
    return (
        soup.find("div", attrs={"role": "main"})
        or soup.find("main")
        or soup.find("div", class_="region-content")
        or soup
    )


def _canon(url: str) -> str:
    return re.sub(r"^http://", "https://", url)


def _in_scope(seed_path: str, url_path: str) -> bool:
    sp = seed_path.rstrip("/")
    return url_path.rstrip("/") == sp or url_path.startswith(sp + "/")


def crawl_entry(seed: str, fetch, max_depth: int = 4, budget: int = 300, stats: dict | None = None):
    seed = _canon(normalize_url(seed, seed))
    seed_path = urlparse(seed).path
    seen = {seed}
    stack: list[tuple[str, int]] = [(seed, 0)]
    while stack and len(seen) <= budget:
        url, depth = stack.pop()
        html = fetch(url)
        if not html:
            continue
        yield url, html
        if depth < max_depth:
            follow, _ = select_links(html, url, seed, relevance_gated=False)
            for u in sorted((_canon(u) for u in follow), reverse=True):
                if u not in seen and _in_scope(seed_path, urlparse(u).path):
                    seen.add(u)
                    stack.append((u, depth + 1))
    if stats is not None:
        stats["truncated"] = bool(stack)
        if stack:
            logger.warning("crawl_entry: hit budget %d at %s; %d links not followed",
                           budget, seed, len(stack))


def _url_rank(url: str) -> tuple[int, int]:
    return (1 if url.lower().endswith(".php") else 0, len(url))


def parse_roster(text: str) -> tuple[list[StaffRecord], list[str]]:
    """Parse the DOS contact-page roster. Blocks are delimited by the trailing 'View Profile' marker;
    in each block the name is the 'Surname, Given' line carrying no role keyword (reordered to
    'Given Surname'), the line(s) after it are the title, and the line before it is the section header
    (persisted to a later headerless block). No per-person email. Anti-fab: a no-name block is skipped
    (preamble/non-person); a name with no title WARNS; recount warning if people != 'View Profile' count.
    Returns ([], []) for a page with no 'View Profile' roster (so non-contact pages fall to prose)."""
    lines = [" ".join(ln.replace("\xa0", " ").split())
             for ln in text.splitlines() if ln.strip()]
    vp_count = sum(1 for ln in lines if ln.lower() == _VIEW_PROFILE)

    records: list[StaffRecord] = []
    warnings: list[str] = []
    seen: set[str] = set()
    last_section = ""
    buf: list[str] = []
    for ln in lines:
        if ln.lower() != _VIEW_PROFILE:
            buf.append(ln)
            continue
        # end of a person block — find the name line
        name_idxs = [k for k, l in enumerate(buf)
                     if _SURNAME_GIVEN.match(l) and not _has_role_keyword(l)]
        if not name_idxs:
            buf = []
            continue                                     # preamble / non-person block
        if len(name_idxs) > 1:
            warnings.append(f"multiple names in one block, kept first: {[buf[k] for k in name_idxs]!r}")
        ni = name_idxs[0]
        name = _reorder(buf[ni])
        # titles run from after the name to the NEXT name line (if a second name leaked into the
        # block) — so a second person's name can never be swallowed into the first's title.
        end = name_idxs[1] if len(name_idxs) > 1 else len(buf)
        titles = buf[ni + 1:end]
        section = buf[ni - 1] if ni > 0 else last_section
        if ni > 0:
            last_section = buf[ni - 1]
        if not titles:
            warnings.append(f"no title for {name!r}")
            buf = []
            continue
        title = " ".join(titles)
        if name in seen:
            warnings.append(f"duplicate roster name (possible homonym), kept first: {name!r}")
        else:
            seen.add(name)
            records.append(StaffRecord(name=name, title=title, phone="", email="",
                                       unit=section or DOS_NAME, titles=(title,)))
        buf = []

    if len(records) != vp_count:
        warnings.append(
            f"parsed {len(records)} people but found {vp_count} 'View Profile' markers "
            f"— possible roster-structure change; review before trusting the roster")
    return records, warnings


def extract_entry(seed: str, fetch, max_depth: int = 4, budget: int = 300) -> EntryResult:
    """Crawl the DOS entry point. PEOPLE only from /dos/contact.php (URL-gated). Every page kept as
    prose (verbatim). Prose deduped by content hash."""
    res = EntryResult(seed=_canon(normalize_url(seed, seed)), staff=[], prose=[], skipped=[])
    seen_names: set[str] = set()
    by_hash: dict[str, ProsePage] = {}
    order: list[str] = []
    stats: dict = {}
    for url, html in crawl_entry(seed, fetch, max_depth=max_depth, budget=budget, stats=stats):
        if urlparse(url).path.rstrip("/") == _CONTACT_PATH:                   # exact-path people gate
            staff, warns = parse_roster(clean_text(_main_text(html)))
            res.warnings.extend(warns)
            for s in staff:
                if s.name in seen_names:
                    res.warnings.append(
                        f"duplicate staff name across pages (possible homonym), kept first: {s.name!r}")
                    continue
                seen_names.add(s.name)
                res.staff.append(s)
        page = extract_prose(url, html)
        if page is None:
            res.skipped.append(url)
            continue
        h = hashlib.sha1(page.content.encode("utf-8")).hexdigest()
        if h not in by_hash:
            by_hash[h] = page
            order.append(h)
        elif _url_rank(page.source_url) < _url_rank(by_hash[h].source_url):
            by_hash[h] = page
    res.prose = [by_hash[h] for h in order]
    _strip_recurring_assets(res.prose)
    res.truncated = stats.get("truncated", False)
    return res


def _main_text(html: str) -> str:
    return str(_main_region(BeautifulSoup(html, "html.parser")))


def _strip_recurring_assets(pages: list[ProsePage]) -> None:
    n = len(pages)
    if n < 5:
        return
    files = Counter(u for p in pages for u, _ in p.files)
    images = Counter(u for p in pages for u, _ in p.images)
    recurring = {u for c in (files, images) for u, k in c.items() if k >= n - 1}
    if not recurring:
        return
    for i, p in enumerate(pages):
        pages[i] = replace(
            p,
            files=tuple((u, t) for u, t in p.files if u not in recurring),
            images=tuple((u, a) for u, a in p.images if u not in recurring),
        )


def extract_prose(url: str, html: str) -> ProsePage | None:
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
    return ProsePage(title=title, content=content, source_url=url,
                     images=tuple(images), files=tuple(files))


def _merge_person_attrs(conn, pid: int, updates: dict) -> None:
    row = conn.execute("SELECT attrs FROM nodes WHERE id=?", (pid,)).fetchone()
    attrs = json.loads(row[0]) if row and row[0] else {}
    for k, v in updates.items():
        if v:
            attrs[k] = v
    conn.execute("UPDATE nodes SET attrs=?, updated_at=datetime('now') WHERE id=?",
                 (json.dumps(attrs), pid))


def ingest_dos(conn, result: EntryResult, source: str = "crawler") -> dict:
    """Write an EntryResult under the EXISTING 'dean-of-students' org (id 20, under njit): staff ->
    Person + has_role(category='staff') (no email/phone published); prose -> knowledge_items
    type='policy', content-hashed for recrawl change detection. Idempotent; does NOT commit."""
    org_id = ensure_org(conn, DOS_SLUG, DOS_NAME, parent_slug="njit", type="office")
    sync_org_nodes(conn)

    for s in result.staff:
        key = f"{source}/{DOS_SLUG}/{_slug(s.name)}"
        pid = project_appointment(
            conn, person_key=key, name=s.name, org_id=org_id, category="staff",
            titles=list(s.titles) or [s.title], source_section=(s.unit or DOS_NAME), source=source)
        if s.email or s.phone:
            _merge_person_attrs(conn, pid, {"email": s.email, "phone": s.phone})

    inserted = updated = unchanged = 0
    for p in result.prose:
        ch = hashlib.sha1(p.content.encode("utf-8")).hexdigest()
        meta = {
            "natural_key": p.source_url,
            "content_hash": ch,
            "images": [list(i) for i in p.images],
            "files": [list(f) for f in p.files],
            "source": "dos_crawl",
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
