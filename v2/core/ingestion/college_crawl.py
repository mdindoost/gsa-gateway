"""Generalizable college/department PROSE crawler (Crawling 2.1).

Pilot: YWCC. Brings data ONLY — fetch → mechanically clean → emit records for the caller to
store in KB. Reuses the eos_crawl / web_crawler DFS spine AS-IS (already host+path scoped via
same_scope). Adds: URL-path page typing, structured date capture, in-host people-page skip,
distinct created_by for reconcile isolation. Makes NO serving/gating/usage decisions.

Spec: docs/superpowers/specs/2026-06-25-ywcc-college-crawler-design.md
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse, urlsplit

from bs4 import BeautifulSoup

from v2.core.graph.orgs import ensure_org, sync_org_nodes
from v2.core.ingestion import entry_points as _ep
from v2.core.ingestion.canonical_url import canonical_prose_url, canonical_link
from v2.core.ingestion.prose_store import upsert_prose
from v2.core.ingestion.eos_crawl import (
    ProsePage, extract_prose, _url_rank, _strip_recurring_assets, _canon, _in_scope,
)
from v2.core.ingestion.web_crawler import normalize_url, select_links

logger = logging.getLogger(__name__)

# People-page segments = the LAST path segment of each SUPPLEMENTARY_PATH (the in-host people
# listings explore.py owns). Single source of truth — can't drift from the people crawler.
_PEOPLE_SEGMENTS = frozenset(p.strip("/").split("/")[-1].lower() for p in _ep.SUPPLEMENTARY_PATHS)

# URL-path segments that mark a page kind (segment match, not substring). 'newsroll' is NJIT's
# dept news-article section (e.g. biology.njit.edu/newsroll/<slug>) — type it news so the recency
# decay applies (F2).
_NEWS_SEGMENTS = ("news", "newsroll", "announcement", "announcements")
_EVENT_SEGMENTS = ("event", "events")

# A Drupal pager/duplicate-title alias appends `-<digits>` to the base path (e.g. a people roster's
# second alias is /administration-0). Strip that suffix before the people-segment check so a paged
# roster doesn't escape the skip (F1). The base must still be an EXACT people segment to match, so a
# real prose page like /faculty-research-talks-fall-2025 (base 'faculty-research-talks-fall') is kept.
_PAGER_SUFFIX = re.compile(r"-\d+$")


def _segments(url: str) -> list[str]:
    return [s for s in urlparse(url).path.lower().split("/") if s]


def extract_dates(html: str) -> dict:
    """Extract literal dates from STRUCTURED markup only (article:published_time, JSON-LD Event
    start/end, <time datetime>, dateModified). NO free-text parsing. Returns only present keys."""
    out: dict = {}
    soup = BeautifulSoup(html, "html.parser")

    m = soup.find("meta", attrs={"property": "article:published_time"})
    if m and m.get("content"):
        out["published_at"] = m["content"].strip()

    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        for node in (data if isinstance(data, list) else [data]):
            if not isinstance(node, dict):
                continue
            if str(node.get("@type", "")).lower() == "event":
                if node.get("startDate"):
                    out.setdefault("event_start", str(node["startDate"]).strip())
                if node.get("endDate"):
                    out.setdefault("event_end", str(node["endDate"]).strip())
            if node.get("datePublished"):
                out.setdefault("published_at", str(node["datePublished"]).strip())
            if node.get("dateModified"):
                out.setdefault("source_updated_at", str(node["dateModified"]).strip())

    if "published_at" not in out:
        t = soup.find("time", attrs={"datetime": True})
        if t and t.get("datetime"):
            out["published_at"] = t["datetime"].strip()
    return out


def is_people_path(url: str) -> bool:
    """True when the URL is a dedicated people/roster page (skip — explore.py owns people).
    Segment match against entry_points.SUPPLEMENTARY_PATHS, so /faculty and /faculty/x match
    but /faculty-handbook (real prose) does not. Also matches Drupal pager aliases of a roster
    (/administration-0) by stripping a trailing `-<digits>` before the exact-segment check (F1)."""
    return any(s in _PEOPLE_SEGMENTS or _PAGER_SUFFIX.sub("", s) in _PEOPLE_SEGMENTS
               for s in _segments(url))


def classify_type(url: str) -> str:
    """Mechanically type a page by URL path SEGMENT (not substring): /news,/announcement(s) →
    news; /event(s) → event; else policy. A 'newsletter-signup' page is policy (segment match)."""
    segs = _segments(url)
    if any(s in _NEWS_SEGMENTS for s in segs):
        return "news"
    if any(s in _EVENT_SEGMENTS for s in segs):
        return "event"
    return "policy"


@dataclass
class EntryResult:
    seed: str
    prose: list[ProsePage]
    skipped: list[str]   # pages with no readable content (flag, never stored)
    truncated: bool = False   # hit the page budget with links still queued
    html_by_url: dict = field(default_factory=dict)  # raw HTML per kept page (for date extraction)


def crawl_entry(seed, fetch, max_depth=4, budget=400, delay=0.0, stats=None):
    """DFS the seed's subdomain (bare-host seed → whole host). Reuses select_links/same_scope
    (already host-scoped) + the eos seed-path guard. Yields (url, html). Politeness delay added."""
    seed = _canon(normalize_url(seed, seed))
    seed_path = urlparse(seed).path or "/"
    seen = {seed}
    stack = [(seed, 0)]
    while stack and len(seen) <= budget:
        url, depth = stack.pop()
        html = fetch(url)
        if delay:
            time.sleep(delay)
        if not html:
            continue
        yield url, html
        if depth < max_depth:
            follow, _ = select_links(html, url, seed, relevance_gated=False)
            for u in sorted((_canon(u) for u in follow), reverse=True):
                if u not in seen and _in_scope(seed_path, urlparse(u).path) \
                        and not is_people_path(u):  # never enqueue people pages — explore.py owns them
                    seen.add(u)
                    stack.append((u, depth + 1))
    if stats is not None:
        stats["truncated"] = bool(stack)
        if stack:
            logger.warning("crawl_entry: hit budget %d at %s; %d links unfollowed",
                           budget, seed, len(stack))


def extract_entry(seed, fetch, max_depth=4, budget=400, delay=0.0) -> EntryResult:
    """Crawl one prose entry point. Skip people pages (explore.py owns people). Dedup prose by
    content hash (collapse .php/clean-URL aliases). Brings data only; no DB writes."""
    res = EntryResult(seed=_canon(normalize_url(seed, seed)), prose=[], skipped=[])
    by_hash: dict[str, ProsePage] = {}
    order: list[str] = []
    stats: dict = {}
    for url, html in crawl_entry(seed, fetch, max_depth=max_depth, budget=budget,
                                 delay=delay, stats=stats):
        if is_people_path(url):
            continue                                  # people page — explore.py owns it
        page = extract_prose(url, html)
        if page is None:
            res.skipped.append(url)
            continue
        h = hashlib.sha1(page.content.encode("utf-8")).hexdigest()
        if h not in by_hash:
            by_hash[h] = page
            order.append(h)
            res.html_by_url[url] = html              # stash raw HTML for date extraction
        elif _url_rank(page.source_url) < _url_rank(by_hash[h].source_url):
            old_url = by_hash[h].source_url
            res.html_by_url.pop(old_url, None)
            by_hash[h] = page
            res.html_by_url[url] = html
    res.prose = [by_hash[h] for h in order]
    _strip_recurring_assets(res.prose)
    res.truncated = stats.get("truncated", False)
    return res


PROSE_SOURCE = "college_crawl"


def ingest_college(conn, org_slug, org_name, parent_slug, result, html_by_url,
                   org_type="college", created_by=PROSE_SOURCE, force_type=None,
                   canonical=False, seen_canon=None) -> dict:
    """Write an EntryResult's prose into knowledge_items under one org:
      type = classify_type(url); dates from extract_dates(raw html); created_by=PROSE_SOURCE.
    Content-hash idempotent (unchanged skipped; changed version-bumps old). NO Person creation.
    Does NOT commit (caller owns the transaction).
    ``org_type`` is the ORG tier for ensure_org when the org does not yet exist (college vs
    department); existing orgs keep their type (ensure_org early-returns).
    ``created_by`` scopes the idempotency check and the row provenance tag; defaults to
    PROSE_SOURCE so all existing callers are byte-for-byte unchanged.
    ``force_type`` (mechanical, URL-derived label set by the caller) replaces the per-page
    classify_type when given; defaults to None so existing callers are byte-for-byte unchanged
    (used by www_crawl to type the marketing/landing bucket as 'webpage').
    ``canonical`` (day-1 rebuild §4.3): when True, key each row on its GLOBAL canonical_prose_url
    (resolving node/<id> aliases via the page's <link rel=canonical>) through upsert_prose — one row
    per URL across ALL orgs/sources, keep-fullest on change. Default False = legacy org-scoped path
    (existing callers byte-for-byte unchanged). ``seen_canon`` (optional set): canonical URLs already
    handled this run — a within-run duplicate (same page in two sitemaps) is skipped."""
    org_id = ensure_org(conn, org_slug, org_name, parent_slug=parent_slug, type=org_type)
    sync_org_nodes(conn)
    inserted = updated = unchanged = 0
    if canonical:
        skipped_dup = 0
        for p in result.prose:
            html = html_by_url.get(p.source_url, "")
            canon = canonical_prose_url(canonical_link(html) or p.source_url)
            if seen_canon is not None and canon in seen_canon:
                skipped_dup += 1
                continue
            ptype = force_type or classify_type(p.source_url)
            meta = {"images": [list(i) for i in p.images], "files": [list(f) for f in p.files]}
            meta.update(extract_dates(html))
            status = upsert_prose(conn, org_id=org_id, ptype=ptype, title=p.title,
                                  content=p.content, meta=meta, canonical=canon,
                                  created_by=created_by)
            if seen_canon is not None:
                seen_canon.add(canon)
            if status == "inserted":
                inserted += 1
            elif status == "updated":
                updated += 1
            else:                                # 'unchanged' or 'skipped_worse'
                unchanged += 1
        return {"org_id": org_id, "prose_inserted": inserted, "prose_updated": updated,
                "prose_unchanged": unchanged, "skipped": len(result.skipped)}
    for p in result.prose:
        ch = hashlib.sha1(p.content.encode("utf-8")).hexdigest()
        meta = {
            "natural_key": p.source_url,
            "content_hash": ch,
            "images": [list(i) for i in p.images],
            "files": [list(f) for f in p.files],
            "source": created_by,
        }
        meta.update(extract_dates(html_by_url.get(p.source_url, "")))
        ptype = force_type or classify_type(p.source_url)
        row = conn.execute(
            "SELECT id, json_extract(metadata,'$.content_hash') FROM knowledge_items "
            "WHERE is_active=1 AND org_id=? AND json_extract(metadata,'$.natural_key')=? "
            "AND created_by=?", (org_id, p.source_url, created_by)).fetchone()
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
            (org_id, ptype, p.title, p.content, json.dumps(meta), p.source_url, created_by))
    return {"org_id": org_id, "prose_inserted": inserted, "prose_updated": updated,
            "prose_unchanged": unchanged, "skipped": len(result.skipped)}


# Per-semester math COURSE-SYLLABUS PDFs live under math.njit.edu/sites/math/files/ and are named
# Math_<coursenum>[-<section>]-<sem><yr>.pdf (e.g. Math_107-F18.pdf, Math_105-001-F18.pdf). The owner
# (2026-07-01) chose to KEEP everything from NJIT verbatim (hard line) but LABEL these so the retriever
# can keep them out of default answers (they are per-semester, high-volume, and would dilute normal RAG).
# The label is `type='syllabus'` (a mechanical URL-derived type, like /news→news); the SERVING policy for
# that type lives in the retriever (excluded from the default corpus), NOT here. brochures/flyers/exams/
# quals/annual reports on the SAME host are named otherwise and stay type='pdf'.
SYLLABUS_TYPE = "syllabus"
_MATH_SYLLABUS_HOST = "math.njit.edu"
_MATH_SYLLABUS_PATH = "/sites/math/files/"
_MATH_SYLLABUS_NAME = re.compile(r"^Math[ _-]?\d", re.IGNORECASE)
# EXCLUSION — a broad "Math<num>" filename that is actually an EXAM/FINAL/QUIZ/etc, NOT a course
# syllabus (senior-eng rev e5228bc found ^Math\d also matched 30 exam PDFs the design KEEPS, e.g.
# "Math 107 Exam 1 Fall 2022.pdf", "Math_222_FinalExam_F17.pdf", "Math 110_Fall 2024_E1.pdf"). These
# doc-kind tokens never appear in a course-syllabus filename, so excluding them keeps exams/finals/
# quizzes/solutions/schedules while still skipping the pure "Math <num> <sem><yr>" syllabi.
_MATH_NON_SYLLABUS = re.compile(
    r"exam|final|quiz|review|assess|solution|\bsols?\b|practice|schedule|midterm|\btest\b|prelim|"
    r"placement|[ _\-]E\d|[ _\-]FE\b", re.IGNORECASE)


def is_math_syllabus(url: str) -> bool:
    """True for a math.njit.edu per-semester course-SYLLABUS PDF (intentionally skipped).

    Parses host + path (NOT a substring match) so a non-math URL that merely CONTAINS the dir string
    in a query/path segment is never excused by the coverage gate. Excludes exam/final/quiz/etc
    filenames so only genuine course syllabi are dropped (reviewers rev e5228bc)."""
    p = urlparse(url or "")
    if p.netloc.lower() != _MATH_SYLLABUS_HOST or not p.path.lower().startswith(_MATH_SYLLABUS_PATH):
        return False
    if not p.path.lower().endswith(".pdf"):
        return False
    fname = unquote(p.path.rsplit("/", 1)[-1])
    return bool(_MATH_SYLLABUS_NAME.match(fname)) and not _MATH_NON_SYLLABUS.search(fname)


def pdf_type(url: str) -> str:
    """Mechanical type for a PDF knowledge_item: 'syllabus' for a math course syllabus (kept in the
    corpus but excluded from default answers by the retriever), else 'pdf'. Single source of the
    PDF-type mapping used by both ingest_pdf_pages write paths."""
    return SYLLABUS_TYPE if is_math_syllabus(url) else "pdf"


def ingest_pdf_pages(conn, org_slug, org_name, parent_slug, pdf_items, fetch_bytes,
                     org_type="college", created_by=PROSE_SOURCE,
                     canonical=False, seen_canon=None) -> dict:
    """Ingest discovered PDF links as type='pdf' knowledge_items rows.

    pdf_items   -- iterable of (url, label) tuples; deduplicated by url inside this function.
    fetch_bytes -- callable(url) -> bytes | None (injectable; None = fetch failed → manifest skip).

    For each unique url:
      - fetch_bytes(url) → None  → manifest skip {"url","status":"fetch_failed","reason":…}
      - extract_pdf_text(data):
          status in {ok, mixed_low_text} → content-hash idempotent INSERT of type='pdf' row.
          status in {empty, image_heavy, invalid} → manifest skip, NO row.

    Idempotent on (org_id, natural_key=url, created_by) exactly like ingest_college.
    Does NOT commit (caller owns the transaction).
    ``created_by`` scopes the idempotency check and the row provenance tag; defaults to
    PROSE_SOURCE so all existing callers are byte-for-byte unchanged.

    Returns {"org_id":…,"pdf_inserted":n,"pdf_updated":n,"pdf_unchanged":n,"skipped":[…]}.
    """
    from v2.core.ingestion.pdf_extract import extract_pdf_text

    org_id = ensure_org(conn, org_slug, org_name, parent_slug=parent_slug, type=org_type)
    sync_org_nodes(conn)

    inserted = updated = unchanged = 0
    skipped: list[dict] = []
    seen_urls: set[str] = set()

    for url, label in pdf_items:
        if url in seen_urls:
            continue
        seen_urls.add(url)

        canon = canonical_prose_url(url) if canonical else url
        if canonical and seen_canon is not None and canon in seen_canon:
            continue                         # same PDF asset already handled this run (two sitemaps)

        data = fetch_bytes(url)
        if data is None:
            skipped.append({"url": url, "status": "fetch_failed",
                            "reason": "fetch_bytes returned None"})
            continue

        res = extract_pdf_text(data)
        if res.status not in ("ok", "mixed_low_text"):
            skipped.append({"url": url, "status": res.status, "reason": res.reason})
            continue

        # Mechanical title: label if non-empty, else filename stem with hyphens/underscores→spaces.
        if label and label.strip():
            title = label.strip()
        else:
            stem = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
            if "." in stem:
                stem = stem.rsplit(".", 1)[0]
            title = re.sub(r"[-_]+", " ", stem)

        text = res.text or ""
        ch = hashlib.sha1(text.encode("utf-8")).hexdigest()
        meta = {
            "natural_key": url,
            "content_hash": ch,
            "pdf_table_degraded": res.table_degraded,
            "status": res.status,
            "source": created_by,
        }

        ptype = pdf_type(url)                    # 'syllabus' (excluded from default answers) or 'pdf'
        if canonical:
            meta.pop("natural_key", None)        # upsert_prose sets natural_key=canon
            meta.pop("content_hash", None)
            status = upsert_prose(conn, org_id=org_id, ptype=ptype, title=title, content=text,
                                  meta=meta, canonical=canon, created_by=created_by)
            if seen_canon is not None:
                seen_canon.add(canon)
            if status == "inserted":
                inserted += 1
            elif status == "updated":
                updated += 1
            else:                                # 'unchanged' or 'skipped_worse'
                unchanged += 1
            continue

        row = conn.execute(
            "SELECT id, json_extract(metadata,'$.content_hash') FROM knowledge_items "
            "WHERE is_active=1 AND org_id=? AND json_extract(metadata,'$.natural_key')=? "
            "AND created_by=?", (org_id, url, created_by)).fetchone()

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
            (org_id, ptype, title, text, json.dumps(meta), url, created_by))

    return {"org_id": org_id, "pdf_inserted": inserted, "pdf_updated": updated,
            "pdf_unchanged": unchanged, "skipped": skipped}


@dataclass(frozen=True)
class ProseEntry:
    seed: str           # bare-host root, e.g. https://cs.njit.edu/
    org_slug: str
    org_name: str
    parent_slug: str
    org_type: str = "department"   # org tier for ensure_org if the org is new (college/department)


# Add a college/dept = add a ProseEntry here (the data registry — no new code). Give the college
# root org_type="college" and each department "department" so a brand-new org is created at the
# right tier; every org below ALSO pre-exists from the people layer (explore.py / ALL_ENTRY_POINTS),
# so ensure_org early-returns and the prose just attaches to the existing org. One seed per host:
# departments that have their own *.njit.edu subdomain crawl independently; colleges whose schools
# have NO subdomain (HCAD's njsoa/art-design, MTSM administration) carry ALL their prose on the one
# college org (the people layer still splits them — prose is host-scoped, not section-scoped).
# ORDER: college root before its departments (readability only — every org already exists).
PROSE_ENTRY_POINTS: list[ProseEntry] = [
    # ── Ying Wu College of Computing (YWCC) — pilot, shipped 2026-06-25 ──────────────────────
    ProseEntry("https://computing.njit.edu/", "ywcc", "YWCC", "njit", "college"),
    ProseEntry("https://cs.njit.edu/", "computer-science", "Computer Science", "ywcc", "department"),
    ProseEntry("https://informatics.njit.edu/", "informatics", "Informatics", "ywcc", "department"),
    ProseEntry("https://datascience.njit.edu/", "data-science", "Data Science", "ywcc", "department"),

    # ── Martin Tuchman School of Management (MTSM) ──────────────────────────────────────────
    # One subdomain (management.njit.edu) serves the college + its administration — one prose seed.
    ProseEntry("https://management.njit.edu/", "mtsm",
               "Martin Tuchman School of Management (MTSM)", "njit", "college"),

    # ── Newark College of Engineering (NCE) — college root + 6 department subdomains ─────────
    ProseEntry("https://engineering.njit.edu/", "nce", "Newark College of Engineering", "njit", "college"),
    ProseEntry("https://biomedical.njit.edu/", "biomedical-engineering",
               "Biomedical Engineering", "nce", "department"),
    ProseEntry("https://cme.njit.edu/", "chemical-materials-engineering",
               "Chemical & Materials Engineering", "nce", "department"),
    ProseEntry("https://civil.njit.edu/", "civil-environmental-engineering",
               "Civil & Environmental Engineering", "nce", "department"),
    ProseEntry("https://ece.njit.edu/", "electrical-computer-engineering",
               "Electrical & Computer Engineering", "nce", "department"),
    ProseEntry("https://mie.njit.edu/", "mechanical-industrial-engineering",
               "Mechanical & Industrial Engineering", "nce", "department"),
    ProseEntry("https://appliedengineering.njit.edu/", "applied-engineering-technology",
               "School of Applied Engineering & Technology", "nce", "department"),

    # ── College of Science and Liberal Arts (CSLA) — college root + 6 dept subdomains + theater
    ProseEntry("https://csla.njit.edu/", "csla", "College of Science and Liberal Arts", "njit", "college"),
    ProseEntry("https://biology.njit.edu/", "biological-sciences", "Biological Sciences", "csla", "department"),
    ProseEntry("https://chemistry.njit.edu/", "chemistry-environmental-science",
               "Chemistry & Environmental Science", "csla", "department"),
    ProseEntry("https://history.njit.edu/", "history", "History", "csla", "department"),
    ProseEntry("https://hss.njit.edu/", "humanities-social-sciences",
               "Humanities & Social Sciences", "csla", "department"),
    ProseEntry("https://math.njit.edu/", "mathematical-sciences", "Mathematical Sciences", "csla", "department"),
    ProseEntry("https://physics.njit.edu/", "physics", "Physics", "csla", "department"),
    ProseEntry("https://theatre.njit.edu/", "theater-arts-technology",
               "Theater Arts & Technology", "csla", "department"),

    # ── Hillier College of Architecture & Design (HCAD) ─────────────────────────────────────
    # One subdomain (design.njit.edu) serves the college + NJSOA + Art+Design — one prose seed on hcad.
    ProseEntry("https://design.njit.edu/", "hcad", "Hillier College of Architecture & Design", "njit", "college"),

    # ── Albert Dorman Honors College ────────────────────────────────────────────────────────
    # An honors PROGRAM (cross-cutting), not a faculty-home college: honors.njit.edu exposes NO
    # people.njit.edu profile roster, so this is PROSE-ONLY (like the prose-only offices). Its
    # leadership is captured in the prose (dean/contact pages); honors-affiliated faculty already
    # live in the KG under their home colleges (home-appointment-only). The prose ingest CREATES
    # the `honors` college org (it doesn't exist yet) via ensure_org.
    ProseEntry("https://honors.njit.edu/", "honors", "Albert Dorman Honors College", "njit", "college"),
]


# SECTION coverage-supplement seeds (day-1 rebuild, 2026-07-01). The wipe+sitemap sweep loses pages
# that no sitemap lists — DFS-only office subtrees under www.njit.edu (path-scoped: _in_scope bounds
# each seed to its own /<section>/ subtree, so a seed can NEVER walk the whole homepage) and the
# ist.njit.edu office subdomain. Each org ALREADY EXISTS (created during the Crawling-2.1 office
# rollout — ensure_org early-returns by slug); org_slug/name/parent below were DERIVED from the org
# each section's live prose already attaches to (data-driven, not guessed) and are frozen here so the
# seed list is STATIC and recrawl-perfect. njitresearch/stem are single university landing pages → the
# `njit` root org. (The bare www.njit.edu homepage is deliberately NOT a seed — it is nav/marketing and
# an "/" seed would DFS the entire site; the one homepage row is a reviewed gate drop.)
SECTION_ENTRY_POINTS: list[ProseEntry] = [
    ProseEntry("https://ist.njit.edu/", "ist", "IST / Technology Support", "njit", "office"),
    ProseEntry("https://www.njit.edu/careerservices", "career-development",
               "Career Development Services", "njit", "office"),
    ProseEntry("https://www.njit.edu/financialaid", "financialaid", "Office of Financial Aid", "njit", "office"),
    ProseEntry("https://www.njit.edu/registrar", "registrar", "Office of the Registrar", "njit", "office"),
    ProseEntry("https://www.njit.edu/environmentalsafety", "eos",
               "Environmental & Operational Services", "njit", "office"),
    ProseEntry("https://www.njit.edu/parking", "eos", "Environmental & Operational Services", "njit", "office"),
    ProseEntry("https://www.njit.edu/mailroom", "eos", "Environmental & Operational Services", "njit", "office"),
    ProseEntry("https://www.njit.edu/sustainability", "eos",
               "Environmental & Operational Services", "njit", "office"),
    ProseEntry("https://www.njit.edu/graduatestudies", "graduate-studies", "Graduate Studies", "njit", "office"),
    ProseEntry("https://www.njit.edu/global", "ogi", "Office of Global Initiatives", "njit", "office"),
    ProseEntry("https://www.njit.edu/studyabroad", "studyabroad", "Study Abroad", "njit", "office"),
    ProseEntry("https://www.njit.edu/bursar", "bursar",
               "Office of the Bursar / Student Accounts", "njit", "office"),
    ProseEntry("https://www.njit.edu/dos", "dean-of-students", "Dean of Students", "njit", "office"),
    ProseEntry("https://www.njit.edu/counseling", "counseling", "Counseling Center (C-CAPS)", "njit", "office"),
    ProseEntry("https://www.njit.edu/admissions", "graduate-admissions",
               "Office of University Admissions", "njit", "office"),
    ProseEntry("https://www.njit.edu/policies", "policies", "Policies", "njit", "office"),
    ProseEntry("https://www.njit.edu/finance", "finance", "Finance", "njit", "office"),
    ProseEntry("https://www.njit.edu/president", "president", "Office of the President", "njit", "office"),
    ProseEntry("https://www.njit.edu/provost", "provost", "Office of the Provost", "njit", "office"),
    ProseEntry("https://www.njit.edu/reslife", "reslife", "Residence Life", "njit", "office"),
    ProseEntry("https://www.njit.edu/publicsafety", "publicsafety", "Public Safety", "njit", "office"),
    ProseEntry("https://www.njit.edu/studentinvolvement", "studentinvolvement",
               "Student Involvement", "njit", "office"),
    ProseEntry("https://www.njit.edu/writingcenter", "writingcenter", "Writing Center", "njit", "office"),
    ProseEntry("https://www.njit.edu/eop", "eop", "Educational Opportunity Program", "njit", "office"),
    ProseEntry("https://www.njit.edu/persistence", "persistence", "Student Persistence", "njit", "office"),
    ProseEntry("https://www.njit.edu/accessibility", "accessibility",
               "Office of Accessibility Resources & Services", "njit", "office"),
    ProseEntry("https://www.njit.edu/njitresearch", "njit",
               "New Jersey Institute of Technology", "njit", "office"),
    ProseEntry("https://www.njit.edu/stem", "njit", "New Jersey Institute of Technology", "njit", "office"),
]
