"""Execute a routed structured query and render a complete, deterministic answer.

The rendered text is BOTH (a) the grounding context handed to the LLM for nicer
prose and (b) the fallback answer if the LLM is unavailable — so it must stand alone
as a correct, complete answer. Empty results are stated honestly, never guessed.
"""

from __future__ import annotations

import json
import sqlite3

from v2.core.people import profile_fields
from v2.core.retrieval import entity, skills
from v2.core.retrieval.router import Route


def _person_attrs(conn: sqlite3.Connection, entity_id: str) -> dict:
    row = conn.execute(
        "SELECT attrs FROM nodes WHERE type='Person' AND key=? AND is_active=1",
        (entity_id,)).fetchone()
    if not row or not row[0]:
        return {}
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return {}


def run(conn: sqlite3.Connection, route: Route) -> dict:
    """Execute the route's skill; return a render-ready dict."""
    a = route.args
    org_name = None
    if a.get("org_id") is not None:
        r = conn.execute("SELECT name FROM organizations WHERE id=?", (a["org_id"],)).fetchone()
        org_name = r[0] if r else None

    skill = route.skill

    # ── entity-layer skills (Phase 1+2) ────────────────────────────────────────
    if skill == "role_in_org":
        return {"skill": skill, "org_name": org_name, "role_head": a["role_head"],
                "rows": entity.role_in_org(conn, a["org_id"], a["role_head"])}
    if skill == "people_by_role":
        return {"skill": skill, "org_name": org_name, "role_head": a["role_head"],
                "rows": entity.people_by_role(conn, a["role_head"], a.get("org_id"))}
    if skill == "people_by_name":
        return {"skill": skill, "name": a["name"],
                "rows": entity.people_by_name(conn, a["name"])}
    if skill == "research_of_person":
        return {"skill": skill, "research": entity.research_of_person(conn, a["entity_id"]),
                "metrics": profile_fields.render_metrics(_person_attrs(conn, a["entity_id"]))}
    if skill == "entity_card":
        return {"skill": skill, "name": a.get("name"),
                "card": entity.entity_card(conn, a["entity_id"]),
                "links": profile_fields.render_links(_person_attrs(conn, a["entity_id"]))}
    if skill == "person_disambig":
        return {"skill": skill, "candidates": a["candidates"]}
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
    elif skill == "officers_in_org":
        rows = skills.officers_in_org(conn, a["org_id"])   # list of (name, title, email)
    elif skill == "people_in_org":
        rows = skills.people_in_org(conn, a["org_id"])     # list of (name, title, email)
    else:  # pragma: no cover - router only emits known skills
        rows = []
    return {"skill": skill, "org_name": org_name, "area": a.get("area"), "rows": rows}


def _join(names: list[str]) -> str:
    return ", ".join(names)


def format_answer(result: dict) -> str:
    skill = result["skill"]

    # ── entity-layer skills. Empty → "" so _try_structured falls through to RAG. ──
    if skill == "role_in_org":
        rows = result["rows"]
        if not rows:                       # exact role absent → let RAG try the prose
            return ""
        listed = "; ".join(
            f"{title} — {name}" + (f" ({email})" if email else "")
            for name, title, email in rows)
        return f"{result['org_name']}: {listed}."

    if skill == "people_by_role":
        rows = result["rows"]                # (name, title, org_name, contact)
        if not rows:                         # nobody holds that role → RAG tries the prose
            return ""
        role = result["role_head"]
        scope = f" in {result['org_name']}" if result.get("org_name") else " at NJIT"
        if len(rows) > 25:                   # too many to name — ask to narrow by org
            orgs = sorted({o for _n, _t, o, _c in rows})
            return (f"{len(rows)} people hold a \"{role}\" title{scope}. Narrow it by org — e.g. "
                    + ", ".join(orgs[:6]) + ("…" if len(orgs) > 6 else "") + ".")
        if len(rows) == 1:
            name, title, oname, contact = rows[0]
            return f"{name} — {title} ({oname})" + (f". {contact}" if contact else ".")
        listed = "; ".join(f"{name} — {title} ({oname})" for name, title, oname, _c in rows)
        return f"{len(rows)} hold a \"{role}\" title{scope}: {listed}."

    if skill == "people_by_name":
        rows = result["rows"]
        if not rows:
            return ""
        listed = "; ".join(
            h["name"] + (f" — {h['title']}" if h.get("title") else "")
            + (f" ({h['org']})" if h.get("org") else "")
            for h in rows)
        return (f"{len(rows)} person(s) with \"{result['name']}\" in their name: {listed}.")

    if skill == "research_of_person":
        rp = result["research"]
        if not rp["areas"] and not rp["statement"]:
            return ""                      # honest empty (e.g. no research card) → RAG
        if rp["areas"]:
            return f"{rp['name']}'s research areas: {', '.join(rp['areas'])}."
        return f"{rp['name']}'s research: {rp['statement']}"

    if skill == "entity_card":
        return result["card"] or ""        # the card is the grounding + offline fallback

    if skill == "person_disambig":
        cands = result["candidates"]
        listed = "; ".join(
            c["name"] + (f" ({c['org']})" if c.get("org") else "") for c in cands)
        return (f"There are {len(cands)} people that could match — which one did you mean? "
                f"{listed}.")

    org, area, rows = result["org_name"], result["area"], result["rows"]
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

    if skill == "officers_in_org":
        if not rows:
            return f"I don't have officer information for {org}."
        listed = "; ".join(
            f"{title} — {name}" + (f" ({email})" if email else "")
            for name, title, email in rows)
        return f"{org} has {len(rows)} officer(s): {listed}."

    if skill == "people_in_org":
        if not rows:
            return f"I don't have people listed for {org}."
        listed = "; ".join(
            f"{title} — {name}" + (f" ({email})" if email else "")
            for name, title, email in rows)
        return f"{org} has {len(rows)} people: {listed}."

    return ""  # pragma: no cover


def deterministic_suffix(result: dict) -> str | None:
    """A line to append to the FINAL answer VERBATIM (after LLM composition), so external-
    profile links/metrics are never rephrased or hallucinated. Mirrors the heads-up pattern.

    Surfacing is encoded by the skill: links on the entity card (identity questions),
    metrics on the research-of-person answer — and only when that structured answer actually
    stood (a card / research present); otherwise the query fell through to RAG and we add
    nothing. Roster/list skills add nothing."""
    skill = result.get("skill")
    if skill == "entity_card" and result.get("card"):
        return result.get("links")
    if skill == "research_of_person":
        rp = result.get("research") or {}
        if rp.get("areas") or rp.get("statement"):
            return result.get("metrics")
    return None
