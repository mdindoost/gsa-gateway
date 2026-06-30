"""Phase 1b — disciplined crawler for a professor's personal website.

NOT a web-scale crawler. Given one seed URL (the personal site we recorded from the
NJIT profile), it fetches a *bounded, relevance-gated, same-domain* set of pages:

  * the homepage (always), then
  * links whose anchor text or URL clearly matter — publications / research /
    projects / software / cv / group / teaching — up to ``max_depth`` hops,
  * same registrable host only (never wanders off-site),
  * a page ``budget`` as a backstop against pathological sites,
  * politeness: project UA, robots.txt, a delay between fetches, dedup + loop guard,
  * non-HTML (PDF CV, slides, …) is RECORDED but not parsed (deferred slice).

The traversal *policy* (URL normalize, same-site, relevance, link selection) is pure
and unit-tested; the network fetch is injected into ``crawl_site`` so tests run
offline. See docs/superpowers/specs/2026-06-11-hybrid-knowledge-ingestion.md (Phase 1b).
"""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
import time
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import Request

from bs4 import BeautifulSoup
from bs4.exceptions import ParserRejectedMarkup

logger = logging.getLogger(__name__)

UA = "GSA-Gateway-Bot/1.0 (+https://github.com/mdindoost/gsa-gateway)"
DEFAULT_DEPTH = 2
DEFAULT_BUDGET = 15
TIMEOUT = 20
DELAY = 1.0  # seconds between fetches (politeness)
MAX_FETCH_BYTES = 5_000_000  # cap the raw body read (defense vs huge/streamed responses)
FETCH_DEADLINE = 60  # total wall-clock seconds for one response body (slow-drip hang guard)


def is_safe_url(url: str) -> bool:
    """SSRF guard. The seed comes from the professor's NJIT 'Website' field — i.e.
    attacker-influenceable — so before ANY fetch (seed, link, or redirect hop) we
    reject non-http(s) and any host that resolves to a private / loopback / link-local
    / reserved address (e.g. 169.254.169.254 cloud metadata, 127.*, 10.*). Only
    globally-routable public hosts are allowed.

    Residual (accepted): this resolves the host, then urllib resolves it again on
    connect — a DNS-rebinding host could pass the check and connect to a private IP.
    Acceptable for an internal admin tool seeded from NJIT faculty Website fields."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    try:
        infos = socket.getaddrinfo(p.hostname, None)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    for *_, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            return False
        if not ip.is_global or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    return bool(infos)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Re-validate every redirect target — an in-scope seed must not be able to 302
    into an internal address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_safe_url(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)

# A link is worth following only if one of these appears in its anchor text or URL.
# Deliberately excludes teaching/courses (huge low-signal syllabi) and generic
# "people" (department rosters) — research/bio/cv is what adds to the KB.
RELEVANCE = (
    "publication", "publications", "papers", "paper", "preprint", "preprints",
    "research", "project", "projects", "software", "code", "tools",
    "cv", "resume", "vitae", "bio", "biography", "about",
    "group", "lab", "students", "talks",
)
MAX_PAGE_CHARS = 150_000  # backstop: skip a data-dump page (e.g. a 300KB syllabus)
# Non-HTML we record (so the URL isn't lost) but do not parse yet.
_NON_HTML_EXT = (".pdf", ".doc", ".docx", ".ppt", ".pptx", ".ps", ".zip", ".gz",
                 ".tar", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".mp4", ".mov",
                 ".bib", ".css", ".js", ".woff", ".woff2", ".ttf", ".ico")


def _host(url: str) -> str:
    return (urlparse(url).netloc or "").lower().removeprefix("www.")


def same_site(a: str, b: str) -> bool:
    """Same host (ignoring a leading www.)."""
    return _host(a) == _host(b) and bool(_host(a))


def scope_prefix(seed: str) -> str:
    """The path prefix that bounds 'this professor's site'. On a SHARED host a
    personal page lives under ``/~user/`` (or a sub-directory) — scoping to that
    prefix stops the crawl wandering into the department site (e.g. a redirect from
    ``/~crix`` into ``cs.njit.edu/about``). A personal domain seeds at ``/`` -> whole
    host."""
    path = urlparse(seed).path or "/"
    m = re.match(r"(/~[^/]+)/?", path)            # /~user style
    if m:
        return m.group(1) + "/"
    if path.endswith("/"):
        return path
    parent = path.rsplit("/", 1)[0]
    return parent + "/" if parent else "/"


def same_scope(seed: str, url: str) -> bool:
    """Same host AND under the seed's personal-path prefix."""
    return same_site(seed, url) and urlparse(url).path.startswith(scope_prefix(seed))


def normalize_url(href: str, base: str) -> str:
    """Resolve ``href`` against ``base``; drop fragment + query and lowercase the
    host. The path is kept verbatim (incl. any trailing slash) — stripping it would
    break relative-link resolution for directory seeds like ``/~ikoutis/``."""
    u = urljoin(base, href.strip())
    p = urlparse(u)
    return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path, "", "", ""))


def _ext(url: str) -> str:
    m = re.search(r"(\.[A-Za-z0-9]{1,5})$", urlparse(url).path)
    return m.group(1).lower() if m else ""


def is_non_html(url: str) -> bool:
    return _ext(url) in _NON_HTML_EXT


def is_relevant(anchor: str, url: str) -> bool:
    hay = f"{anchor.lower()} {urlparse(url).path.lower()}"
    return any(kw in hay for kw in RELEVANCE)


def select_links(html: str, current_url: str, seed_url: str, relevance_gated: bool = True):
    """From one page: (HTML links to follow, recorded non-HTML files). Pure. When
    relevance_gated=False (aspect='office'), follow ALL same-scope HTML links (the people
    vocabulary would harvest ~1–2 pages of an office tree) — assets still dropped. [SE1]"""
    follow: set[str] = set()
    files: set[str] = set()
    try:
        soup = BeautifulSoup(html, "html.parser")
    except ParserRejectedMarkup:
        logger.warning("select_links: malformed markup at %s", current_url, exc_info=True)
        return follow, files
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("mailto:", "javascript:", "#", "tel:")):
            continue
        url = normalize_url(href, current_url)
        if not urlparse(url).scheme.startswith("http"):
            continue
        if not same_scope(seed_url, url):
            continue
        anchor = a.get_text(" ", strip=True)
        if is_non_html(url):
            if relevance_gated and is_relevant(anchor, url):
                files.add(url)
            elif not relevance_gated:
                pass                            # office: skip non-HTML files (prose only)
            continue
        if (not relevance_gated) or is_relevant(anchor, url):
            follow.add(url)
    return follow, files


def clean_text(html: str) -> str:
    """Strip boilerplate (script/style/nav/header/footer) and return readable text.

    A malformed page (e.g. a bad marked section) makes html.parser raise bs4's
    ParserRejectedMarkup; degrade to "" so the live-fallback path returns no spans and
    falls back to the normal deflection instead of showing the user a generic error."""
    try:
        soup = BeautifulSoup(html, "html.parser")
    except ParserRejectedMarkup:
        logger.warning("clean_text: malformed markup, returning empty text", exc_info=True)
        return ""
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "form"]):
        tag.decompose()
    return re.sub(r"\n\s*\n+", "\n\n",
                  re.sub(r"[ \t]+", " ", soup.get_text("\n", strip=True))).strip()


@dataclass
class CrawledPage:
    url: str
    text: str
    depth: int


@dataclass
class CrawlResult:
    seed: str
    pages: list[CrawledPage] = field(default_factory=list)
    recorded_files: list[str] = field(default_factory=list)   # PDFs etc. (deferred)
    oversized: list[str] = field(default_factory=list)        # skipped data-dump pages
    note: str = ""


def crawl_site(seed_url: str, fetch, max_depth: int = DEFAULT_DEPTH,
               budget: int = DEFAULT_BUDGET, delay: float = 0.0,
               relevance_gated: bool = True) -> CrawlResult:
    """BFS over the site. ``fetch(url) -> html|None`` is injected (real fetcher does
    UA + robots + timeout; tests pass a dict-backed stub). Relevance-gated, same-site,
    depth- and budget-bounded, dedup + loop-guarded."""
    seed = normalize_url(seed_url, seed_url)
    visited = {seed}
    queue: list[tuple[str, int]] = [(seed, 0)]
    res = CrawlResult(seed=seed)
    files: set[str] = set()
    while queue and len(res.pages) < budget:
        url, depth = queue.pop(0)
        html = fetch(url)
        if not html:
            continue
        text = clean_text(html)
        if len(text) > MAX_PAGE_CHARS:              # data dump (syllabus/log) — skip
            res.oversized.append(url)
            continue
        res.pages.append(CrawledPage(url=url, text=text, depth=depth))
        if depth < max_depth:
            follow, nf = select_links(html, url, seed, relevance_gated=relevance_gated)
            files |= nf
            for u in sorted(follow):
                if u not in visited:
                    visited.add(u)
                    queue.append((u, depth + 1))
        if delay:
            time.sleep(delay)
    res.recorded_files = sorted(files)
    if len(res.pages) >= budget and queue:
        res.note = f"hit page budget ({budget}); {len(queue)} relevant links not followed"
    return res


def _make_opener_and_allowed():
    """Shared factory: build a redirect-safe opener and a robots-cache-backed _allowed(url)
    callable. Both fetch_with_status and make_bytes_fetcher use this to avoid duplicating
    the opener + robots logic."""
    opener = urllib.request.build_opener(_SafeRedirect())
    robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def _allowed(url: str) -> bool:
        host = urlparse(url).scheme + "://" + urlparse(url).netloc
        rp = robots_cache.get(host, "miss")
        if rp == "miss":
            rp = urllib.robotparser.RobotFileParser()
            try:
                rp.set_url(host + "/robots.txt"); rp.read()
            except Exception:  # noqa: BLE001
                rp = None
            robots_cache[host] = rp
        return rp is None or rp.can_fetch(UA, url)

    return opener, _allowed


def _read_capped(resp, max_bytes=MAX_FETCH_BYTES, deadline_s=FETCH_DEADLINE, *, now=time.monotonic):
    """Read up to ``max_bytes`` from an HTTP response, but abort after ``deadline_s`` total
    wall-clock seconds. The per-recv socket timeout (TIMEOUT) only fires on a fully-silent gap;
    a server that DRIBBLES a few bytes within each window keeps a plain ``resp.read(max_bytes)``
    going indefinitely (observed: a 12-minute stall, socket ESTABLISHED but no progress). Reading
    one recv at a time via ``read1`` lets us check the wall-clock between chunks, so the total read
    is bounded regardless of the drip. Raises ``TimeoutError`` on overrun — callers already map any
    read exception to a fetch-miss (None)."""
    start = now()
    buf = bytearray()
    while len(buf) < max_bytes:
        if now() - start > deadline_s:
            raise TimeoutError(f"body read exceeded {deadline_s}s deadline")
        part = resp.read1(max_bytes - len(buf))
        if not part:                       # EOF
            break
        buf.extend(part)
    return bytes(buf)


def fetch_with_status(timeout: int = TIMEOUT):
    """Like make_fetcher but returns (html|None, status|None). status is the HTTP code
    (200/404/410/…) or None on a transport error (timeout/DNS/SSRF-block), so a transient
    failure is distinguishable from a real 404 — the [SE3] retire guard (Plan C)."""
    opener, _allowed = _make_opener_and_allowed()

    def fetch(url: str):
        if not is_safe_url(url):
            return None, None
        if not _allowed(url):
            return None, None
        try:
            req = Request(url, headers={"User-Agent": UA})
            with opener.open(req, timeout=timeout) as r:
                ctype = r.headers.get("Content-Type", "")
                status = getattr(r, "status", None) or r.getcode()
                if "html" not in ctype.lower():
                    return None, status
                return _read_capped(r).decode("utf-8", "ignore"), status
        except urllib.error.HTTPError as e:       # 404/410/5xx carry a real status
            return None, e.code
        except Exception:  # noqa: BLE001 - transport error: status unknown
            return None, None

    return fetch


def make_bytes_fetcher(timeout: int = TIMEOUT):
    """Returns fetch_bytes(url) -> bytes | None. Applies the same SSRF/robots/UA guards and
    reads up to MAX_FETCH_BYTES as fetch_with_status, but does NOT reject by Content-Type
    (PDFs are application/pdf, not text/html)."""
    opener, _allowed = _make_opener_and_allowed()

    def fetch_bytes(url: str) -> bytes | None:
        if not is_safe_url(url):
            return None
        if not _allowed(url):
            return None
        try:
            req = Request(url, headers={"User-Agent": UA})
            with opener.open(req, timeout=timeout) as r:
                return _read_capped(r)
        except Exception:  # noqa: BLE001
            return None

    return fetch_bytes


def make_fetcher(timeout: int = TIMEOUT):
    """A real fetcher returning html|None (backward-compatible). Robots/SSRF/UA/HTML-only."""
    inner = fetch_with_status(timeout)
    return lambda url: inner(url)[0]
