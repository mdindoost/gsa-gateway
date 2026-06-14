"""Execute a routed structured query and render a complete, deterministic answer.

The rendered text is BOTH (a) the grounding context handed to the LLM for nicer
prose and (b) the fallback answer if the LLM is unavailable — so it must stand alone
as a correct, complete answer. Empty results are stated honestly, never guessed.
"""

from __future__ import annotations

import sqlite3

from v2.core.retrieval import skills
from v2.core.retrieval.router import Route


def run(conn: sqlite3.Connection, route: Route) -> dict:
    """Execute the route's skill; return a render-ready dict."""
    a = route.args
    org_name = None
    if a.get("org_id") is not None:
        r = conn.execute("SELECT name FROM organizations WHERE id=?", (a["org_id"],)).fetchone()
        org_name = r[0] if r else None

    skill = route.skill
    if skill == "org_departments":
        rows = skills.org_departments(conn, a["org_id"])
    elif skill == "faculty_in_department":
        rows = [n for n, _ in skills.faculty_in_department(conn, a["org_id"])]
    elif skill == "people_by_research_area":
        rows = [n for n, _ in skills.people_by_research_area(conn, a["area"], a.get("org_id"))]
    elif skill == "count_people_by_research_area":
        rows = skills.count_people_by_research_area(conn, a["area"], a.get("org_id"))
    elif skill == "areas_in_org":
        rows = skills.areas_in_org(conn, a["org_id"])
    elif skill == "area_counts":
        rows = skills.area_counts(conn, a["org_id"])
    elif skill == "people_by_area_tag":
        rows = [n for n, _ in skills.people_by_area_tag(conn, a["area"], a.get("org_id"))]
    else:  # pragma: no cover - router only emits known skills
        rows = []
    return {"skill": skill, "org_name": org_name, "area": a.get("area"), "rows": rows}


def _join(names: list[str]) -> str:
    return ", ".join(names)


def format_answer(result: dict) -> str:
    skill, org, area, rows = (result["skill"], result["org_name"],
                              result["area"], result["rows"])
    scope = f" in {org}" if org else ""

    if skill == "org_departments":
        if not rows:
            return f"I don't have department information for {org}."
        return f"{org} has {len(rows)} department(s): {_join(rows)}."

    if skill == "faculty_in_department":
        if not rows:
            return f"I don't have faculty listed for {org}."
        return f"{org} has {len(rows)} faculty: {_join(rows)}."

    if skill == "people_by_research_area":
        if not rows:
            return f"I couldn't find anyone working on \"{area}\"{scope}."
        return f"{len(rows)} faculty work on \"{area}\"{scope}: {_join(rows)}."

    if skill == "count_people_by_research_area":
        return f"{rows} faculty work on \"{area}\"{scope}."

    if skill == "areas_in_org":
        if not rows:
            return f"I don't have research areas listed for {org}."
        return (f"Across the faculty who list research areas{scope}, "
                f"{len(rows)} areas appear: {_join(rows)}.")

    if skill == "area_counts":
        if not rows:
            return f"I don't have research areas listed for {org}."
        ranked = "; ".join(f"{a} ({n})" for a, n in rows)
        return (f"Research areas{scope}, by number of faculty who list them: {ranked}.")

    if skill == "people_by_area_tag":
        if not rows:
            return f"I couldn't find anyone who lists \"{area}\" as a research area{scope}."
        return f"{len(rows)} faculty list \"{area}\" as a research area{scope}: {_join(rows)}."

    return ""  # pragma: no cover
