"""Headless discovery for JavaScript-rendered faculty lists (e.g. DS).

NJIT's DS faculty list (ds.njit.edu/people) is a React app over Elasticsearch —
static HTML has no profile links, so the CS-style ``discover()`` finds nothing.
This renders the page with a headless browser and scrapes the
``people.njit.edu/profile/<slug>`` links. Everything downstream of discovery is
unchanged (the existing static pipeline crawls those profile pages).

Design (see docs/superpowers/specs/2026-06-13-ds-crawler-design.md):
- Playwright is an OPTIONAL dependency, imported lazily. If absent, raise a plain
  RuntimeError (NEVER SystemExit — the --all loop catches Exception, so a missing
  dep / render failure degrades to a clean per-department failure rather than
  aborting the whole batch).
- Completeness over "≥1 link": scroll / load-more until the profile-link count is
  stable, so a paginated list isn't silently truncated.
- Also intercept the page's own data response (the search-api JSON we couldn't pin
  by hand) so the verification gate can cross-check the DOM scrape against it.
- The render itself is verified live (the §5 gate), not in CI. The pure helpers
  here are unit-tested.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_PROFILE_RE = re.compile(r"https://people\.njit\.edu/profile/[A-Za-z0-9_-]+")
_UA = "GSA-Gateway-Bot/1.0 (+https://github.com/mdindoost/gsa-gateway)"
PLAYWRIGHT_HINT = (
    "DS discovery needs Playwright: pip install playwright "
    "&& playwright install chromium")


@dataclass
class DiscoveryResult:
    urls: list[str]                                  # DOM-scraped (production set)
    intercepted: list[str] = field(default_factory=list)  # from the page's data API
    title: str = ""
    html_len: int = 0


def _extract_profiles(html: str) -> list[str]:
    """Unique people.njit.edu/profile URLs in document order."""
    seen: set[str] = set()
    out: list[str] = []
    for u in _PROFILE_RE.findall(html or ""):
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _slugs(urls) -> set[str]:
    return {u.rstrip("/").rsplit("/", 1)[-1] for u in urls}


def crosscheck(dom_urls, intercepted_urls) -> bool:
    """True if the two independent discovery sets cover the same profiles.

    Agreement between the DOM scrape and the page's own data response is strong
    evidence the scrape is complete (not a truncated first page)."""
    return _slugs(dom_urls) == _slugs(intercepted_urls)


def discover_js(faculty_list_url: str, timeout: int = 30) -> DiscoveryResult:
    """Render a JS faculty list and return the profile URLs (+ intercepted data).

    Raises RuntimeError if Playwright is unavailable or the render fails (after one
    retry). Never raises SystemExit.
    """
    try:
        from playwright.sync_api import TimeoutError as PWTimeout
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(PLAYWRIGHT_HINT) from exc

    last_err: Exception | None = None
    for attempt in (1, 2):  # one retry — headless cold-starts / network flap
        try:
            return _render(sync_playwright, PWTimeout, faculty_list_url, timeout)
        except Exception as exc:  # noqa: BLE001 - retry once, then surface
            last_err = exc
            logger.warning("DS render attempt %d failed: %s", attempt, exc)
            time.sleep(2)
    raise RuntimeError(f"DS discovery render failed: {last_err}")


def _render(sync_playwright, PWTimeout, url: str, timeout: int) -> DiscoveryResult:
    intercepted: list[str] = []

    def _on_response(resp):
        # Capture the page's own data calls (the search-api JSON). We don't know
        # its exact shape, so scan the body defensively for profile links.
        try:
            ct = (resp.headers or {}).get("content-type", "")
            if "json" not in ct.lower():
                return
            body = resp.text()
        except Exception:  # noqa: BLE001 - never let interception break the render
            return
        for u in _extract_profiles(body):
            if u not in intercepted:
                intercepted.append(u)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(user_agent=_UA)
            page = ctx.new_page()
            page.on("response", _on_response)
            page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
            # Wait for the React list to render at least one profile link.
            page.wait_for_selector("a[href*='people.njit.edu/profile/']",
                                   timeout=timeout * 1000)
            _exhaust_pagination(page, timeout)
            html = page.content()
            title = page.title()
        finally:
            browser.close()

    urls = _extract_profiles(html)
    if not urls:
        # Distinguish a structure change from a consent / bot-challenge shell.
        raise RuntimeError(
            f"DS render produced 0 profiles (title={title!r}, html_len={len(html)})")
    logger.info("DS discovery: %d profiles (DOM), %d intercepted",
                len(urls), len(intercepted))
    return DiscoveryResult(urls=urls, intercepted=intercepted,
                           title=title, html_len=len(html))


def _exhaust_pagination(page, timeout: int) -> None:
    """Scroll / click 'load more' until the profile-link count stops growing, so a
    paginated or lazy-loaded list isn't truncated."""
    deadline = time.time() + timeout
    selector = "a[href*='people.njit.edu/profile/']"
    prev = -1
    stable = 0
    while time.time() < deadline and stable < 2:
        count = page.locator(selector).count()
        if count == prev:
            stable += 1
        else:
            stable = 0
            prev = count
        # Try a "load more" / "next" control if present; else scroll to bottom.
        clicked = False
        for label in ("load more", "show more", "next"):
            btn = page.get_by_role("button", name=re.compile(label, re.I))
            try:
                if btn.count() and btn.first.is_enabled():
                    btn.first.click()
                    clicked = True
                    break
            except Exception:  # noqa: BLE001 - best-effort pagination
                pass
        if not clicked:
            page.mouse.wheel(0, 20000)
        page.wait_for_timeout(800)
