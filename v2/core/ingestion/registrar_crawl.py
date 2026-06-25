"""Office of the Registrar crawler.

Registrar-specific extractor — a copy of the Bursar / Graduate Studies (GSO) crawler adapted
for www.njit.edu/registrar/: the same path-prefix DFS and verbatim prose ingest, reusing the
web_crawler spine (fetch / clean / link discovery). Brings data ONLY — fetch → mechanically
clean → emit records for the caller to store in KB/KG. It makes NO serving/gating/staging
decisions (2026-06-23 hard line).

DELTA vs Bursar: the Registrar publishes a real STAFF DIRECTORY at /registrar/directory/
mallstaff.php as a three-column table (Name / Phone / Functions), rendering one person per
(name, phone, title) triplet. ``parse_roster`` is therefore POSITIONAL + table-anchored, not
email-anchored. Anti-fab boundary: a roster line only becomes a Person when it has a
'Surname, Given' shape (EVERY pre-comma token capitalized — accepts multi-token surnames like
"Van Pelt" yet rejects a comma-bearing TITLE whose clause contains lowercase words). An office
label, a comma-bearing title, or a homonym duplicate can never be fabricated/silently dropped —
such a row WARNS, never invents.

EMAILS: the table's VISIBLE text shows no email, but each name is a ``mailto:`` anchor carrying a
personal njit.edu address (e.g. jerry.trombella@njit.edu). ``clean_text`` strips hrefs, so
``_emails_from_html`` reads them from the raw HTML and attaches them per person (matched by the
anchor's 'Surname, Given' text) — honoring complete-coverage / never-withhold. A departmental
function mailbox is guarded out (never attached to a named Person).

Spec: docs/superpowers/specs/2026-06-24-registrar-crawl-design.md
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

REGISTRAR_SLUG = "registrar"
REGISTRAR_NAME = "Office of the Registrar"

# Registrar phones render space-separated ("973 596 3236") OR dash-separated; normalize to dashes.
_PHONE = re.compile(r"\b(\d{3})[-\s](\d{3})[-\s](\d{4})\b")
_PHONE_ONLY = re.compile(r"\d{3}[-\s]\d{3}[-\s]\d{4}")
# A roster NAME line is 'Surname, Given'. The anti-fab discriminator vs a comma-bearing TITLE
# ("Asst. Registrar for Graduation, Veterans …") is that EVERY pre-comma token is capitalized:
# a real surname (incl. multi-token "Van Pelt", "De La Cruz", "St. John") is all-Title-case, while
# a title clause contains lowercase words ("for", "and") and so never matches.
_NAME = re.compile(r"^[A-Z][A-Za-z.'’-]*(?:[ \-][A-Z][A-Za-z.'’-]*)*,\s+[A-Z]")
# The staff table's column header marks where the roster begins.
_ROSTER_HEADER = ("name", "phone", "functions")
# Site-chrome markers that can follow the staff block in clean_text output.
_BLOCK_END = ("popular searches", "in this section", "appointments")
# Anti-fab guard: a DEPARTMENTAL function mailbox is NOT a person's email — never attach it to a
# named Person. (The Registrar's named staff each publish a personal njit.edu address; this guards
# against a future generic mailbox anchored under a person-shaped link.)
_FUNCTION_MAILBOXES = frozenset({
    "registrar", "info", "contact", "admissions", "help", "transcripts", "graduation",
    "enrollment", "records", "veterans",
})


def _is_phone_only(line: str) -> bool:
    """True when a line is JUST a phone number (the next record's phone) — used to terminate a
    title block WITHOUT mistaking a title that merely CONTAINS a phone for a record boundary."""
    return bool(_PHONE_ONLY.fullmatch(line.strip()))


def _emails_from_html(html: str) -> dict[str, str]:
    """Capture per-person emails from the staff table's ``mailto:`` anchors (the page exposes them
    only in hrefs, which ``clean_text`` strips — so we read them from the raw HTML to honor
    complete-coverage / never-withhold). The anchor TEXT is the person's 'Surname, Given' name, so
    the email maps unambiguously by name. Anti-fab: only anchors whose text is a person-name shape
    are kept, and a departmental function mailbox is never attached. Returns {normalized name: email}."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, str] = {}
    for a in soup.select('a[href^="mailto:"]'):
        email = a["href"].split(":", 1)[1].split("?")[0].strip()
        txt = a.get_text(" ", strip=True)
        if not email or not _NAME.match(txt):           # anchor text must be a 'Surname, Given' name
            continue
        if email.split("@", 1)[0].lower() in _FUNCTION_MAILBOXES:
            continue                                     # function mailbox — not a personal address
        out[_norm_name(txt)] = email
    return out


@dataclass(frozen=True)
class StaffRecord:
    name: str
    title: str            # the primary (first) title line
    phone: str
    email: str
    unit: str = ""        # functional sub-section header on a contact page
    titles: tuple[str, ...] = ()   # ALL published title lines (a person may list >1) — never dropped


_ASSET_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif")


@dataclass(frozen=True)
class ProsePage:
    title: str
    content: str
    source_url: str
    images: tuple[tuple[str, str], ...] = ()   # (absolute_url, alt)
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
    warnings: list[str] = field(default_factory=list)   # unparseable roster rows (flag)


def _canon(url: str) -> str:
    """Canonicalize an njit.edu URL to https. The hub emits absolute http:// links whose
    http→https redirect our fetcher does not follow (they return the home-page stub), so
    folding scheme here removes that whole class of duplicate."""
    return re.sub(r"^http://", "https://", url)


def _in_scope(seed_path: str, url_path: str) -> bool:
    """An office site is bounded by the SEED's OWN path prefix — NOT the shared crawler's
    parent-dir scope (which resolves a non-directory seed to "/" and crawls the whole
    university). Follow the seed page itself and its subtree only."""
    sp = seed_path.rstrip("/")
    return url_path.rstrip("/") == sp or url_path.startswith(sp + "/")


def crawl_entry(seed: str, fetch, max_depth: int = 4, budget: int = 300, stats: dict | None = None):
    """DFS from ``seed``, following links UNDER THE SEED'S OWN PATH deep. Yields ``(url, html)``.

    Reuses the web_crawler spine (``select_links`` for asset-dropping link extraction) but keeps
    RAW HTML (which ``crawl_site`` discards) and applies a seed-prefix scope so a landing-page
    seed can't wander the whole site. Scheme canonicalized to https; depth- and budget-bounded,
    dedup + loop-guarded. ``fetch(url) -> html|None`` is injected. If ``stats`` is given,
    ``stats['truncated']`` is set True when the budget is hit with links still queued."""
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
    """Crawl the Registrar entry point and extract: the staff table -> roster (KG people),
    every page -> prose (KB), empty shells -> skipped (flagged). COVERAGE RULE: the staff page
    is NOT dropped from prose — its directory prose is kept too. Prose deduped by content hash
    (collapsing .php / clean-URL aliases), keeping the cleanest URL. Staff deduped by NAME (the
    roster carries no email). Brings data only; no DB writes."""
    res = EntryResult(seed=_canon(normalize_url(seed, seed)), staff=[], prose=[], skipped=[])
    seen_names: set[str] = set()
    by_hash: dict[str, ProsePage] = {}
    order: list[str] = []
    stats: dict = {}
    for url, html in crawl_entry(seed, fetch, max_depth=max_depth, budget=budget, stats=stats):
        staff, warns = parse_roster(clean_text(html))
        res.warnings.extend(warns)
        emails = _emails_from_html(html)                 # per-person emails from this page's anchors
        for s in staff:
            if s.name in seen_names:                     # never silent-drop a homonym (S2)
                res.warnings.append(
                    f"duplicate staff name across pages (possible homonym), kept first: {s.name!r}")
                continue
            seen_names.add(s.name)
            em = emails.get(s.name, "")
            res.staff.append(replace(s, email=em) if em else s)
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
    """Decide how a page should be handled: ``staff-roster`` (people → KG; fires on the staff
    directory), ``prose`` (content → KB), or ``skip-empty`` (no readable main content → flag,
    never store). ``parse_roster`` returns ``(records, warnings)``."""
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


def _norm_name(surname_given: str) -> str:
    """'Trombella, Jerry' -> 'Jerry Trombella' (mechanical reorder, verbatim tokens)."""
    last, given = surname_given.split(",", 1)
    return f"{given.strip()} {last.strip()}"


def _is_end(low_line: str) -> bool:
    return any(low_line.startswith(m) for m in _BLOCK_END)


def parse_roster(text: str) -> tuple[list[StaffRecord], list[str]]:
    """Parse the Registrar staff directory table. After the 'Name / Phone / Functions' column
    header, each person renders as a (NAME, PHONE, TITLE…) triplet:
        Trombella, Jerry / 973 596 3236 / University Registrar
    POSITIONAL + name-anchored. A record opens on a 'Surname, Given' NAME line (single-token
    surname before the comma — the anti-fab discriminator); the next line must be a phone; the
    lines after the phone, up to the next name/phone/chrome, are the title(s) (>=1, none dropped).
    Anti-fab: a row that is not a clean 'Surname, Given' name, or has no phone/title, is a
    WARNING — never fabricated, never silent-dropped. Returns ([], []) for any page lacking the
    table header (the normal non-directory page), so it falls through to prose."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    low = [ln.lower() for ln in lines]

    hdr = -1
    for i in range(len(lines) - 2):
        if (low[i] == _ROSTER_HEADER[0] and low[i + 1] == _ROSTER_HEADER[1]
                and low[i + 2] == _ROSTER_HEADER[2]):
            hdr = i + 3
            break
    if hdr == -1:
        return [], []

    records: list[StaffRecord] = []
    warnings: list[str] = []
    seen: set[str] = set()
    i = hdr
    while i < len(lines):
        if _is_end(low[i]):
            break
        name_line = lines[i]
        if not _NAME.match(name_line):
            # not a 'Surname, Given' row: an office label or stray line — never fabricate.
            warnings.append(f"roster row not a person name (skipped): {name_line!r}")
            i += 1
            continue
        if i + 1 >= len(lines) or not _PHONE.search(lines[i + 1]):
            warnings.append(f"no phone under name {name_line!r}")
            i += 1
            continue
        m = _PHONE.search(lines[i + 1])
        phone = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        # title lines = everything after the phone until the next name / phone-only line / chrome.
        # (A title that merely CONTAINS a phone — "Call 973-… for X" — does not end the block: N1.)
        j = i + 2
        titles: list[str] = []
        while j < len(lines) and not _is_end(low[j]) \
                and not _NAME.match(lines[j]) and not _is_phone_only(lines[j]):
            titles.append(lines[j])
            j += 1
        if not titles:
            warnings.append(f"no title under name {name_line!r}")
            i = j if j > i else i + 1
            continue
        name = _norm_name(name_line)
        if name in seen:                                 # never silent-drop a homonym (S2)
            warnings.append(f"duplicate roster name (possible homonym), kept first: {name!r}")
        else:
            seen.add(name)
            records.append(StaffRecord(name=name, title=titles[0], phone=phone, email="",
                                       unit="Registrar Staff", titles=tuple(titles)))
        i = j
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


def ingest_registrar(conn, result: EntryResult, source: str = "crawler") -> dict:
    """Write an EntryResult under the EXISTING 'registrar' org (id 24, under njit):
      - staff -> Person + has_role(category='staff') + published phone attr (KG). The roster
        carries no email, so only phone is merged.
      - prose -> knowledge_items type='policy' (IN the served corpus, NOT office_page),
        keyed by source_url, content-hash for recrawl change detection, figures in metadata.
    Idempotent: unchanged pages are skipped; changed pages version-bump (old deactivated).
    Recrawl is change-detection ONLY — removed pages/departed staff are NOT retired (ND6;
    departure reconciliation deferred). Does NOT commit (caller owns the transaction)."""
    org_id = ensure_org(conn, REGISTRAR_SLUG, REGISTRAR_NAME, parent_slug="njit", type="office")
    sync_org_nodes(conn)

    for s in result.staff:
        key = f"{source}/{REGISTRAR_SLUG}/{_slug(s.name)}"
        pid = project_appointment(
            conn, person_key=key, name=s.name, org_id=org_id, category="staff",
            titles=list(s.titles) or [s.title], source_section=(s.unit or "Registrar Staff"),
            source=source)
        _merge_person_attrs(conn, pid, {"email": s.email, "phone": s.phone})

    inserted = updated = unchanged = 0
    for p in result.prose:
        ch = hashlib.sha1(p.content.encode("utf-8")).hexdigest()
        meta = {
            "natural_key": p.source_url,
            "content_hash": ch,
            "images": [list(i) for i in p.images],
            "files": [list(f) for f in p.files],
            "source": "registrar_crawl",
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
