"""Pure renderer — faculty dict -> HTML string via Jinja2. No DB, no I/O.

Applies the mechanical formatters and the degradation rules (spec §4). The photo
is resolved elsewhere (photos.py, I/O) and passed in as photo_ref; when omitted a
non-I/O monogram is used so this stays pure and trivially testable.
"""
import os

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config
from . import format as F
from . import momentum
from .chart import render_chart, sparkline

_env = Environment(
    loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), "templates")),
    autoescape=select_autoescape(["html"]),
)
# Assistant version for the shared footer (base.html) — one source of truth (config → identity).
_env.globals["assistant_version"] = config.ASSISTANT_VERSION
# Depth prefix from a page to the assets dir. Profiles/leaderboards live one level
# deep (p/, <dept>/) -> "../"; the root hub overrides to "" (render_hub).
_env.globals["asset_root"] = "../"


# Fixed lineup of every profile type, in display order. Present -> active link;
# absent -> grayed (Fixed mode) or omitted (Adaptive mode). The SVG glyphs live in
# the template keyed by this same `key`.
_SOCIAL_ORDER = [
    ("email", "Email"),
    ("scholar", "Google Scholar"),
    ("website", "Website"),
    ("linkedin", "LinkedIn"),
    ("github", "GitHub"),
    ("orcid", "ORCID"),
]


def social_icons(f: dict, mode: str) -> list:
    """Build the ordered social-icon list for a faculty dict.

    Fixed   -> all 6 types every page (missing ones inactive/grayed).
    Adaptive-> only the types the person actually has.
    Dedup (both modes): if a profile's URL was already claimed by an earlier icon in
    the order (e.g. a Website whose URL IS the Scholar URL), it's not a distinct link —
    grayed in Fixed, omitted in Adaptive — so no two icons point to the same place.
    """
    profiles = f.get("profiles") or {}
    email = f.get("email")
    seen = set()                              # profile URLs already claimed by an earlier icon

    out = []
    for key, title in _SOCIAL_ORDER:
        if key == "email":
            u = f"mailto:{email}" if email else None
        else:
            u = (profiles.get(key) or {}).get("url") or None
            if u and u in seen:
                u = None                      # duplicate of an earlier profile link
            elif u:
                seen.add(u)
        active = bool(u)
        if mode == "Adaptive" and not active:
            continue
        out.append({"key": key, "title": title, "url": u, "active": active})
    return out


_ALWAYS_ADAPTIVE_ROWS = {"Teaching interests", "Research interests"}   # sparse: omit when empty regardless of ABOUT_ROWS mode


def about_rows(f: dict, mode: str) -> list:
    """The Background block's optional rows (Appointment is separate — always shown).

    Fixed    -> all rows every page; a missing one is grayed with config.ABOUT_EMPTY_LABEL.
    Adaptive -> only rows that have data (the original omit-when-empty behavior).
    """
    items = [
        ("Education", " · ".join(F.format_education(f.get("education_raw") or ""))),
        ("Research interests", F.clean_research_statement(f.get("research_statement_raw") or "")),
        ("Office", _contact(f) or ""),
        ("Teaching interests", ", ".join(F.format_teaching_interests(f.get("teaching_raw") or ""))),
        ("Teaching", " · ".join(F.format_teaching(f.get("teaching_raw") or ""))),
    ]
    out = []
    for label, val in items:
        present = bool(val)
        # sparse rows are ALWAYS adaptive (omit when empty) even in Fixed mode — a page-wide
        # "Not listed" for a field most faculty lack is clutter, not a useful claim nudge.
        adaptive = mode == "Adaptive" or label in _ALWAYS_ADAPTIVE_ROWS
        if adaptive and not present:
            continue
        out.append({"label": label,
                    "value": val if present else config.ABOUT_EMPTY_LABEL,
                    "present": present})
    return out


def _appointment(f: dict) -> str:
    lead = f"{f['title']}, {f['home_dept']}" if f.get("title") else (f.get("home_dept") or "")
    if f.get("joint_dept"):
        lead = f"{lead} — joint appointment in {f['joint_dept']}"
    for aff in (f.get("affiliated_depts") or []):
        lead = f"{lead} — affiliated with {aff}"
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
        "recent_trend": momentum.recent_trend(cpy, sync_year),
        "top_cited": [_pub(p) for p in (sch.get("top_cited") or [])],
        "newest": [_pub(p) for p in (sch.get("newest") or [])],
    }


def render_profile(f: dict, photo_ref: str = None,
                   asset_root: str = "../", canonical: str = None) -> str:
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
        "social_icons": social_icons(f, config.flag("SOCIAL_ICONS")),
        "photo_ref": photo_ref,
        "areas": f.get("areas") or [],
        "appointment": _appointment(f),
        "about_rows": about_rows(f, config.flag("ABOUT_ROWS")),
        "awards": F.format_awards(f.get("awards_raw")),
        "service": F.format_service(f.get("service_raw") or ""),
        "about_source": config.ABOUT_SOURCE,
        "heading": config.FIXED_HEADING,
        "active_since_label": config.ACTIVE_SINCE_LABEL,
        "scholar": sch,
        "sync_label": config.sync_label(sch["updated_at"]) if sch else "",
        "sources": "Scholar + NJIT" if sch else "NJIT",
        "home_dept_segment": f.get("home_dept_segment") or "",
        "asset_root": asset_root,
        "canonical": canonical,
    }
    if sch:
        ctx.update(_scholar_ctx(sch))
    return _env.get_template("profile.html").render(**ctx)


def render_hub(title: str, cards: list, *, eyebrow: str, asset_root: str,
               canonical: str = None) -> str:
    """Hub landing page (NJIT hub: cards=colleges; college hub: cards=depts). One template.
    `asset_root` = rel path to assets/ for this page's depth; `eyebrow` = 'University'/'College'."""
    return _env.get_template("hub.html").render(
        college_name=title, eyebrow=eyebrow, cards=cards,
        asset_root=asset_root, canonical=canonical)


_LB_AREA_CHIPS = 4          # chips shown per directory row; full list is on the profile + in data-areas


def _lb_row(r: dict, photo_map: dict) -> dict:
    """One roster dict -> a template-ready leaderboard row (photo, formatted numbers, chips)."""
    areas = r.get("areas") or []
    has_scholar = r.get("citations") is not None
    return {
        "slug": r["slug"],
        "name": r["name"],
        "title": r.get("title") or "",
        "photo_ref": photo_map.get(r["slug"]) or f"monogram:{F.initials(r['name'])}",
        "areas": areas[:_LB_AREA_CHIPS],           # display cap (serving layer; full list one click away)
        "data_areas": " ".join(areas),             # search matches ALL areas, not just the shown chips
        "rank_num": r.get("rank_num"),
        "has_scholar": has_scholar,
        "citations": F.commafy(r["citations"]) if has_scholar else "—",
        "h_index": r["h_index"] if r.get("h_index") is not None else "—",
    }


def _rising_row(r: dict, photo_map: dict) -> dict:
    """One momentum view-model -> a template-ready ★ Rising row. The `%/yr` (or ▲ glyph)
    is ALWAYS accompanied by the sparkline + the absolute recent-rate chip (the hard rule)."""
    areas = r.get("areas") or []
    return {
        "slug": r["slug"],
        "name": r["name"],
        "title": r.get("title") or "",
        "photo_ref": photo_map.get(r["slug"]) or f"monogram:{F.initials(r['name'])}",
        "data_areas": " ".join(areas),
        "spark_svg": sparkline(r["values"]),
        "momentum": "▲ growing" if r["glyph"] else f"+{r['momentum_pct']}%/yr",
        "window": "" if r["glyph"] else r["window"],
        "recent_rate": r["recent_rate"],
    }


# Panel caption is window-free (mixed per-person sync years) — each row carries its own window.
_RISING_CAPTION = (
    "Faculty whose annual citations grew over their five most recent complete years "
    "(the current year is excluded — it is still accruing). Citations lag research by 2–5 "
    "years, and faculty with large established citation bases naturally show flatter recent "
    "growth — this highlights recent momentum, not overall impact or research quality. See "
    "the By citations view for lifetime totals."
)


def _rising_funnel_text(fn: dict) -> str:
    """Computed coverage funnel (honesty anchor) — never a literal."""
    return (
        f"{fn['risers']} of {fn['gated']} faculty with growing citations "
        f"(Scholar-listed with ≥5 complete years; "
        f"{fn['scholar']} of {fn['total']} faculty are on Google Scholar)."
    )


def render_leaderboard(org_name: str, roster_views: dict, stats: dict,
                       coverage: tuple, photo_map: dict, rising=None,
                       asset_root: str = "../", canonical: str = None) -> str:
    """Render the directory views (rank default / citations / A–Z [/ ★ Rising]), all faculty shown.

    roster_views = {"rank": by_rank groups, "citations": by_citations rows, "az": by_name rows}.
    rising = (riser rows, funnel dict) from rank.rising, or None. An EMPTY rising set hides the
    ★ Rising tab entirely (S4 — an empty board named "Rising" would read as a negative verdict).
    photo_map = {slug: photo_ref}; a slug absent -> monogram. Sorting is precomputed upstream.
    """
    if config.LEADERBOARD_DEFAULT_VIEW not in config.LEADERBOARD_VIEWS:
        raise ValueError(
            f"LEADERBOARD_DEFAULT_VIEW={config.LEADERBOARD_DEFAULT_VIEW!r} "
            f"must be one of {config.LEADERBOARD_VIEWS}"
        )
    n, m = coverage
    rank_groups = [
        {"label": g["label"], "members": [_lb_row(x, photo_map) for x in g["members"]]}
        for g in roster_views["rank"]
    ]
    cite_rows = [_lb_row(x, photo_map) for x in roster_views["citations"]]
    az_rows = [_lb_row(x, photo_map) for x in roster_views["az"]]

    rising_rows, rising_funnel_text, show_rising = [], "", False
    if rising:
        rows, funnel = rising
        if rows:                                   # empty -> hide the tab (S4)
            rising_rows = [_rising_row(x, photo_map) for x in rows]
            rising_funnel_text = _rising_funnel_text(funnel)
            show_rising = True

    return _env.get_template("leaderboard.html").render(
        org_name=org_name, rank_groups=rank_groups, cite_rows=cite_rows, az_rows=az_rows,
        stats=stats, coverage_n=n, coverage_m=m,
        default_view=config.LEADERBOARD_DEFAULT_VIEW,
        show_rising=show_rising, rising_rows=rising_rows,
        rising_caption=_RISING_CAPTION, rising_funnel=rising_funnel_text,
        asset_root=asset_root, canonical=canonical,
    )
