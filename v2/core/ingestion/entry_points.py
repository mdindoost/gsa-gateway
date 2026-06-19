"""Anchored entry points: a seed URL + prior knowledge (which org it maps to) + kind."""
from __future__ import annotations
import json
import sqlite3
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
    policy: str | None = None  # section-routing policy (see section_policy.route); None = legacy

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

# ── Newark College of Engineering (NCE) ───────────────────────────────────────────────────
# The college /our-people page is a faculty ROLL-UP that also carries the dean's office; the
# 'college_admin_only' policy keeps only the admin/staff sections on `nce` and skips the rolled-up
# faculty (each department listing owns them). Then one listing per engineering department.
NCE_COLLEGE = EntryPoint("https://engineering.njit.edu/our-people", "nce",
                         "Newark College of Engineering", "listing",
                         parent_slug="njit", org_type="college", policy="college_admin_only")
NCE_DEPTS = [
    EntryPoint("https://biomedical.njit.edu/people", "biomedical-engineering",
               "Biomedical Engineering", "listing", parent_slug="nce", org_type="department"),
    EntryPoint("https://cme.njit.edu/people", "chemical-materials-engineering",
               "Chemical & Materials Engineering", "listing", parent_slug="nce", org_type="department"),
    EntryPoint("https://civil.njit.edu/people", "civil-environmental-engineering",
               "Civil & Environmental Engineering", "listing", parent_slug="nce", org_type="department"),
    EntryPoint("https://ece.njit.edu/our-people", "electrical-computer-engineering",
               "Electrical & Computer Engineering", "listing", parent_slug="nce", org_type="department"),
    EntryPoint("https://mie.njit.edu/faculty", "mechanical-industrial-engineering",
               "Mechanical & Industrial Engineering", "listing", parent_slug="nce", org_type="department"),
    EntryPoint("https://appliedengineering.njit.edu/our-people", "applied-engineering-technology",
               "School of Applied Engineering & Technology", "listing", parent_slug="nce", org_type="department"),
]

# ── College of Science and Liberal Arts (CSLA) ────────────────────────────────────────────
# CSLA has no flat college /our-people people list (its site is a department index), so the
# `csla` college org is seeded (SEED_ORGS) and only department listings are anchored. Theatre's
# page does not use the shared profile template (0 profiles) → deferred, not anchored yet.
CSLA_DEPTS = [
    EntryPoint("https://biology.njit.edu/our-people", "biological-sciences",
               "Biological Sciences", "listing", parent_slug="csla", org_type="department"),
    EntryPoint("https://chemistry.njit.edu/people", "chemistry-environmental-science",
               "Chemistry & Environmental Science", "listing", parent_slug="csla", org_type="department"),
    EntryPoint("https://history.njit.edu/people", "history",
               "History", "listing", parent_slug="csla", org_type="department"),
    EntryPoint("https://hss.njit.edu/people", "humanities-social-sciences",
               "Humanities & Social Sciences", "listing", parent_slug="csla", org_type="department"),
    EntryPoint("https://math.njit.edu/our-people", "mathematical-sciences",
               "Mathematical Sciences", "listing", parent_slug="csla", org_type="department"),
    EntryPoint("https://physics.njit.edu/people", "physics",
               "Physics", "listing", parent_slug="csla", org_type="department"),
]

# ── Hillier College of Architecture and Design (HCAD) ──────────────────────────────────────
# No department subdomains: one /our-people page sectioned by school. The 'hcad_split' policy
# routes Architecture sections → `njsoa`, Art+Design → `art-design`, everything else → `hcad`.
HCAD_COLLEGE = EntryPoint("https://design.njit.edu/our-people", "hcad",
                          "Hillier College of Architecture & Design", "listing",
                          parent_slug="njit", org_type="college", policy="hcad_split")

# Orgs that must exist BEFORE the crawl loop (so colleges/departments never orphan): the
# university root, every college, and HCAD's two school orgs (policy targets, not their own
# listings). ensure_org is idempotent, so seeding a college that also has a listing is safe.
# (slug, name, parent_slug, type) — order top-down so each parent exists first.
SEED_ORGS = [
    ("njit", "New Jersey Institute of Technology", None, "university"),
    ("nce", "Newark College of Engineering", "njit", "college"),
    ("csla", "College of Science and Liberal Arts", "njit", "college"),
    ("hcad", "Hillier College of Architecture & Design", "njit", "college"),
    ("njsoa", "New Jersey School of Architecture", "hcad", "school"),
    ("art-design", "School of Art + Design", "hcad", "school"),
]

# Every anchored root the crawler walks on a full gather. run_explore.py iterates this so a
# re-run refreshes ALL colleges and M3 reconciles departures per listing.
# Adding a college = add its EntryPoint(s) here (+ SEED_ORGS if it has policy-target orgs).
# ORDER MATTERS: a sub-unit listing must come AFTER the listing/seed that creates its parent
# org. SEED_ORGS guarantees the colleges exist, so department order within a college is free,
# but we keep college-before-departments for readability. reconcile_departures still runs ONCE
# after the whole loop (in run_explore.py), so cross-listing ordering never causes false retirement.
ALL_ENTRY_POINTS = [
    ROOT, MTSM_FACULTY, MTSM_ADMIN,
    NCE_COLLEGE, *NCE_DEPTS,
    *CSLA_DEPTS,
    HCAD_COLLEGE,
]

# Common short names users actually type → resolved to the right org via metadata.aliases.
# Department official names carry "&"/"and" + extra words ("Civil & Environmental Engineering"),
# so "civil engineering" / "civil and environmental engineering" don't match the name or slug.
# These aliases make the structured router resolve the org (else it falls through to partial RAG).
# Applied by apply_org_aliases() after a crawl, and idempotent. Avoid over-broad single words.
ORG_ALIASES: dict[str, list[str]] = {
    # colleges / schools
    "nce": ["newark college of engineering", "college of engineering", "nce"],
    "csla": ["college of science and liberal arts", "science and liberal arts", "csla"],
    "hcad": ["hillier college of architecture and design", "hillier college",
             "architecture and design", "hcad"],
    "njsoa": ["new jersey school of architecture", "school of architecture", "architecture", "njsoa"],
    "art-design": ["school of art and design", "art and design", "art + design", "art design"],
    # NCE departments
    "biomedical-engineering": ["biomedical engineering", "biomedical", "bme"],
    "chemical-materials-engineering": ["chemical and materials engineering", "chemical engineering",
                                       "materials engineering", "cme"],
    "civil-environmental-engineering": ["civil and environmental engineering", "civil engineering",
                                        "environmental engineering", "cee"],
    "electrical-computer-engineering": ["electrical and computer engineering", "electrical engineering",
                                        "computer engineering", "ece"],
    "mechanical-industrial-engineering": ["mechanical and industrial engineering",
                                          "mechanical engineering", "industrial engineering", "mie"],
    "applied-engineering-technology": ["school of applied engineering and technology",
                                       "applied engineering and technology", "applied engineering",
                                       "engineering technology"],
    # CSLA departments
    "biological-sciences": ["biological sciences", "biology"],
    "chemistry-environmental-science": ["chemistry and environmental science", "chemistry",
                                        "environmental science"],
    "mathematical-sciences": ["mathematical sciences", "mathematics", "math"],
    "humanities-social-sciences": ["humanities and social sciences", "humanities",
                                   "social sciences", "hss"],
    "physics": ["physics"],
    "theater-arts-technology": ["theater arts and technology", "theatre arts and technology",
                                "theatre arts", "theater arts", "theatre", "theater"],
    "njit-administration": ["njit administration", "senior administration", "senior administrators",
                            "university administration", "njit leadership", "senior leadership",
                            "njit cabinet", "njit senior administration"],
}


def apply_org_aliases(conn: sqlite3.Connection) -> int:
    """Write metadata.aliases for known colleges/departments (ORG_ALIASES) so common short names
    route to the right org. Merges into existing metadata (never clobbers other keys). Idempotent;
    only touches orgs that exist. Returns how many orgs were updated."""
    n = 0
    for slug, aliases in ORG_ALIASES.items():
        row = conn.execute("SELECT id, metadata FROM organizations WHERE slug=?", (slug,)).fetchone()
        if not row:
            continue
        meta = json.loads(row[1]) if row[1] else {}
        merged = sorted(set(meta.get("aliases") or []) | set(aliases))
        if merged != (meta.get("aliases") or []):
            meta["aliases"] = merged
            conn.execute("UPDATE organizations SET metadata=? WHERE id=?",
                         (json.dumps(meta), row[0]))
            n += 1
    return n


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
    YWCC's areas are the hub's known child listings (_CHILDREN). Other colleges group their
    listing entry points by parent, deriving a clean area label from each listing's org_name.
    """
    # YWCC hub → child dept/unit listings (dedupe; several labels map to one org)
    ywcc_areas, seen = [], set()
    for _slug, name, _parent in _CHILDREN.values():
        if name not in seen:
            seen.add(name)
            ywcc_areas.append(name)
    scope = [{"college": "Ying Wu College of Computing (YWCC)", "areas": ywcc_areas}]

    # Other colleges: group listing entry points by parent_slug → college display name.
    college_names = {
        "mtsm": "Martin Tuchman School of Management (MTSM)",
        "nce": "Newark College of Engineering (NCE)",
        "csla": "College of Science and Liberal Arts (CSLA)",
        "hcad": "Hillier College of Architecture & Design (HCAD)",
    }
    groups: dict[str, list[str]] = {}
    for e in ALL_ENTRY_POINTS:
        if e is ROOT or e.kind != "listing":
            continue
        # the college this listing belongs to: its parent if the parent is a college, else itself
        college = e.parent_slug if e.parent_slug in college_names else e.org_slug
        if college not in college_names:
            continue
        # the listing's own area label (skip the college's own roll-up listing)
        if e.org_slug == college:
            groups.setdefault(college, [])      # ensure the college shows even if only a roll-up
            continue
        groups.setdefault(college, []).append(e.org_name)
    # HCAD's schools come from the policy, not listings — add them explicitly.
    if "hcad" in groups:
        groups["hcad"] = ["New Jersey School of Architecture", "School of Art + Design"] + groups["hcad"]
    for slug, name in college_names.items():
        if slug in groups:
            scope.append({"college": name, "areas": groups[slug]})
    return scope
