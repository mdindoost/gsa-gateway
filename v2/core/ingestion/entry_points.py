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

ROOT = EntryPoint("https://computing.njit.edu/people", "ywcc",
                  "Ying Wu College of Computing", "hub")

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
