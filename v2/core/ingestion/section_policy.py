"""Section-routing policy for multi-college listings (KG expansion 2026-06-18).

`parse_listing` labels every person with their section header; `category_for_section` maps that
to a category (faculty/admin/staff/emeritus/joint/advisor/None). A college `/our-people` page is
a faculty ROLL-UP that also carries the dean's office, and the Hillier (HCAD) page packs two
schools onto one listing. So an EntryPoint may carry a `policy`, and the listing branch of
`explore()` consults `route(policy, section, category, default_slug)` per person to decide which
org to appoint them to — or `None` to skip the edge entirely (their real home appointment comes
from another listing). When `policy is None` the caller keeps the legacy behavior (everyone →
the listing's own org, with the dean→parent reappointment).

Design: docs/superpowers/specs/2026-06-18-all-colleges-departments-kg-expansion-design.md
"""
from __future__ import annotations

import re

# A college page is a roll-up: keep ONLY the dean's-office / college-staff sections on the
# college org; the faculty (and emeritus/joint/advisor) sections are department people who get
# their real appointment from their department listing → skip here (return None).
_ROLLUP_SKIP_CATEGORIES = {"faculty", "emeritus", "joint", "advisor"}

# HCAD has no department subdomains, so its one listing is split into the two schools by section.
_ARCH = re.compile(r"architect", re.I)
_ARTDESIGN = re.compile(r"art\s*\+?\s*(?:and\s+)?design", re.I)
_LIBRARY = re.compile(r"\blibrar", re.I)   # university library staff cross-listed on the HCAD page


def route(policy: str | None, section: str, category: str | None,
          default_slug: str) -> str | None:
    """Return the org slug to appoint this person to, or None to skip (no edge).

    - None (no policy): default_slug (legacy — caller also applies dean→parent reappointment).
    - 'college_admin_only': keep admin/staff/misc sections on the college; skip rolled-up
      faculty/emeritus/joint/advisor (their department listing owns them).
    - 'hcad_split': route architecture sections → 'njsoa', art+design → 'art-design', university
      library → skip, everything else (leadership/staff/centers/emeritus/…) → 'hcad' (college).
    """
    if not policy:
        return default_slug
    if policy == "college_admin_only":
        return None if category in _ROLLUP_SKIP_CATEGORIES else default_slug
    if policy == "hcad_split":
        if _LIBRARY.search(section or ""):
            return None
        if _ARCH.search(section or ""):
            return "njsoa"
        if _ARTDESIGN.search(section or ""):
            return "art-design"
        return default_slug   # leadership, staff, centers, emeritus, professors of practice, …
    raise ValueError(f"unknown section policy: {policy!r}")
