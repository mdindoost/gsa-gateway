"""Anchored entry points: a seed URL + prior knowledge (which org it maps to) + kind."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class EntryPoint:
    url: str
    org_slug: str
    org_name: str
    kind: str            # 'hub' | 'listing' | 'profile'
    parent_slug: str | None = None
    aspect: str = "people"
    org_type: str = "unit"   # type to create the org as when ensure_org first makes it

ROOT = EntryPoint("https://computing.njit.edu/people", "ywcc",
                  "Ying Wu College of Computing", "hub")

# Martin Tuchman School of Management (MTSM) — a SECOND college. MTSM renders people on the
# same NJIT template as YWCC, so no new parser is needed; we only anchor its two listing
# pages. Two orgs (mtsm + mtsm-administration) so a person on BOTH pages (faculty who is also
# a program director) accumulates two correct has_role edges instead of one clobbering the
# other — mirrors YWCC's college-administration split. MTSM has NO type='department' children
# (its sections are role-based: Leadership, Professors, …), an invariant verify_kg checks.
MTSM_FACULTY = EntryPoint("https://management.njit.edu/faculty", "mtsm",
                          "Martin Tuchman School of Management (MTSM)", "listing",
                          parent_slug="njit", org_type="college")
MTSM_ADMIN = EntryPoint("https://management.njit.edu/administration", "mtsm-administration",
                        "MTSM Administration", "listing", parent_slug="mtsm", org_type="unit")

# Every anchored root the crawler walks on a full gather. run_explore.py iterates this so a
# re-run refreshes ALL colleges (YWCC + MTSM) and M3 reconciles departures per listing.
# Adding a college = add its EntryPoint(s) here.
# ORDER MATTERS: a sub-unit listing must come AFTER the listing that creates its parent org,
# or ensure_org can't resolve the parent and creates an orphan (parent_id=NULL). So MTSM_FACULTY
# (creates the `mtsm` college) precedes MTSM_ADMIN (creates `mtsm-administration` under it).
# reconcile_departures still runs ONCE after the whole loop (in run_explore.py), so cross-listing
# ordering never causes false retirement.
ALL_ENTRY_POINTS = [ROOT, MTSM_FACULTY, MTSM_ADMIN]

# hub child label (lowercased substring) -> (org_slug, org_name, parent_slug)
_CHILDREN = {
    "college administration": ("college-administration", "College Administration", "ywcc"),
    "academic advisors":      ("college-administration", "College Administration", "ywcc"),
    "computer science":       ("computer-science", "Computer Science", "ywcc"),
    "data science":           ("data-science", "Data Science", "ywcc"),
    "informatics":            ("informatics", "Informatics", "ywcc"),
}

def child_for(label: str, url: str) -> EntryPoint | None:
    low = label.lower()
    for key, (slug, name, parent) in _CHILDREN.items():
        if key in low:
            return EntryPoint(url=url, org_slug=slug, org_name=name, kind="listing",
                              parent_slug=parent)
    return None


def crawl_scope() -> list[dict]:
    """Human-readable coverage of a full explore() gather (every root in ALL_ENTRY_POINTS),
    grouped by college — for the dashboard Jobs tab so the KG gather's real scope is visible.
    YWCC's areas are the hub's known child listings (_CHILDREN), so they stay in sync (e.g.
    Informatics shows automatically). Non-hub colleges (MTSM) list their listing entry points.
    """
    # YWCC hub → child dept/unit listings (dedupe; several labels map to one org)
    ywcc_areas, seen = [], set()
    for _slug, name, _parent in _CHILDREN.values():
        if name not in seen:
            seen.add(name)
            ywcc_areas.append(name)
    scope = [{"college": "Ying Wu College of Computing (YWCC)", "areas": ywcc_areas}]

    # Non-hub colleges: group listing entry points by college, deriving a short area label
    # from each listing URL's tail (/faculty → Faculty, /administration → Administration).
    mtsm_areas = [e.url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
                  for e in (MTSM_FACULTY, MTSM_ADMIN)]
    scope.append({"college": "Martin Tuchman School of Management (MTSM)", "areas": mtsm_areas})
    return scope
