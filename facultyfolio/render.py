"""Pure renderer — faculty dict -> HTML string via Jinja2. No DB, no I/O.

Applies the mechanical formatters and the degradation rules (spec §4). The photo
is resolved elsewhere (photos.py, I/O) and passed in as photo_ref; when omitted a
non-I/O monogram is used so this stays pure and trivially testable.
"""
import os

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config
from . import format as F
from .chart import render_chart

_env = Environment(
    loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), "templates")),
    autoescape=select_autoescape(["html"]),
)


def _appointment(f: dict) -> str:
    lead = f"{f['title']}, {f['home_dept']}" if f.get("title") else (f.get("home_dept") or "")
    if f.get("joint_dept"):
        lead = f"{lead} — joint appointment in {f['joint_dept']}"
    return f"{lead}, {f['college']}." if f.get("college") else f"{lead}."


def _contact(f: dict):
    if not (f.get("office") or f.get("phone")):
        return None
    parts = []
    if f.get("office"):
        parts.append(F.format_office(f["office"]))
    if f.get("email"):
        parts.append(f["email"])
    if f.get("phone"):
        parts.append(f["phone"])
    return " · ".join(parts)


def _pub(p: dict) -> dict:
    cites = p.get("cited_by") or 0
    venue = F.format_venue(p.get("venue") or "") or str(p.get("year") or "")
    return {
        "cites": cites,
        "cites_disp": str(cites) if cites else "—",
        "year": p.get("year", ""),
        "title": p.get("title", ""),
        "url": p.get("url"),
        "venue": venue,
    }


def _scholar_ctx(sch: dict) -> dict:
    cpy = sch.get("cites_per_year") or {}
    years = sorted(int(y) for y in cpy) if cpy else []
    sync_year = int((sch.get("updated_at") or "0")[:4] or 0)
    return {
        "cites": F.commafy(sch["citations"]),
        "h": sch.get("h_index"),
        "i10": sch.get("i10_index"),
        "since_year": sch.get("recent_since_year", ""),
        "recent_cites": F.commafy(sch.get("recent_citations", 0)),
        "recent_h": sch.get("recent_h_index", ""),
        "recent_i10": sch.get("recent_i10_index", ""),
        "active_since": years[0] if years else "",
        "years_active": (years[-1] - years[0] + 1) if years else "",
        "chart_svg": render_chart(cpy, sync_year),
        "top_cited": [_pub(p) for p in (sch.get("top_cited") or [])],
        "newest": [_pub(p) for p in (sch.get("newest") or [])],
    }


def render_profile(f: dict, photo_ref: str = None) -> str:
    name = f["name"]
    if photo_ref is None:
        photo_ref = f"monogram:{F.initials(name)}"
    sch = f.get("scholar")
    ctx = {
        "name": name,
        "title": f.get("title"),
        "home_dept": f.get("home_dept"),
        "joint_dept": f.get("joint_dept"),
        "college": f.get("college"),
        "email": f.get("email"),
        "profiles": f.get("profiles") or {},
        "photo_ref": photo_ref,
        "areas": f.get("areas") or [],
        "appointment": _appointment(f),
        "education": F.format_education(f.get("education_raw") or ""),
        "contact": _contact(f),
        "teaching": F.format_teaching(f.get("teaching_raw") or ""),
        "about_source": config.ABOUT_SOURCE,
        "heading": config.FIXED_HEADING,
        "active_since_label": config.ACTIVE_SINCE_LABEL,
        "scholar": sch,
        "sync_label": config.sync_label(sch["updated_at"]) if sch else "",
        "sources": "Scholar + NJIT-CS" if sch else "NJIT-CS",
    }
    if sch:
        ctx.update(_scholar_ctx(sch))
    return _env.get_template("profile.html").render(**ctx)


def render_leaderboard(org_name: str, ranked: list, coverage: tuple) -> str:
    n, m = coverage
    rows = [
        {
            "rank": r["rank"],
            "name": r["name"],
            "slug": r["slug"],
            "citations": F.commafy(r["citations"]),
            "h_index": r["h_index"] if r["h_index"] is not None else "—",
        }
        for r in ranked
    ]
    return _env.get_template("leaderboard.html").render(
        org_name=org_name, rows=rows, coverage_n=n, coverage_m=m,
    )
