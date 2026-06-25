"""Office of Global Initiatives (OGI) crawler (Crawling 2.1, office rollout #3).

A copy of the Admissions / Registrar office crawler adapted for www.njit.edu/global/: same
path-prefix DFS + verbatim prose ingest reusing the web_crawler spine. Brings data ONLY — fetch ->
mechanically clean -> emit records for the caller to store in KB/KG; NO serving/gating/staging
decisions (2026-06-23 hard line). OGI content is immigration-heavy (F-1/J-1/OPT/STEM/H-1B); the
serve-time heads-up handling covers that — the crawler just brings it verbatim.

DELTA vs Admissions (the only new code):
  * The roster parser is "VIEW PROFILE"-ANCHORED. On the staff page each person renders as a detail
    block: a 'View Profile' marker, then  Name / full-title(s) / email / phone / "Official" / location.
    (Each block is preceded by a summary card 'Name / short-title / View Profile' and section headers
    like 'Executive Director' — both ignored; only the detail block after a 'View Profile' is parsed.)
  * People are extracted ONLY from the staff page (URL-gated to /office-global-initiatives-staff).
  * Names may carry a middle initial ('James A Jones', 'Vaughn C. Rogers') — _NAME allows it.
  * Phone captured (the line after the email). Function-mailbox guard retained (+ 'global').
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

OGI_SLUG = "ogi"
OGI_NAME = "Office of Global Initiatives"

# The staff page (and ONLY this page) carries the office roster. Matched by EXACT path suffix.
_STAFF_PATH_SUFFIX = "/office-global-initiatives-staff"
_VIEW_PROFILE = "view profile"          # the per-person detail-block marker

_EMAIL = re.compile(r"^[\w.+-]+@njit\.edu$", re.I)
_PHONE = re.compile(r"^\d{3}-\d{3}-\d{4}$")
# A roster NAME is 'Given [Initial[.]]* Surname' — Title-case tokens, optional middle initial
# ('James A Jones', 'Vaughn C. Rogers') and parenthetical nickname; apostrophe/hyphen surnames OK.
# Letters are Unicode-aware so diacritic names ('José Álvarez', 'Begoña') parse, not silent-drop.
_LCAP = r"[A-ZÀ-ÖØ-Þ]"                       # an uppercase letter (Latin-1 incl. accented)
_LCH = r"(?:[^\W\d_]|['’.-])"                # a name body char: any Unicode letter, or ' ’ . -
_NAME = re.compile(
    rf"^{_LCAP}{_LCH}+(?: +\([^)]+\))?(?: +{_LCAP}{_LCH}*)* +{_LCAP}{_LCH}+$", re.UNICODE)
_ROLE_KEYWORDS = (
    "director", "manager", "coordinator", "assistant", "associate", "provost", "officer",
    "specialist", "counselor", "advisor", "adviser", "dean", "analyst", "executive",
    "administrative", "representative", "services",
)
_FUNCTION_MAILBOXES = frozenset({
    "global", "info", "contact", "help", "international", "ogi", "studyabroad", "isss",
})

_ASSET_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif")


def _has_role_keyword(line: str) -> bool:
    low = line.lower()
    return any(k in low for k in _ROLE_KEYWORDS)


def _clean_email(raw: str) -> str:
    return raw.replace("​", "").strip()


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
    """Parse the OGI staff page. 'VIEW PROFILE'-anchored: each person is the detail block right
    after a 'View Profile' marker — Name / full-title(s) / email / phone / 'Official' / location.
    Summary cards and section headers are skipped (only post-'View Profile' blocks are parsed).
    Anti-fab: name must be a person-name shape with no role keyword, a title must exist, a function
    mailbox is never a person — a block that fails WARNS, never fabricates. Returns ([], []) for any
    page with no 'View Profile' roster (so non-staff pages fall through to prose)."""
    lines = [" ".join(ln.replace("\xa0", " ").split())
             for ln in text.splitlines() if ln.strip()]
    personal_emails = sum(
        1 for ln in lines
        if _EMAIL.match(ln) and _clean_email(ln).split("@", 1)[0].lower() not in _FUNCTION_MAILBOXES)

    records: list[StaffRecord] = []
    warnings: list[str] = []
    seen: set[str] = set()
    i = 0
    while i < len(lines):
        if lines[i].lower() != _VIEW_PROFILE:
            i += 1
            continue
        j = i + 1
        if j >= len(lines):
            break
        name = lines[j]
        j += 1
        titles: list[str] = []
        while j < len(lines) and not _EMAIL.match(lines[j]) and lines[j].lower() != _VIEW_PROFILE:
            titles.append(lines[j])
            j += 1
        if j >= len(lines) or not _EMAIL.match(lines[j]):
            warnings.append(f"no email in detail block for {name!r}")
            i = j
            continue
        email = _clean_email(lines[j])
        j += 1
        phone = lines[j] if j < len(lines) and _PHONE.match(lines[j]) else ""
        if email.split("@", 1)[0].lower() in _FUNCTION_MAILBOXES:
            warnings.append(f"function mailbox in detail block (not a person): {email!r}")
            i = j
            continue
        if not _NAME.match(name) or _has_role_keyword(name):
            warnings.append(f"detail block head is not a person name (skipped): {name!r}")
            i = j
            continue
        if not titles:
            warnings.append(f"no title under name {name!r}")
            i = j
            continue
        title = " ".join(titles)
        if name in seen:
            warnings.append(f"duplicate roster name (possible homonym), kept first: {name!r}")
        else:
            seen.add(name)
            records.append(StaffRecord(name=name, title=title, phone=phone, email=email,
                                       unit=OGI_NAME, titles=(title,)))
        i = j

    if len(records) != personal_emails:
        warnings.append(
            f"parsed {len(records)} people but found {personal_emails} personal emails on the page "
            f"— possible roster-structure change; review before trusting the roster")
    return records, warnings


def extract_entry(seed: str, fetch, max_depth: int = 4, budget: int = 300) -> EntryResult:
    """Crawl the OGI entry point. PEOPLE are extracted ONLY from the staff page (URL-gated). EVERY
    page is kept as prose (verbatim, source of truth). Prose deduped by content hash."""
    res = EntryResult(seed=_canon(normalize_url(seed, seed)), staff=[], prose=[], skipped=[])
    seen_names: set[str] = set()
    by_hash: dict[str, ProsePage] = {}
    order: list[str] = []
    stats: dict = {}
    for url, html in crawl_entry(seed, fetch, max_depth=max_depth, budget=budget, stats=stats):
        if urlparse(url).path.rstrip("/").endswith(_STAFF_PATH_SUFFIX):   # exact-path people gate
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


def ingest_ogi(conn, result: EntryResult, source: str = "crawler") -> dict:
    """Write an EntryResult under the EXISTING 'ogi' org (id 16, Office of Global Initiatives, under
    njit): staff -> Person + has_role(category='staff') + email/phone attrs; prose -> knowledge_items
    type='policy', content-hashed for recrawl change detection. Idempotent; does NOT commit."""
    org_id = ensure_org(conn, OGI_SLUG, OGI_NAME, parent_slug="njit", type="office")
    sync_org_nodes(conn)

    for s in result.staff:
        key = f"{source}/{OGI_SLUG}/{_slug(s.name)}"
        pid = project_appointment(
            conn, person_key=key, name=s.name, org_id=org_id, category="staff",
            titles=list(s.titles) or [s.title], source_section=(s.unit or OGI_NAME), source=source)
        _merge_person_attrs(conn, pid, {"email": s.email, "phone": s.phone})

    inserted = updated = unchanged = 0
    for p in result.prose:
        ch = hashlib.sha1(p.content.encode("utf-8")).hexdigest()
        meta = {
            "natural_key": p.source_url,
            "content_hash": ch,
            "images": [list(i) for i in p.images],
            "files": [list(f) for f in p.files],
            "source": "ogi_crawl",
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
