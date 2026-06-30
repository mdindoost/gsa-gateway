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
    # Single per-person attrs reader lives in entity.py; delegate so there is one read path.
    return entity.person_attrs(conn, entity_id)


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
    if skill == "metric_of_person":
        r = entity.metric_of_person(conn, a["entity_id"], a["field_key"], a.get("metric_key"))
        return {"skill": skill, "field_key": a["field_key"], "metric_key": a.get("metric_key"), **r}
    if skill == "top_people_by_metric":
        r = skills.top_people_by_metric(conn, a["org_id"], a["field_key"], a["metric_key"])
        return {"skill": skill, "org_name": org_name, "field_key": a["field_key"],
                "metric_key": a["metric_key"], "n": a.get("n", 1),
                "org_defaulted": a.get("org_defaulted", False), **r}
    if skill == "metric_descending_unsupported":
        # No DB query — a deterministic decline (mirrors person_disambig). field/metric come straight
        # from the route so format_answer can name the metric + offer the highest-ranked alternative.
        return {"skill": skill, "field_key": a["field_key"], "metric_key": a["metric_key"]}
    if skill == "link_of_person":
        r = entity.link_of_person(conn, a["entity_id"], a["field_key"])
        return {"skill": skill, "field_key": a["field_key"], **r}
    if skill == "papers_of_person":
        r = entity.papers_of_person(conn, a["entity_id"], a["mode"])
        return {"skill": skill, "n": a.get("n", 1), **r}
    if skill == "person_disambig":
        return {"skill": skill, "candidates": a["candidates"]}
    if skill == "faculty_areas_in_department":
        rows = skills.faculty_areas_in_department(conn, a["org_id"])
        # honest fallback when NObody lists areas: the roster (names only) + a 'no areas' line.
        roster = [] if rows else [n for n, _ in skills.faculty_in_department(conn, a["org_id"])]
        return {"skill": skill, "org_name": org_name, "rows": rows, "roster": roster}
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


# Deterministic answers whose NUMBERS must never be reworded by the LLM — the caller skips
# compose_from_rows for these (their format_answer output IS the final answer).
_DETERMINISTIC_SKILLS = frozenset({"metric_of_person", "top_people_by_metric", "link_of_person",
                                   "metric_descending_unsupported", "papers_of_person",
                                   "citation_trend_of_person"})


def is_deterministic(result: dict) -> bool:
    """True for skills whose rendered answer must be sent VERBATIM (no LLM compose)."""
    return result.get("skill") in _DETERMINISTIC_SKILLS


def _fmt_metrics(field_key: str, present: dict) -> str:
    """Render the present metrics of a field via the registry templates, in registry order:
    {"citations":2774,"h_index":26} -> "2,774 citations, h-index 26"."""
    parts = [m.template.format(v=present[m.key])
             for fk, m in profile_fields.metric_fields()
             if fk == field_key and m.key in present]
    return ", ".join(parts)


def _metric_noun(metric_key: str) -> str:
    return metric_key.replace("_", "-")   # citations / h-index / i10-index


def _fmt_paper(p: dict) -> str:
    """One captured paper, rendered verbatim: '"Title" (year, venue) — N citations. url'.
    Citation clause omitted for an uncited paper; url/meta omitted when absent."""
    title = (p.get("title") or "").strip()
    meta = ", ".join(x for x in (p.get("year"), p.get("venue")) if x)
    head = f'"{title}"' + (f" ({meta})" if meta else "")
    cb = p.get("cited_by")
    cite = f" — {cb:,} citations" if cb else ""
    url = p.get("url")
    return f"{head}{cite}" + (f". {url}" if url else ".")


def _top_with_ties(ranked: list, n: int) -> list:
    """The top n, extended to include everyone tied with the n-th value (so n=1 never names just
    one of several tied top people)."""
    if len(ranked) <= n:
        return list(ranked)
    cutoff = ranked[n - 1][1]
    k = n
    while k < len(ranked) and ranked[k][1] == cutoff:
        k += 1
    return ranked[:k]


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

    if skill == "metric_of_person":
        name, found, allm = result["name"], result["found"], result["all"]
        updated = result.get("updated_at")
        asof = f" (as of {updated})" if updated else ""
        if found:
            return f"{name} — {_fmt_metrics(result['field_key'], found)}{asof}."
        if allm:                                   # partial miss: offer the metrics we DO have
            noun = _metric_noun(result["metric_key"])
            return (f"I don't have the {noun} on file for {name} — "
                    f"I do have {_fmt_metrics(result['field_key'], allm)}{asof}.")
        return f"I don't have Scholar metrics on file for {name}."

    if skill == "top_people_by_metric":
        org = result.get("org_name") or "this group"
        ranked, wm, total = result["ranked"], result["with_metric"], result["total_in_org"]
        n, noun = result.get("n", 1), _metric_noun(result["metric_key"])
        fk = result["field_key"]
        if wm == 0:
            return f"I don't have Scholar metrics on file for anyone in {org}."
        cov = f"{wm} of {org}'s {total} people"
        caveat = ", so this isn't a full ranking" if wm < total else ""
        sel = _top_with_ties(ranked, n)
        vstr = lambda v: _fmt_metrics(fk, {result["metric_key"]: v})  # noqa: E731
        if len(sel) == 1:
            ans = (f"I have Scholar {noun} for {cov}{caveat}. "
                   f"The highest is {sel[0][0]} ({vstr(sel[0][1])}).")
        else:
            listed = "; ".join(f"{i}. {nm} ({vstr(v)})" for i, (nm, v) in enumerate(sel, 1))
            if len(ranked) < n:                    # asked for more than exist with metrics
                ans = (f"You asked for the top {n}, but I only have Scholar {noun} for "
                       f"{cov}{caveat}: {listed}.")
            else:
                ans = f"Top {len(sel)} in {org} by {noun} — I have metrics for {cov}{caveat}: {listed}."
        if result.get("org_defaulted"):            # bare "most cited professor" → defaulted NJIT-wide
            ans += (" This is university-wide. Want a specific college or department? "
                    "Just name it (e.g. 'most cited in YWCC').")
        return ans

    if skill == "metric_descending_unsupported":
        # Deterministic decline (in _DETERMINISTIC_SKILLS → no LLM). Names NO person; offers the
        # highest-ranked alternative; coverage kept qualitative (no baked numbers — they drift).
        noun = _metric_noun(result["metric_key"])
        return (f"I can only rank people by highest {noun} (e.g. most cited, top h-index), not "
                f"lowest — and my Scholar coverage is partial, so a 'least {noun}' ranking wouldn't "
                f"be meaningful. Want the most {noun} instead?")

    if skill == "link_of_person":
        # Deterministic + TERMINAL: a present URL or an honest-empty line — never "" (which would
        # fall to RAG and risk surfacing a stale/hallucinated link).
        if result.get("url"):
            return f"{result['name']}'s {result['field_label']}: {result['url']}"
        return f"I don't have a {result['field_label']} on file for {result['name']}."

    if skill == "papers_of_person":
        # Deterministic: titles/years/venues/citation-counts/links are emitted VERBATIM from the
        # captured highlight set. Empty → "" so _try_structured falls through to RAG (honest: we only
        # track the most-cited / newest / this-year papers, not the full bibliography).
        papers = result["papers"]
        if not papers:
            return ""
        name, mode, n = result["name"], result["mode"], result.get("n", 1)
        if mode == "current_year":
            yr = papers[0].get("year", "this year")
            listed = "; ".join(_fmt_paper(p) for p in papers[:max(n, 1)])
            return f"{name} published {len(papers)} paper(s) in {yr}: {listed}"
        label = "most-cited" if mode == "most_cited" else "newest"
        if n and n > 1:
            top = papers[:n]
            listed = " ".join(f"{i}. {_fmt_paper(p)}" for i, p in enumerate(top, 1))
            return f"{name}'s top {len(top)} {label} papers: {listed}"
        return f"{name}'s {label} paper: {_fmt_paper(papers[0])}"

    if skill == "entity_card":
        return result["card"] or ""        # the card is the grounding + offline fallback

    if skill == "faculty_areas_in_department":
        org = result.get("org_name") or "this department"
        rows = result["rows"]                      # (name, [areas]) — only people WITH areas
        if rows:
            listed = "; ".join(f"{n} — {', '.join(areas)}" for n, areas in rows)
            return f"{len(rows)} of the {org} faculty list research areas: {listed}."
        roster = result.get("roster") or []        # honest-partial: nobody lists areas
        if not roster:
            return ""                              # no faculty at all → RAG
        return (f"I don't have research areas listed for {org}'s faculty. "
                f"The faculty are: {_join(roster)}.")

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
