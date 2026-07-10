"""Pure renderer — faculty dict -> HTML string via Jinja2. No DB, no I/O.

Applies the mechanical formatters and the degradation rules (spec §4). The photo
is resolved elsewhere (photos.py, I/O) and passed in as photo_ref; when omitted a
non-I/O monogram is used so this stays pure and trivially testable.
"""
import datetime
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


def _org_suffix(title: str, org: str) -> str:
    """' · <org>' unless the verbatim title already names the org (casefold substring)."""
    if not org:
        return ""
    return "" if org.lower() in (title or "").lower() else f" · {org}"


def _visible_leadership(f: dict) -> list:
    """Leadership entries to SHOW: drop any whose title is already contained in the home title
    (casefold substring) — else Payton ('Dean, Ying Wu College of Computing' home) and Wu
    ('…Associate Dean for Academic Affairs' home) would repeat their role. SELECTION, not
    rewording. Shared by the header line AND appointment_lines so they never disagree."""
    home = (f.get("title") or "").lower()
    return [L for L in (f.get("leadership") or []) if L["title"].lower() not in home]


def appointment_lines(f: dict) -> list:
    """Structured appointment list, tiers ordered home → leadership → joint → affiliated.
    Each item: {"text": str, "label": str}. No title repeats: home carries rank+dept; leadership
    carries the role title (+org unless embedded); joint/affiliated carry ORG ONLY (no title).
    A single-line list drops the (noise) tier label."""
    out = []
    if f.get("home_dept"):
        rank = f"{f['title']} · {f['home_dept']}" if f.get("title") else f["home_dept"]
        out.append({"text": rank, "label": "home"})
    for L in _visible_leadership(f):
        out.append({"text": f"{L['title']}{_org_suffix(L['title'], L['org'])}", "label": "leadership"})
    if f.get("joint_dept"):
        out.append({"text": f"Joint appointment · {f['joint_dept']}", "label": "joint"})
    for aff in (f.get("affiliated_depts") or []):
        out.append({"text": f"Affiliated · {aff}", "label": "affiliated"})
    if len(out) == 1:
        out[0] = {"text": out[0]["text"], "label": ""}
    return out


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


_NSF_LINK = "https://www.nsf.gov/awardsearch/showAward?AWD_ID={}"
_NIH_LINK = "https://reporter.nih.gov/project-details/{}"


def _exp_date(mdy):
    try:
        return datetime.datetime.strptime(mdy, "%m/%d/%Y").date()
    except (ValueError, TypeError):
        return None


def _year(mdy):
    d = _exp_date(mdy)
    return d.year if d else "—"


def funding_view(f: dict, today: datetime.date = None) -> dict | None:
    """Profile 'Research funding' view-model. NSF group then NIH group, each
    summary + recency-ordered rows. None when there are no contributing rows."""
    fund = f.get("funding") or {}
    today = today or datetime.date.today()
    fy_now = today.year + 1 if today.month >= 10 else today.year
    groups, updated = [], []

    nsf = fund.get("nsf")
    if nsf:
        rows = [a for a in nsf.get("awards", []) if a.get("at_njit")]
        if rows:
            updated.append(nsf["updated_at"])
            rows = sorted(rows, key=lambda a: (_exp_date(a["exp"]) or datetime.date.min,
                                               a["obligated"]), reverse=True)
            n = len(rows)
            groups.append({
                "agency": "NSF awards",
                "summary": f'{F.money_exact(nsf["njit_total"])} obligated · {n} award{"" if n == 1 else "s"}',
                "rows": [{
                    "amount": F.money(a["obligated"]), "unit": "obligated",
                    "title": a["title"], "url": _NSF_LINK.format(a["id"]),
                    "meta": f'NSF {a["id"]}',
                    "years": f'{_year(a["start"])} – {_year(a["exp"])}',
                    "active": bool(_exp_date(a["exp"]) and _exp_date(a["exp"]) >= today),
                    "copi": False,
                } for a in rows],
            })

    nih = fund.get("nih")
    if nih:
        projects = nih.get("projects", [])
        contact = [p for p in projects if p.get("role") == "contact"]
        copi = [p for p in projects if p.get("role") == "co_pi"]
        if contact or copi:
            updated.append(nih["updated_at"])
            key = lambda p: (p.get("fy_last") or 0, p.get("total") or 0)
            ordered = sorted(contact, key=key, reverse=True) + sorted(copi, key=key, reverse=True)
            if contact:
                nc = len(contact)
                summary = (f'{F.money_exact(nih["njit_total"])} project costs · '
                           f'{nc} project{"" if nc == 1 else "s"} (as contact PI)')
            else:
                ncp = len(copi)
                summary = f'co-investigator on {ncp} project{"" if ncp == 1 else "s"}'
            groups.append({
                "agency": "NIH projects", "summary": summary,
                "rows": [{
                    "amount": F.money(p["total"]),
                    "unit": "costs" if p["role"] == "contact" else "project",
                    "title": p["title"],
                    "url": _NIH_LINK.format(p["appl_id"]) if p.get("appl_id") else None,
                    "meta": f'NIH {p["core"]}',
                    "years": f'FY{p["fy_first"]} – FY{p["fy_last"]}' if p.get("fy_first") else "—",
                    "active": bool(isinstance(p.get("fy_last"), int) and p["fy_last"] >= fy_now),
                    "copi": p["role"] == "co_pi",
                } for p in ordered],
            })

    if not groups:
        return None
    present = [g["agency"].split()[0] for g in groups]     # ["NSF"], ["NIH"], or both
    src = " and ".join(present)
    as_of = min(u for u in updated if u)                   # YYYY-MM-DD sorts chronologically
    return {"groups": groups,
            "provenance": f"From {src} public award records · as of {F.date_long(as_of)}"}


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
                   asset_root: str = "../", canonical: str = None,
                   nav: list = None, og_title: str = None, og_description: str = None,
                   dept_url: str = None) -> str:
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
        "leadership": _visible_leadership(f),
        "appointment_lines": appointment_lines(f),
        "about_rows": about_rows(f, config.flag("ABOUT_ROWS")),
        "awards": F.format_awards(f.get("awards_raw")),
        "service": F.format_service(f.get("service_raw") or ""),
        "about_source": config.ABOUT_SOURCE,
        "heading": config.FIXED_HEADING,
        "active_since_label": config.ACTIVE_SINCE_LABEL,
        "scholar": sch,
        "funding": funding_view(f),
        "sync_label": config.sync_label(sch["updated_at"]) if sch else "",
        "sources": "Scholar + NJIT" if sch else "NJIT",
        "home_dept_segment": f.get("home_dept_segment") or "",
        "asset_root": asset_root,
        "canonical": canonical,
        "nav": nav or [],
        "og_title": og_title or name,
        "og_description": og_description,
        "claim_url": config.CLAIM_MAILTO,
        "dept_url": dept_url,
    }
    if sch:
        ctx.update(_scholar_ctx(sch))
    return _env.get_template("profile.html").render(**ctx)


def _rollup_view(r: dict | None) -> dict | None:
    """Raw funding_rollup dict -> template-ready {parts:[($str, agency)], n, as_of}."""
    if not r or (not r.get("nsf") and not r.get("nih")):
        return None
    parts = []
    if r["nsf"]:
        parts.append((F.money(r["nsf"]), "NSF"))
    if r["nih"]:
        parts.append((F.money(r["nih"]), "NIH"))
    return {"parts": parts, "n": r["n_funded"],
            "as_of": F.month_year(r["as_of"]) if r.get("as_of") else ""}


def render_hub(title: str, cards: list, *, eyebrow: str, asset_root: str,
               canonical: str = None, nav: list = None,
               og_title: str = None, og_description: str = None,
               stats: dict = None, leadership: dict = None,
               funding_rollup: dict = None) -> str:
    """Hub landing page (NJIT hub: cards=colleges; college hub: cards=depts). One template.
    `asset_root` = rel path to assets/ for this page's depth; `eyebrow` = 'University'/'College'.
    `stats` = college_rollup dict (college hub only); `leadership` =
    {"dean":[rows],"assoc_deans":[rows],"chairs":[rows]} of `_lb_row` rows. The NJIT hub passes
    neither, so it renders exactly as before. `funding_rollup` = raw rank.funding_rollup dict
    (NJIT hub + college hub both pass one; None -> no `.rollup` line rendered)."""
    return _env.get_template("hub.html").render(
        college_name=title, eyebrow=eyebrow, cards=cards,
        asset_root=asset_root, canonical=canonical,
        nav=nav or [], og_title=og_title or title, og_description=og_description,
        claim_url=config.CLAIM_MAILTO,
        stats=stats, leadership=leadership or {},
        funding_rollup=_rollup_view(funding_rollup))


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
                       asset_root: str = "../", canonical: str = None,
                       nav: list = None, og_title: str = None, og_description: str = None,
                       funding_rollup: dict = None) -> str:
    """Render the directory views (rank default / citations / A–Z [/ ★ Rising]), all faculty shown.

    roster_views = {"rank": by_rank groups, "citations": by_citations rows, "az": by_name rows}.
    rising = (riser rows, funnel dict) from rank.rising, or None. An EMPTY rising set hides the
    ★ Rising tab entirely (S4 — an empty board named "Rising" would read as a negative verdict).
    photo_map = {slug: photo_ref}; a slug absent -> monogram. Sorting is precomputed upstream.
    funding_rollup = raw rank.funding_rollup dict for this dept; None -> no `.rollup` line.
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
        nav=nav or [], og_title=og_title or org_name, og_description=og_description,
        claim_url=config.CLAIM_MAILTO,
        funding_rollup=_rollup_view(funding_rollup),
    )
