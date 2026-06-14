"""Deterministic discovery over NJIT people pages (the spike-proven path).

NJIT renders every people/faculty listing on the same template — section headers
(`<h4>`) over cards, each card an `a[href*="/profile/"]` wrapping `h1.name` and one or
more `p.title`. This module turns that structure into discrete records; the role
**category comes from the section**, not from guessing the title. Hub pages (e.g.
`computing.njit.edu/people`) instead expose "<Label> Learn More" links to their children.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup


@dataclass
class ListingPerson:
    slug: str
    name: str
    section: str
    titles: list[str] = field(default_factory=list)


# Section header → role category. Order matters: a more specific cue wins (e.g.
# "Faculty Emeriti" is emeritus, not faculty; "Joint Faculty" is joint). An
# unrecognized section returns None (unknown/pending) — we never guess a role.
_SECTION_RULES = [
    (re.compile(r"emerit", re.I), "emeritus"),
    (re.compile(r"advis", re.I), "advisor"),
    (re.compile(r"joint", re.I), "joint"),
    (re.compile(r"dean", re.I), "admin"),
    (re.compile(r"chair", re.I), "admin"),
    (re.compile(r"professor|lecturer|faculty", re.I), "faculty"),
    (re.compile(r"staff|administrativ|director|coordinator|assistant|designer", re.I), "staff"),
]


def category_for_section(section: str) -> str | None:
    for rx, cat in _SECTION_RULES:
        if rx.search(section or ""):
            return cat
    return None


def parse_listing(html: str) -> list[ListingPerson]:
    """Every person card on a listing page, with its section and title line(s)."""
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[ListingPerson] = []
    for a in soup.select('a[href*="/profile/"]'):
        nm = a.select_one("h1.name")
        if not nm:
            continue
        titles = [t.get_text(strip=True) for t in a.select("p.title") if t.get_text(strip=True)]
        sec = a.find_previous(["h4", "h3", "h2"])
        slug = a["href"].rstrip("/").split("/profile/")[-1]
        out.append(ListingPerson(slug=slug, name=nm.get_text(strip=True),
                                 section=sec.get_text(strip=True) if sec else "",
                                 titles=titles))
    return out


def hub_children(html: str, base: str = "") -> list[tuple[str, str]]:
    """(label, url) for each '<Label> Learn More' child link on a hub page."""
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for a in soup.find_all("a", href=True):
        t = " ".join(a.get_text(" ").split())
        if "learn more" in t.lower():
            label = re.sub(r"\s*learn more\s*$", "", t, flags=re.I).strip()
            url = urljoin(base, a["href"]) if base else a["href"]
            key = (label, url)
            if label and key not in seen:
                seen.add(key)
                out.append(key)
    return out
