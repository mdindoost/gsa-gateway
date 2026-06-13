"""Precise adapter for the uniform people.njit.edu profile template.

NJIT renders every faculty/staff profile from one template: a ``div.tabbed-content``
with panes (about / teaching / research / publications / service). This adapter
parses that structure into a normalized :class:`EntityRecord` with NO content caps
— every publication citation present in the page is captured, the full research
statement is kept, and the service/teaching/education panes are included.

It is the "precise" leg of the hybrid pipeline; arbitrary-shaped external sites are
handled by the (deferred) generic LLM-extract leg. Pure parsing lives in
``parse_entity(url, html)`` so it is unit-testable offline; ``fetch`` does the I/O.

Note: the publications pane paginates ("SHOW MORE") via JS, so a static fetch sees
the citations rendered server-side. We take all of them — completeness beyond that
needs the JS/API leg (Phase 1b), not a cap here.
"""
from __future__ import annotations

import re
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from v2.core.ingestion.entity import EntityRecord, Publication

UA = "GSA-Gateway-Bot/1.0 (+https://github.com/mdindoost/gsa-gateway)"
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_PUB_TYPE = re.compile(
    r"^(Journal Article|Conference Proceeding|Book Chapter|Book|Thesis|"
    r"Technical Report|Report|Patent|Presentation|Magazine Article|Other)\s*", re.I)
# Department labels we recognize for the display ``org`` — prefer the specific
# department over the umbrella college, which co-occurs on the page.
_DEPTS = ["Computer Science", "Data Science", "Informatics", "Ying Wu College of Computing"]
_NOISE = {"show more", "show less"}
_MIN_PUB_LEN = 40   # a real citation is longer than a "Journal Article" type label
_MIN_LINE_LEN = 5   # skip empty/decorative leaf-div lines in list panes
# A citation "core": the `<Year>. "` that every citation has exactly one of. Used
# to detect when one <br> fragment actually holds several citations (template drift
# to newline-separation), so we can split it instead of silently re-creating the
# multi-paper blob the parser exists to prevent.
_CITE_CORE = re.compile(r"(?:19|20)\d{2}\.\s+\"")
# Boundary to split a multi-citation blob at: whitespace before the next citation's
# author list. Only applied to fragments with >=2 cores, so a single real citation
# (which has author initials like "D. Spielman, I. Koutis.") is never split.
_CITE_START = re.compile(
    r"(?<=[.)\]])\s+(?=(?:[A-Z][A-Za-z.'\-]+,?\s+){1,12}(?:19|20)\d{2}\.\s+\")")


def _split_citations(raw: str) -> list[str]:
    """One <br> fragment -> its citation(s). Whole if it has <=1 core (the normal
    case — never splits a single citation); split only on a genuine multi-citation
    blob."""
    if len(_CITE_CORE.findall(raw)) < 2:
        return [raw]
    return _CITE_START.split(raw)


def fetch(url: str, timeout: int = 25) -> str:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def entity_id_from_url(url: str) -> str:
    """Stable per-entity key from the profile slug (e.g. .../profile/ikoutis).

    Lower-cased so a differently-cased URL for the same person reconciles to the
    same entity instead of forking a duplicate.
    """
    m = re.search(r"people\.njit\.edu/profile/([A-Za-z0-9_-]+)", url)
    slug = (m.group(1) if m else url.rstrip("/").rsplit("/", 1)[-1]).lower()
    return f"people.njit.edu/profile/{slug}"


def _clean(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def _leaf_divs(pane):
    """Innermost divs only — skips the container div that wraps every citation
    (the source of the old 40 KB faculty-card bloat)."""
    return [d for d in pane.find_all("div") if not d.find("div")]


# About-pane section labels -> canonical key. Anything not a label is content for
# the current section; unknown labels are kept under their own slug (nothing dropped).
_ABOUT_LABELS = {
    "about me": "bio", "bio": "bio", "biography": "bio",
    "education": "education",
    "awards & honors": "awards", "awards and honors": "awards",
    "awards & honours": "awards", "awards": "awards", "honors": "awards", "honours": "awards",
    "website": "website", "web page": "website", "webpage": "website", "homepage": "website",
    "websites": "website",
    "experience": "experience", "professional experience": "experience",
}


def _about_sections(pane) -> dict[str, list[str]]:
    """Walk the about pane's ordered leaf-divs, splitting only at KNOWN section-label
    divs. Returns {canonical_key: [content lines]}. Splitting on known labels (not a
    fuzzy header guess) avoids mistaking real content like 'Professor, June 2019 -'
    for a header; an unrecognized new label just merges into the prior section
    (mislabelled, never dropped)."""
    sections: dict[str, list[str]] = {}
    cur = None
    for d in _leaf_divs(pane):
        t = _clean(d)
        if not t:
            continue
        key = _ABOUT_LABELS.get(t.lower())
        if key is not None:
            cur = key
            sections.setdefault(cur, [])
            continue
        if cur is not None:
            sections[cur].append(t)
    return sections


def _list_lines(pane) -> list[str]:
    out, seen = [], set()
    for d in _leaf_divs(pane):
        t = _clean(d)
        low = t.lower()
        if len(t) >= _MIN_LINE_LEN and low not in _NOISE and low not in seen:
            seen.add(low)
            out.append(t)
    return out


def _resolve_org(titles: list[str], html: str, default: str) -> str:
    """Resolve the display department for a profile.

    The department named in the **title/position lines** is authoritative (e.g.
    "Professor, Data Science"), so check those first. Only fall back to the page
    body if the title names no known department — otherwise a Data Science
    professor whose bio mentions "Computer Science" (common: CS background / joint
    history) would be mis-filed under CS. Within each pass, the specific department
    is preferred over the umbrella college by list order (_DEPTS)."""
    title_hay = " ".join(titles).lower()
    full_hay = title_hay + " " + (html or "").lower()
    for hay in (title_hay, full_hay):
        for kw in _DEPTS:
            if kw.lower() in hay:
                return kw
    return default


def parse_entity(url: str, html: str, org_default: str = "") -> EntityRecord:
    soup = BeautifulSoup(html, "html.parser")

    title = soup.find("title")
    name = title.get_text().split("|")[0].strip() if title else ""

    # title/role lines (div.position, <br>-separated for multi-title people)
    titles: list[str] = []
    for d in soup.select("div.position"):
        for part in re.split(r"(?i)<br\s*/?>", d.decode_contents()):
            t = re.sub(r"\s+", " ",
                       BeautifulSoup(part, "html.parser").get_text(" ", strip=True)).strip()
            if t and t not in titles:
                titles.append(t)

    org = _resolve_org(titles, html, org_default)

    contact: dict = {}
    em = re.search(r"[A-Za-z0-9._%+-]+@njit\.edu", html)
    if em:
        contact["email"] = em.group(0)
    ph = soup.find(class_="phone1")
    if ph and _clean(ph):
        contact["phone"] = _clean(ph)
    om = re.search(r"\b\d{3,4}\s+[A-Z][A-Za-z .]{5,60}?(?:Center|Hall|Building|GITC|Tower)\b[^<]{0,25}",
                   html)
    if om:
        contact["office"] = om.group(0).strip()

    # tabbed panes
    panes: dict = {}
    tc = soup.find("div", class_="tabbed-content")
    if tc:
        nav = [a.get("data-target") for a in tc.select("a.tab-control")]
        panes = dict(zip(nav, tc.find_all("div", class_="tab-content")))

    def pane(k):
        return panes.get(k)

    # research pane: full statement + the "Research Interests" list as areas
    research = _clean(pane("research"))
    research_areas: list[str] = []
    ri = re.match(r"(?i)\s*research interests\s+(.*)", research)
    if ri:
        research_areas = [a.strip() for a in re.split(r"\s*;\s*", ri.group(1)) if a.strip()]
    teaching = _list_lines(pane("teaching")) if pane("teaching") else []
    service = _list_lines(pane("service")) if pane("service") else []

    # about pane is a sequence of labelled sections (About Me / Education /
    # Awards & Honors / Website / Experience) — walk them so each lands in the
    # right field instead of everything-after-Education getting dumped together.
    bio, education, awards, experience = "", [], [], []
    website = ""
    if pane("about"):
        sec = _about_sections(pane("about"))
        bio = " ".join(sec.get("bio", [])).strip()
        if sec.get("education"):
            edu = " ".join(sec["education"])
            education = [e.strip() for e in re.split(r"(?<=\d{4})\s+(?=[A-Z])", edu) if e.strip()]
        if sec.get("awards"):
            aw = " ".join(sec["awards"])
            awards = [a.strip() for a in re.split(r"(?<=\S)\s+(?=(?:19|20)\d{2}\s+\S)", aw) if a.strip()]
        experience = [e.strip() for e in sec.get("experience", []) if e.strip()]
        for v in sec.get("website", []):
            mu = re.search(r"https?://\S+", v)
            if mu:
                website = mu.group(0)
                break

    # publications — NJIT groups citations by type into category divs. Citations
    # are separated by a DOUBLE <br>; a SINGLE <br> is an internal line break
    # (e.g. title line / venue+date line of the same paper). So split on runs of
    # 2+ <br> only — splitting on every <br> shatters multi-line citations into a
    # titleless venue fragment. One Publication per paper; no count cap; must carry
    # a year. (Single <br> left inside a fragment becomes whitespace via get_text.)
    pubs: list[Publication] = []
    seen: set[str] = set()
    if pane("publications"):
        for d in _leaf_divs(pane("publications")):
            for frag in re.split(r"(?i)(?:<br\s*/?>\s*){2,}", d.decode_contents()):
                raw = re.sub(r"\s+", " ",
                             BeautifulSoup(frag, "html.parser").get_text(" ", strip=True)).strip()
                for piece in _split_citations(raw):  # whole unless a real blob
                    t = _PUB_TYPE.sub("", piece).strip()
                    if len(t) < _MIN_PUB_LEN or not _YEAR.search(t):
                        continue
                    key = t[:120].lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    yr = _YEAR.search(t)
                    pubs.append(Publication(title=t, year=yr.group(0) if yr else ""))

    # links: the Website section (above), plus scholar/orcid/github anywhere in about
    links: dict = {}
    if website:
        links["website"] = website
    about_html = str(pane("about")) if pane("about") else html
    for label, pat in (("scholar", "scholar.google"), ("orcid", "orcid.org"),
                       ("github", "github.com")):
        lm = re.search(r"https?://[^\"' ]*" + pat + r"[^\"' ]*", about_html)
        if lm:
            links[label] = lm.group(0)

    return EntityRecord(
        entity_id=entity_id_from_url(url), name=name, org=org, source_url=url,
        verified=True, titles=titles, role="", bio=bio,
        research_statement=research, research_areas=research_areas,
        publications=pubs, teaching=teaching, service=service, education=education,
        experience=experience, awards=awards, links=links, contact=contact,
    )
