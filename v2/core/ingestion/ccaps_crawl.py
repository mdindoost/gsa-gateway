"""Counseling Center (C-CAPS) crawler (Crawling 2.1, office rollout #6).

A copy of the OGI / Admissions office crawler adapted for www.njit.edu/counseling/: same path-prefix
DFS + verbatim prose ingest reusing the web_crawler spine. Brings data ONLY (2026-06-23 hard line).
Counseling is sensitive content (mental health) — served verbatim; the serve-time heads-up covers it.

DELTA vs Admissions (the only new code): the roster (on /counseling/c-caps-staff) is EMAIL-ANCHORED
with a CREDENTIAL suffix and Phone:/Email: label lines. Each person renders:
    Given Surname, <Credential(s)>   (e.g. 'Phyllis Bolling, Ph.D.', 'Maham Tariq, MA, LPC')
    <title line(s)>                  (e.g. Director / Licensed Psychologist)
    Phone: <number>
    Email:
    localpart@njit.edu
parse_roster is email-anchored: the email closes a person; within the block the NAME is the first
'Given Surname' line carrying no role keyword (the credential after the first comma is stripped from
the stored name — kept verbatim in prose); the lines between the name and the Phone:/Email: labels are
the title(s); the phone is read from the 'Phone:' line. Anti-fab: a function mailbox is never a person,
a block with no name / no title WARNS, recount warning if people != personal-email count.
People are URL-gated to /counseling/c-caps-staff; names may carry diacritics/middle initials.
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

CCAPS_SLUG = "counseling"
CCAPS_NAME = "Counseling Center (C-CAPS)"

_STAFF_PATH = "/counseling/c-caps-staff"          # the ONLY page that mints people (exact path)

_EMAIL = re.compile(r"^[\w.+-]+@njit\.edu$", re.I)
_PHONE_IN = re.compile(r"\(?\d{3}\)?[-\s]\d{3}-\d{4}")
_LCAP = r"[A-ZÀ-ÖØ-Þ]"
_LCH = r"(?:[^\W\d_]|['’.-])"
# A NAME (the part before any credential comma) is 'Given [Initial] Surname' — Title-case tokens.
_NAME = re.compile(
    rf"^{_LCAP}{_LCH}+(?: +{_LCAP}{_LCH}*)* +{_LCAP}{_LCH}+$", re.UNICODE)
_ROLE_KEYWORDS = (
    "director", "manager", "coordinator", "assistant", "associate", "provost", "officer",
    "specialist", "counselor", "counsel", "advisor", "adviser", "psychologist", "clinician",
    "social worker", "therapist", "staff", "licensed", "professional", "administrative",
    "liaison", "educator", "intern", "trainee", "psychiatrist", "worker",
)
_FUNCTION_MAILBOXES = frozenset({
    "counseling", "ccaps", "c-caps", "info", "contact", "help", "wellness",
})
_META_PREFIXES = ("phone:", "email:", "fax:", "office:", "location:")
_ASSET_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif")


def _has_role_keyword(line: str) -> bool:
    low = line.lower()
    return any(k in low for k in _ROLE_KEYWORDS)


def _clean_email(raw: str) -> str:
    return raw.replace("​", "").strip()


def _is_name(line: str) -> bool:
    """A roster name line: the part before the first comma is 'Given Surname' with no role keyword
    (the part after the comma, if any, is a credential like 'Ph.D.' / 'MA, LPC')."""
    cand = line.split(",", 1)[0].strip()
    return bool(_NAME.match(cand)) and not _has_role_keyword(cand)


def _is_meta(line: str) -> bool:
    low = line.lower()
    return any(low.startswith(p) for p in _META_PREFIXES) or bool(_PHONE_IN.fullmatch(line.strip()))


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
    """Parse the C-CAPS staff page (email-anchored). The email closes a person; within the block the
    name is the first 'Given Surname' line (credential after the first comma stripped from the stored
    name), the non-meta lines between name and the Phone:/Email: labels are the title(s), and the
    phone is read from the 'Phone:' line. Anti-fab: function mailbox never a person; no name / no title
    WARNS; recount warning if people != personal-email count. Returns ([], []) for a page with no
    personal-email roster (so non-staff pages fall through to prose)."""
    lines = [" ".join(ln.replace("\xa0", " ").split())
             for ln in text.splitlines() if ln.strip()]
    personal_emails = sum(
        1 for ln in lines
        if _EMAIL.match(ln) and _clean_email(ln).split("@", 1)[0].lower() not in _FUNCTION_MAILBOXES)

    records: list[StaffRecord] = []
    warnings: list[str] = []
    seen: set[str] = set()
    buf: list[str] = []
    for ln in lines:
        if not _EMAIL.match(ln):
            buf.append(ln)
            continue
        email = _clean_email(ln)
        if email.split("@", 1)[0].lower() in _FUNCTION_MAILBOXES:
            buf = []
            continue
        # The name is the 'Given Surname' line CLOSEST to the title/phone/email block (the last
        # _is_name line before the first meta line), so an earlier name-shaped section header (e.g.
        # a future 'Wellness Program') is skipped rather than minted as a fabricated person.
        meta_idx = next((k for k, l in enumerate(buf) if _is_meta(l)), len(buf))
        cand_idxs = [k for k in range(meta_idx) if _is_name(buf[k])]
        if not cand_idxs:
            warnings.append(f"no person name in block before email {email!r}")
            buf = []
            continue
        name_idx = cand_idxs[-1]
        name = buf[name_idx].split(",", 1)[0].strip()        # drop the credential suffix
        titles = [l for l in buf[name_idx + 1:] if not _is_meta(l)]
        phone = ""
        for l in buf[name_idx + 1:]:                          # the PERSON's 'Phone:' line only —
            if l.lower().startswith("phone:"):               # not the office number in the preamble
                m = _PHONE_IN.search(l)
                if m:
                    phone = m.group(0)
                    break
        if not titles:
            warnings.append(f"no title under name {name!r}")
            buf = []
            continue
        if name in seen:
            warnings.append(f"duplicate roster name (possible homonym), kept first: {name!r}")
        else:
            seen.add(name)
            records.append(StaffRecord(name=name, title=titles[0], phone=phone, email=email,
                                       unit=CCAPS_NAME, titles=tuple(titles)))
        buf = []

    if len(records) != personal_emails:
        warnings.append(
            f"parsed {len(records)} people but found {personal_emails} personal emails on the page "
            f"— possible roster-structure change; review before trusting the roster")
    return records, warnings


def extract_entry(seed: str, fetch, max_depth: int = 4, budget: int = 300) -> EntryResult:
    """Crawl the C-CAPS entry point. PEOPLE only from /counseling/c-caps-staff (URL-gated, exact path).
    Every page kept as prose (verbatim). Prose deduped by content hash."""
    res = EntryResult(seed=_canon(normalize_url(seed, seed)), staff=[], prose=[], skipped=[])
    seen_names: set[str] = set()
    by_hash: dict[str, ProsePage] = {}
    order: list[str] = []
    stats: dict = {}
    for url, html in crawl_entry(seed, fetch, max_depth=max_depth, budget=budget, stats=stats):
        if urlparse(url).path.rstrip("/") == _STAFF_PATH:                 # exact-path people gate
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


def ingest_ccaps(conn, result: EntryResult, source: str = "crawler") -> dict:
    """Write an EntryResult under the EXISTING 'counseling' org (id 19, under njit): staff -> Person +
    has_role(category='staff') + email/phone attrs; prose -> knowledge_items type='policy',
    content-hashed for recrawl change detection. Idempotent; does NOT commit."""
    org_id = ensure_org(conn, CCAPS_SLUG, CCAPS_NAME, parent_slug="njit", type="office")
    sync_org_nodes(conn)

    for s in result.staff:
        key = f"{source}/{CCAPS_SLUG}/{_slug(s.name)}"
        pid = project_appointment(
            conn, person_key=key, name=s.name, org_id=org_id, category="staff",
            titles=list(s.titles) or [s.title], source_section=(s.unit or CCAPS_NAME), source=source)
        _merge_person_attrs(conn, pid, {"email": s.email, "phone": s.phone})

    inserted = updated = unchanged = 0
    for p in result.prose:
        ch = hashlib.sha1(p.content.encode("utf-8")).hexdigest()
        meta = {
            "natural_key": p.source_url,
            "content_hash": ch,
            "images": [list(i) for i in p.images],
            "files": [list(f) for f in p.files],
            "source": "ccaps_crawl",
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
