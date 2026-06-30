"""Sitemap-driven crawler for the whole of www.njit.edu (Build B).

Brings ALL www.njit.edu prose into knowledge_items under `created_by='njit_www_crawl'`, driven by
each Drupal subsite's own /<section>/sitemap.xml plus the main www.njit.edu/sitemap.xml. This fixes
the office DFS crawlers' budget/depth page-gaps (e.g. /bursar/payment-information was absent):
sitemap-driven = complete + deterministic, so every recrawl is complete by construction.

Reuses Build A's sitemap engine (catalog_crawl.catalog_seed_urls / extract_urls /
reconcile_sitemap_set) and college_crawl's ingest (ingest_college / ingest_pdf_pages). The one
genuinely-new behavior is cross-source CONTENT dedup (§4.3): skip ingesting a page whose content is
already active under ANY source → fill the office gaps, skip the duplicates. Makes NO serving/gating
decisions (data-bringing-only hard line).

Spec: docs/superpowers/specs/2026-06-30-www-crawl-build-b-design.md
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from v2.core.ingestion.canonical_url import canonical_prose_url
from v2.core.ingestion.catalog_crawl import catalog_seed_urls, extract_urls, reconcile_sitemap_set
from v2.core.ingestion.college_crawl import ingest_college, ingest_pdf_pages

logger = logging.getLogger(__name__)

SOURCE = "njit_www_crawl"
MAIN_SITEMAP = "https://www.njit.edu/sitemap.xml"
_RECONCILE_TYPES = ("policy", "news", "event")   # 'pdf' deliberately excluded (B2)


@dataclass(frozen=True)
class WwwEntry:
    """One crawl unit: a subsite sitemap bound to an org. `page_type` (mechanical, URL-derived) is
    an optional whole-entry type override (the main-sitemap marketing bucket → 'webpage')."""
    sitemap_url: str
    org_slug: str
    org_name: str
    parent_slug: str | None
    org_type: str
    page_type: str | None = None


def _sm(section: str) -> str:
    return f"https://www.njit.edu/{section}/sitemap.xml"


# Office subsites → their EXISTING org (verified 2026-06-30). Several URL-prefixes legitimately
# share one org (the four EOS-family subsites all sit on the `eos` org).
_OFFICES: list[tuple[str, str, str]] = [
    ("bursar", "bursar", "Office of the Bursar / Student Accounts"),
    ("registrar", "registrar", "Office of the Registrar"),
    ("financialaid", "financialaid", "Office of Financial Aid"),
    ("careerservices", "career-development", "Career Development Services"),
    ("counseling", "counseling", "Counseling Center (C-CAPS)"),
    ("dos", "dean-of-students", "Dean of Students"),
    ("graduatestudies", "graduate-studies", "Graduate Studies"),
    ("global", "ogi", "Office of Global Initiatives"),
    # NOTE: no "admissions" entry — www.njit.edu/admissions/sitemap.xml is a permanent 404 (the
    # section has no per-subsite Drupal sitemap). A dead entry would trip SE-1 every recrawl and
    # disable the whole retirement pass. The 46 /admissions/* pages are covered by the MAIN sitemap
    # (typed 'webpage') and the admissions office DFS crawl, so nothing is lost by omitting it.
    ("environmentalsafety", "eos", "Environmental & Operational Services"),
    ("parking", "eos", "Environmental & Operational Services"),
    ("mailroom", "eos", "Environmental & Operational Services"),
    ("sustainability", "eos", "Environmental & Operational Services"),
]

# College/dept SUBDOMAINS (host, existing org slug, org name, org_type) — verified 2026-06-30 from live
# rows. Scope expansion (owner 2026-06-30): the sitemap sweep covers EVERY njit host so the subdomains
# are sitemap-driven too (no DFS budget/depth). The bulk of each subdomain is already held by
# college_crawl (DFS); this pass ADDS any sitemap page the DFS missed (cross-source dedup → never a
# duplicate, never a loss — college_crawl rows are a different source, untouched). Subdomains keep
# classify_type (NOT the webpage marketing bucket). ALL 22 orgs EXIST today → ensure_org early-returns
# (org_type/parent inert). org_type is carried correctly ('college' vs 'department') so that IF this ever
# runs before the orgs exist (e.g. the planned DB-wipe rebuild ordering), a college isn't mis-created as
# a department. (Dept parent should ultimately be its college, not njit — a rebuild-ordering concern, out
# of scope here; the orgs already exist with correct parents.)
_SUBDOMAINS: list[tuple[str, str, str, str]] = [
    ("appliedengineering.njit.edu", "applied-engineering-technology", "School of Applied Engineering & Technology", "department"),
    ("biology.njit.edu", "biological-sciences", "Biological Sciences", "department"),
    ("biomedical.njit.edu", "biomedical-engineering", "Biomedical Engineering", "department"),
    ("chemistry.njit.edu", "chemistry-environmental-science", "Chemistry & Environmental Science", "department"),
    ("civil.njit.edu", "civil-environmental-engineering", "Civil & Environmental Engineering", "department"),
    ("cme.njit.edu", "chemical-materials-engineering", "Chemical & Materials Engineering", "department"),
    ("computing.njit.edu", "ywcc", "YWCC", "college"),
    ("cs.njit.edu", "computer-science", "Computer Science", "department"),
    ("csla.njit.edu", "csla", "College of Science and Liberal Arts", "college"),
    ("datascience.njit.edu", "data-science", "Data Science", "department"),
    ("design.njit.edu", "hcad", "Hillier College of Architecture & Design", "college"),
    ("ece.njit.edu", "electrical-computer-engineering", "Electrical & Computer Engineering", "department"),
    ("engineering.njit.edu", "nce", "Newark College of Engineering", "college"),
    ("history.njit.edu", "history", "History", "department"),
    ("honors.njit.edu", "honors", "Albert Dorman Honors College", "college"),
    ("hss.njit.edu", "humanities-social-sciences", "Humanities & Social Sciences", "department"),
    ("informatics.njit.edu", "informatics", "Informatics", "department"),
    ("management.njit.edu", "mtsm", "Martin Tuchman School of Management (MTSM)", "college"),
    ("math.njit.edu", "mathematical-sciences", "Mathematical Sciences", "department"),
    ("mie.njit.edu", "mechanical-industrial-engineering", "Mechanical & Industrial Engineering", "department"),
    ("physics.njit.edu", "physics", "Physics", "department"),
    ("theatre.njit.edu", "theater-arts-technology", "Theater Arts & Technology", "department"),
]


# Genuinely-uncovered service subsites → a lightweight org under njit (type='office').
_SERVICES: list[tuple[str, str]] = [
    ("policies", "Policies"),
    ("finance", "Finance"),
    ("president", "Office of the President"),
    ("provost", "Office of the Provost"),
    ("reslife", "Residence Life"),
    ("publicsafety", "Public Safety"),
    ("studentinvolvement", "Student Involvement"),
    ("writingcenter", "Writing Center"),
    ("eop", "Educational Opportunity Program"),
    ("studyabroad", "Study Abroad"),
    ("persistence", "Student Persistence"),
    ("accessibility", "Office of Accessibility Resources & Services"),
]

# The ONE NJIT sitemap sweep: every www.njit.edu subsite + every college/dept subdomain + the main
# sitemap. Each entry is a sitemap bound to an existing-or-new org. Subdomains keep classify_type;
# only the main www marketing bucket is forced to 'webpage'.
WWW_SUBSITES: list[WwwEntry] = (
    [WwwEntry(_sm(seg), slug, name, "njit", "office") for seg, slug, name in _OFFICES]
    + [WwwEntry(_sm(seg), seg, name, "njit", "office") for seg, name in _SERVICES]
    + [WwwEntry(f"https://{host}/sitemap.xml", slug, name, "njit", otype)
       for host, slug, name, otype in _SUBDOMAINS]
    # NOTE: the main-sitemap entry MUST stay LAST — office/subdomain pages (typed via classify_type →
    # 'policy'/'news') run first, so if the main sitemap also lists one it dedups against the already-
    # ingested policy row rather than re-typing it 'webpage' (focused-review MINOR-5).
    # main sitemap = academics/marketing landing pages → njit root, typed 'webpage' (downweighted)
    + [WwwEntry(MAIN_SITEMAP, "njit", "New Jersey Institute of Technology", None,
                "university", page_type="webpage")]
)


def www_seed_urls(fetch_bytes, sitemap_url):
    """The current canonical URL frontier from a subsite's sitemap.xml (reuses Build A's parser:
    bytes-fetched, normalized-once, deduped, robots-disallow honored)."""
    return catalog_seed_urls(fetch_bytes, sitemap_url)


def _content_hash(content: str) -> str:
    """The EXACT formula ingest_college / eos_crawl store as metadata.content_hash, so the dedup set
    and the stored hashes are comparable across sources/aliases."""
    return hashlib.sha1(content.encode("utf-8")).hexdigest()


# Extra people-LIST skip beyond college_crawl.is_people_path (which exact-matches only the canonical
# roster HUB segments). The COMPLETE sitemap sweep reaches compound-segment rosters the budget DFS
# missed (focused-review MAJOR). Skip genuine name-LISTS (…-faculty/-staff/-people, directory, …) while
# KEEPING faculty-TOPIC content (faculty-research/-awards/-positions/-handbook, talks, news) — the KG
# (explore.py) owns faculty, so a name-dump must not enter the prose corpus. Mechanical, URL-only.
_ROSTER_SUFFIXES = ("-faculty", "-staff", "-people")
_ROSTER_EXACT = frozenset({"directory", "administration", "emeritus", "personnel",
                           "our-faculty", "faculty-staff", "faculty-and-staff", "faculty-directory"})


def _roster_skip(url: str) -> bool:
    seg = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1].lower()
    seg = re.sub(r"\.(php|html?|aspx)$", "", seg)   # strip a file extension
    seg = re.sub(r"-\d+$", "", seg)                 # strip a Drupal pager suffix (people-2)
    return seg in _ROSTER_EXACT or seg.endswith(_ROSTER_SUFFIXES)


# Soft-404 guard: Drupal subsites list their own /error-404 page in sitemap.xml, served as HTTP 200
# with a boilerplate "Error 404 - Document Not Found" body — junk, not prose. Drop by CONTENT/title
# signature (not URL: the page can sit at /error-404-document-not-found OR /errordocs/404.php). Tight
# prefix match so a real page that merely MENTIONS "404" (e.g. "Handling 404s in Flask") is kept.
_ERROR_PAGE_PREFIXES = ("error 404", "404 error", "404 - ", "404 not found", "404 page not found",
                        "page not found")


def _is_error_page(title: str, content: str) -> bool:
    t = (title or "").strip().lower()
    head = (content or "").strip()[:60].lower()
    return t.startswith(_ERROR_PAGE_PREFIXES) or head.startswith(_ERROR_PAGE_PREFIXES)


def crawl_www_entry(entry: WwwEntry, fetch, fetch_bytes, limit=0):
    """Seed one subsite from its sitemap and extract its prose. Returns (EntryResult, sitemap_urls).
    `extract_urls` (Build A) does verbatim extraction, content-hash alias dedup, is_people_path skip;
    we then drop compound-segment roster leaks (_roster_skip). `limit>0` (dev) crawls only the first N
    sitemap URLs of the entry."""
    urls = www_seed_urls(fetch_bytes, entry.sitemap_url)
    if limit:
        urls = urls[:limit]
    res = extract_urls(urls, fetch)

    def _drop(p):                       # name-LIST roster leak OR a soft-404 boilerplate page
        return _roster_skip(p.source_url) or _is_error_page(p.title, p.content)

    kept = [p for p in res.prose if not _drop(p)]
    for p in res.prose:
        if _drop(p):
            res.html_by_url.pop(p.source_url, None)
    res.prose = kept
    return res, urls


def filter_existing_content(existing: set, res) -> int:
    """Cross-source CONTENT dedup (§4.3): drop pages whose content hash is already active anywhere
    (the prefetched `existing` set), so `ingest_college` only sees genuinely-new-or-changed content.
    Kept pages' hashes are added to `existing` so within-run dupes across subsites are caught too.
    Mutates `res.prose`/`res.html_by_url`; returns the count dropped."""
    kept = []
    dropped = 0
    for p in res.prose:
        h = _content_hash(p.content)
        if h in existing:
            dropped += 1
            res.html_by_url.pop(p.source_url, None)
            continue
        existing.add(h)
        kept.append(p)
    res.prose = kept
    return dropped


def detect_stale_dups(conn, org_id, pages, created_by) -> list[str]:
    """Report-only (RAG-2): a www page whose TITLE matches an ACTIVE row in the same org under a
    DIFFERENT source with a DIFFERENT content hash is a likely stale duplicate (e.g. an old office
    fee page vs the current one). Flag it for a MANUAL gated retire — never act here (that would
    touch another source, breaking isolation)."""
    warns = []
    for p in pages:
        h = _content_hash(p.content)
        row = conn.execute(
            "SELECT source_url, json_extract(metadata,'$.content_hash') FROM knowledge_items "
            "WHERE is_active=1 AND org_id=? AND created_by!=? AND title=? LIMIT 1",
            (org_id, created_by, p.title)).fetchone()
        if row and row[1] and row[1] != h:
            warns.append(f"stale-dup? '{p.title}': www {p.source_url} vs {row[0]} (different content)")
    return warns


def _prior_active(conn, source) -> int:
    placeholders = ",".join("?" * len(_RECONCILE_TYPES))
    return conn.execute(
        f"SELECT COUNT(*) FROM knowledge_items WHERE is_active=1 AND created_by=? "
        f"AND type IN ({placeholders})", (source, *_RECONCILE_TYPES)).fetchone()[0]


def run(conn, fetch, fetch_bytes, *, entries=None, reconcile=True, source=SOURCE, limit=0,
        canonical=False) -> dict:
    """Crawl every WwwEntry, dedup-and-fill into knowledge_items under `source`, then reconcile
    against the UNION of all subsite sitemaps. Does NOT commit (caller owns the transaction).
    `limit>0` (dev) crawls only the first N URLs per entry and forces retirement OFF (partial
    frontier must never retire — S5).
    `canonical` (day-1 rebuild §4.3): when True, key every row on its GLOBAL canonical_prose_url
    via upsert_prose (one row per URL across ALL orgs/sources, keep-fullest) — drops the legacy
    content-hash cross-source skip (`filter_existing_content`). The reconcile union is built from
    canonical_prose_url so the stored source_url and the union use the SAME normalizer."""
    entries = WWW_SUBSITES if entries is None else entries

    existing = set() if canonical else {h for (h,) in conn.execute(
        "SELECT json_extract(metadata,'$.content_hash') FROM knowledge_items WHERE is_active=1") if h}
    prior = _prior_active(conn, source)

    union: set[str] = set()
    seen_hashes: set[str] = set()
    seen_canon: set[str] = set()
    any_failed = False
    warnings: list[str] = []
    totals = {"prose_inserted": 0, "prose_updated": 0, "prose_unchanged": 0, "dropped_dup": 0,
              "pdf_inserted": 0, "pdf_updated": 0, "pdf_unchanged": 0, "skipped": 0}

    for entry in entries:
        res, urls = crawl_www_entry(entry, fetch, fetch_bytes, limit=limit)
        if not urls:
            any_failed = True
            logger.warning("www_crawl: empty/failed sitemap for %s — retirement will be skipped",
                           entry.sitemap_url)
            continue
        union |= {canonical_prose_url(u) for u in urls} if canonical else set(urls)
        if not canonical:
            for p in res.prose:                   # pre-dedup: protect renamed/aliased rows (SE-2)
                seen_hashes.add(_content_hash(p.content))
            totals["dropped_dup"] += filter_existing_content(existing, res)
        out = ingest_college(conn, entry.org_slug, entry.org_name, entry.parent_slug, res,
                             res.html_by_url, org_type=entry.org_type, created_by=source,
                             force_type=entry.page_type, canonical=canonical,
                             seen_canon=seen_canon if canonical else None)
        warnings += detect_stale_dups(conn, out["org_id"], res.prose, source)
        pdf_items = [(u, t) for p in res.prose for u, t in p.files if u.lower().endswith(".pdf")]
        if pdf_items:
            pout = ingest_pdf_pages(conn, entry.org_slug, entry.org_name, entry.parent_slug,
                                    pdf_items, fetch_bytes, org_type=entry.org_type, created_by=source,
                                    canonical=canonical,
                                    seen_canon=seen_canon if canonical else None)
            for k in ("pdf_inserted", "pdf_updated", "pdf_unchanged"):
                totals[k] += pout[k]
        for k in ("prose_inserted", "prose_updated", "prose_unchanged"):
            totals[k] += out[k]
        totals["skipped"] += out["skipped"]

    if limit:
        rec = {"retired": 0, "skipped_reason": "limit_partial_frontier"}   # S5
    elif reconcile and any_failed:
        logger.warning("www_crawl: a subsite sitemap failed — skipping the retirement pass (SE-1)")
        rec = {"retired": 0, "skipped_reason": "subsite_sitemap_failed"}
    elif reconcile:
        rec = reconcile_sitemap_set(conn, union, prior, created_by=source,
                                    seen_hashes=seen_hashes, types=_RECONCILE_TYPES)
    else:
        rec = {"retired": 0, "skipped_reason": "reconcile_disabled"}

    return {"totals": totals, "reconcile": rec, "warnings": warnings, "union": len(union)}
