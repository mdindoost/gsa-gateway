"""Office of University Admissions crawler (Crawling 2.1, office rollout #2).

A copy of the Registrar / Bursar office crawler adapted for www.njit.edu/admissions/: the same
path-prefix DFS and verbatim prose ingest reusing the web_crawler spine (fetch / clean / link
discovery). Brings data ONLY — fetch -> mechanically clean -> emit records for the caller to store
in KB/KG. Makes NO serving/gating/staging decisions (2026-06-23 hard line).

Owner decisions (2026-06-24, escalated): full crawl, the live site is the source of truth; org 21
represents the OFFICE OF UNIVERSITY ADMISSIONS (whole /admissions/ subtree + full ~26-person team);
the grad-advisors directory page is PROSE ONLY (its 71 people are existing faculty cross-listed as
program advisors — mint 0 people). See docs/superpowers/specs/2026-06-24-admissions-crawl-design.md.

DELTA vs Registrar (the only new code):
  * The roster parser is EMAIL-ANCHORED + section-grouped, not a table. On the contact page each
    person renders as:  [section header] / Given Surname / <title line(s)> / localpart@njit.edu .
    A personal email closes a person; a section header sets the current unit and resets the buffer
    (cleanly discarding the office address/hours preamble and the duplicate "Surname, Given"
    leadership summary); a function mailbox resets without emitting. Anti-fab: the name line must be
    a 'Given Surname' shape AND carry no role keyword, and a person must have >=1 title line — a
    block that fails WARNS, never fabricates / silent-drops.
  * People are extracted ONLY from the contact-admissions page (URL-gated in extract_entry); every
    other page (incl. graduateadvisors.php) is prose-only. This enforces "grad-advisors = 0 people".
  * Emails are printed as visible text on the contact page, so they are captured inline by the
    parser (no mailto-href reader needed). Function-mailbox anti-fab guard retained.
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

ADMISSIONS_SLUG = "graduate-admissions"          # kept (avoid breaking refs); org 21
ADMISSIONS_NAME = "Office of University Admissions"

# The contact page (and ONLY this page) carries the office staff roster. Matched by EXACT path
# suffix (not a loose substring) so a hypothetical /contact-admissions-faq never opens the people gate.
_CONTACT_PATH_SUFFIX = "/contact-admissions"

# A personal njit.edu email line (the per-person anchor) — full-line anchored.
_EMAIL = re.compile(r"^[\w.+-]+@njit\.edu$", re.I)
# A roster NAME is 'Given Surname' (>=2 Title-case tokens), optionally with a parenthetical
# nickname ("Yenitza (Jenny) Ruiz") and apostrophe/hyphen surnames ("Shannon O'Brien").
_NAME = re.compile(
    r"^[A-Z][A-Za-z.'’-]+(?: +\([A-Za-z]+\))?(?: +[A-Z][A-Za-z.'’-]+)+$")
# Section headers that group the roster (set the person's unit + reset the parse buffer). Matched
# case-insensitively; "Recruitment - …" matched by prefix. Documented to THIS page (source of truth).
_SECTION_EXACT = frozenset({"university admissions", "operations", "leadership", "administration"})
# Role keywords mark a TITLE line — and so must NEVER appear in a name (anti name/title swap).
_ROLE_KEYWORDS = (
    "provost", "director", "recruiter", "manager", "coordinator", "assistant",
    "associate", "generalist", "clerk", "specialist", "counselor", "officer",
    "dean", "registrar", "analyst", "vice president", "president", "secretary",
    "advisor", "representative", "services", "enrollment",
)
# Anti-fab guard: a DEPARTMENTAL function mailbox is NOT a person's email.
_FUNCTION_MAILBOXES = frozenset({
    "admissions", "info", "contact", "help", "transfer", "finaid", "global",
    "gradstudies", "graduate", "enrollment", "records", "international",
})

_ASSET_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif")


def _has_role_keyword(line: str) -> bool:
    low = line.lower()
    return any(k in low for k in _ROLE_KEYWORDS)


def _is_section(line: str) -> bool:
    low = line.strip().lower()
    return low in _SECTION_EXACT or low.startswith("recruitment")


def _clean_email(raw: str) -> str:
    """Strip zero-width chars the page sprinkles into some mailto text (e.g. \\u200b)."""
    return raw.replace("​", "").strip()


@dataclass(frozen=True)
class StaffRecord:
    name: str
    title: str            # the (joined) title — a wrapped title is one logical title
    phone: str
    email: str
    unit: str = ""        # the section header this person fell under
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
    """DFS from ``seed`` under its OWN path prefix; yields ``(url, html)``. (Same spine as the
    other office crawlers.)"""
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
    """Parse the Office of University Admissions staff roster (the contact page).

    EMAIL-ANCHORED + section-grouped. Walking the cleaned text top-to-bottom:
      * a SECTION header sets the current unit and RESETS the line buffer (this discards the office
        address/hours preamble and the duplicate 'Surname, Given' leadership summary);
      * a FUNCTION mailbox resets the buffer without emitting (e.g. admissions@njit.edu);
      * a PERSONAL email closes the buffered person: name = first buffered line, title = the rest
        joined (a wrapped title is one logical title), email = this line.
    Anti-fab: the name must be a 'Given Surname' shape AND contain no role keyword, and a title must
    exist — a block that fails WARNS, never fabricates. Returns ([], []) for any page with no
    personal-email roster (so non-contact pages fall through to prose)."""
    # normalize non-breaking spaces / whitespace runs so 'Dimana\xa0Kornegay' reads as a name
    lines = [" ".join(ln.replace("\xa0", " ").split())
             for ln in text.splitlines() if ln.strip()]
    records: list[StaffRecord] = []
    warnings: list[str] = []
    seen: set[str] = set()
    buf: list[str] = []
    section = ""

    # personal-email lines = the ground-truth person count (used as a recount sanity check below).
    personal_emails = sum(
        1 for ln in lines
        if _EMAIL.match(ln) and _clean_email(ln).split("@", 1)[0].lower() not in _FUNCTION_MAILBOXES)

    for ln in lines:
        if _is_section(ln):
            section = ln
            buf = []
            continue
        if _EMAIL.match(ln):                             # a bare email line = person anchor
            email = _clean_email(ln)
            local = email.split("@", 1)[0].lower()
            if local in _FUNCTION_MAILBOXES:
                buf = []                                 # office mailbox — discard preceding noise
                continue
            if not buf:
                warnings.append(f"email with no preceding name/title: {email!r}")
                continue
            name, title_lines = buf[0], buf[1:]
            if not _NAME.match(name) or _has_role_keyword(name):
                warnings.append(f"roster block head is not a person name (skipped): {name!r}")
                buf = []
                continue
            if not title_lines:
                warnings.append(f"no title under name {name!r}")
                buf = []
                continue
            title = " ".join(title_lines)
            if name in seen:                             # never silent-drop a homonym
                warnings.append(f"duplicate roster name (possible homonym), kept first: {name!r}")
            else:
                seen.add(name)
                records.append(StaffRecord(
                    name=name, title=title, phone="", email=email,
                    unit=section or "University Admissions", titles=(title,)))
            buf = []
            continue
        # Structural section-header guard: title lines are never name-shaped (they carry a role
        # keyword or a lowercase connector), so two CONSECUTIVE name-shaped lines with no title /
        # email between them means the FIRST is an UNRECOGNIZED section header — NOT a person. Reset
        # it (set as the unit) and WARN, rather than fabricate a person + steal the next email. This
        # makes section handling robust to a header rename/addition on recrawl (the BLOCKER fix).
        if (_NAME.match(ln) and not _has_role_keyword(ln) and len(buf) == 1
                and _NAME.match(buf[0]) and not _has_role_keyword(buf[0])):
            warnings.append(f"unrecognized section header (not minted as a person): {buf[0]!r}")
            section = buf[0]
            buf = [ln]
            continue
        buf.append(ln)

    # Recount sanity check: a roster-structure change (renamed section, new layout) shows up as a
    # mismatch between people parsed and personal emails present. Loud warning, never silent.
    if len(records) != personal_emails:
        warnings.append(
            f"parsed {len(records)} people but found {personal_emails} personal emails on the page "
            f"— possible roster-structure change; review before trusting the roster")
    return records, warnings


def extract_entry(seed: str, fetch, max_depth: int = 4, budget: int = 300) -> EntryResult:
    """Crawl the Admissions entry point. PEOPLE are extracted ONLY from the contact-admissions page
    (URL-gated) so graduateadvisors.php and every other page never mint admissions staff. EVERY page
    (incl. the contact page and the advisors page) is kept as prose (verbatim, source of truth).
    Prose deduped by content hash (collapsing .php/clean-URL aliases). Brings data only; no writes."""
    res = EntryResult(seed=_canon(normalize_url(seed, seed)), staff=[], prose=[], skipped=[])
    seen_names: set[str] = set()
    by_hash: dict[str, ProsePage] = {}
    order: list[str] = []
    stats: dict = {}
    for url, html in crawl_entry(seed, fetch, max_depth=max_depth, budget=budget, stats=stats):
        if urlparse(url).path.rstrip("/").endswith(_CONTACT_PATH_SUFFIX):  # exact-path people gate
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
    """Mechanically clean a service page to VERBATIM main-content text (hard line #3)."""
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


def ingest_admissions(conn, result: EntryResult, source: str = "crawler") -> dict:
    """Write an EntryResult under the EXISTING 'graduate-admissions' org (id 21, Office of
    University Admissions, under njit):
      - staff -> Person + has_role(category='staff') + email attr (KG).
      - prose -> knowledge_items type='policy' (served corpus), keyed by source_url, content-hashed
        for recrawl change detection.
    Idempotent: unchanged pages skipped; changed pages version-bump. Recrawl is change-detection
    only (departures not retired — ND6 deferred). Does NOT commit (caller owns the transaction)."""
    org_id = ensure_org(conn, ADMISSIONS_SLUG, ADMISSIONS_NAME, parent_slug="njit", type="office")
    sync_org_nodes(conn)

    for s in result.staff:
        key = f"{source}/{ADMISSIONS_SLUG}/{_slug(s.name)}"
        pid = project_appointment(
            conn, person_key=key, name=s.name, org_id=org_id, category="staff",
            titles=list(s.titles) or [s.title], source_section=(s.unit or "University Admissions"),
            source=source)
        _merge_person_attrs(conn, pid, {"email": s.email})

    inserted = updated = unchanged = 0
    for p in result.prose:
        ch = hashlib.sha1(p.content.encode("utf-8")).hexdigest()
        meta = {
            "natural_key": p.source_url,
            "content_hash": ch,
            "images": [list(i) for i in p.images],
            "files": [list(f) for f in p.files],
            "source": "admissions_crawl",
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
