"""Department registry — makes the faculty-ingest pipeline reusable across NJIT
departments (CS, DS, …) with no per-department code.

Everything downstream of discovery is already department-neutral: NJIT renders every
department's faculty on the same people.njit.edu template, and the org resolver maps
a profile's label to the right org automatically. The ONLY department-specific things
are: which faculty-list page to start from, the fallback org id, and HOW that list
exposes profile URLs (some are static HTML, some are JS-rendered).

Adding a department = one entry here. Nothing else changes.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Department:
    key: str             # short cli key, e.g. "cs"
    name: str            # org name as it appears in the organizations table
    faculty_list: str    # page that links to the faculty profiles
    default_org_id: int  # fallback org when a profile's dept label can't be resolved
    discovery: str       # "static" = profile links are in the served HTML;
                         # "js" = the list is JavaScript-rendered (needs a headless
                         #        fetch — static discovery returns nothing)
    note: str = ""


DEPARTMENTS: dict[str, Department] = {
    "cs": Department(
        key="cs", name="Computer Science",
        faculty_list="https://cs.njit.edu/faculty", default_org_id=5,
        discovery="static"),
    "ds": Department(
        key="ds", name="Data Science",
        faculty_list="https://ds.njit.edu/people", default_org_id=6,
        discovery="js",
        note="ds.njit.edu/people is JS-rendered; static discovery yields 0 links. "
             "Needs a headless fetch (or a static source) before it can be ingested."),
    "informatics": Department(
        key="informatics", name="Informatics",
        faculty_list="https://informatics.njit.edu/faculty", default_org_id=7,
        discovery="static"),
}


def get(key: str) -> Department:
    try:
        return DEPARTMENTS[key.lower()]
    except KeyError:
        raise SystemExit(
            f"unknown department {key!r}; known: {', '.join(sorted(DEPARTMENTS))}")
