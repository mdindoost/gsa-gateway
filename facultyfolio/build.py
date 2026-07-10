"""Build orchestrator — dict -> photo -> render -> write; then leaderboard; then assets.

Idempotent: re-running regenerates every page. A faculty photo that resolved to a real
image is cached by slug (`assets/photos/<slug>.jpg`), so a rebuild reuses it with no
network I/O and produces byte-identical output. A monogram-only person (no photo anywhere)
has nothing to cache, so their NJIT lookup is re-attempted each run — cheap, output-stable.
The leaderboard reuses the exact photo refs the profile pass resolved (threaded in via
`photo_map`), so it never re-resolves or diverges from the profile pages.
"""
import os
import re

from . import assets, config, db, paths, rank, render, seo

_BADGE_STOP = {"of", "and", "the", "for", "in", "&"}


def _org_badge(slug: str, name: str, is_college: bool = False) -> str:
    """Monogram-badge label for a hub card (Spec B). Override wins; else auto-derive:
    a college = its slug upper-cased (YWCC/MTSM); a dept = its name's word-initials
    (Computer Science -> CS), falling back to the first two letters for a single word."""
    if slug in config.ORG_BADGES:
        return config.ORG_BADGES[slug]
    if is_college:
        return slug.upper()
    words = [w for w in re.split(r"[\s\-]+", name) if w and w.lower() not in _BADGE_STOP]
    letters = "".join(w[0] for w in words)
    return (letters[:4] if len(letters) >= 2 else name[:2]).upper()


def _crumbs(asset_root: str, trail: list) -> list:
    """Breadcrumb model (Spec B). trail = [(label, target_relpath_from_root | None)];
    a None target is the current page (rendered as text, no link). Every href is the
    page's asset_root + the root-relative target, so links resolve at any page depth."""
    return [{"label": lbl, "href": (None if tgt is None else asset_root + tgt)}
            for lbl, tgt in trail]


def _profile_og(faculty: dict) -> str:
    parts = [faculty.get("title"), faculty.get("home_dept"), faculty.get("college")]
    lead = ", ".join(p for p in parts if p)
    return f"{lead} · NJIT faculty profile" if lead else "NJIT faculty profile"


def _write(path: str, html: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)


def photos_ensure(slug, scholar_photo_url, name, assets_dir):
    # thin indirection so tests can stub network without importing photos everywhere
    from .photos import ensure_photo
    return ensure_photo(slug, scholar_photo_url, name, assets_dir)


def _resolve_photo(slug: str, faculty: dict, assets_dir: str) -> str:
    """Resolve one faculty photo using the person's real Scholar photo URL (self-sufficient)."""
    scholar = faculty.get("scholar") or {}
    return photos_ensure(slug, scholar.get("photo"), faculty["name"], assets_dir)


def build_one(slug: str, out_root: str, photo_ref: str = None,
              college_seg: str = None, college_name: str = None,
              dept_slug: str = None, dept_name: str = None) -> str:
    """Generate one faculty profile page; return the written path (or '' if suppressed).

    When `photo_ref` is supplied (by build_all) the photo is not re-resolved; otherwise it
    is resolved here so build_one stays usable standalone. The college/dept args give the
    breadcrumb its ancestors; when omitted (standalone use) they're derived from the KG.
    """
    faculty = db.get_faculty(slug)
    if faculty["suppressed"]:
        return ""
    if photo_ref is None:
        photo_ref = _resolve_photo(slug, faculty, paths.assets_dir(out_root))
    trail = _profile_trail(faculty, college_seg, college_name, dept_slug, dept_name)
    nav = _crumbs("../", trail)
    # card dept back-link: the correct nested /<college>/<dept>/ path (not the legacy ../<dept>/
    # redirect), reused from the dept crumb; None when the person has no resolvable home dept.
    dept_url = ("../" + trail[2][1]) if len(trail) == 4 else None
    html = render.render_profile(
        faculty, photo_ref=photo_ref,
        asset_root="../", canonical=paths.canonical_url(f"p/{slug}.html"),
        nav=nav, og_title=faculty["name"], og_description=_profile_og(faculty), dept_url=dept_url)
    path = paths.profile_path(out_root, slug)
    _write(path, html)
    return path


def _profile_trail(faculty, college_seg, college_name, dept_slug, dept_name):
    """Breadcrumb trail for a profile: NJIT / College / Dept / Name. Uses the passed
    ancestors when present (full build), else derives them from the person's home dept."""
    if college_seg and dept_slug:
        return [("NJIT", ""), (college_name, f"{college_seg}/"),
                (dept_name, f"{college_seg}/{dept_slug}/"), (faculty["name"], None)]
    ds = faculty.get("home_dept_segment")
    if ds:
        try:
            cs = _college_of_dept(ds)          # home dept's published college
        except ValueError:
            cs = None                          # home dept not under a published college -> no dept crumb
        if cs:
            return [("NJIT", ""), (faculty.get("college") or "NJIT", f"{cs}/"),
                    (faculty.get("home_dept") or "", f"{cs}/{ds}/"), (faculty["name"], None)]
    return [("NJIT", ""), (faculty["name"], None)]


def build_dept(org: dict, out_root: str, college_seg: str, photo_map: dict = None,
               college_name: str = None) -> str:
    """Render one department's leaderboard at <college>/<dept>/index.html."""
    roster = rank.roster(org["node_id"])
    coverage = rank.coverage(org["node_id"])
    views = {"rank": rank.by_rank(roster), "citations": rank.by_citations(roster),
             "az": rank.by_name(roster)}
    rising = rank.rising(roster)
    stats = rank.leaderboard_stats(roster, coverage)
    assets_dir = paths.assets_dir(out_root)
    photo_map = dict(photo_map or {})
    for r in roster:
        if r["slug"] not in photo_map:
            photo_map[r["slug"]] = _resolve_photo(r["slug"], db.get_faculty(r["slug"]), assets_dir)
    canonical = paths.canonical_url(f"{college_seg}/{org['slug']}/")
    college_name = college_name or db.college_name(db.org_node_by_slug(college_seg))
    nav = _crumbs("../../", [("NJIT", ""), (college_name, f"{college_seg}/"), (org["name"], None)])
    og = f"{coverage[1]} faculty in {org['name']} at NJIT — ranked by citations, with Google Scholar metrics."
    html = render.render_leaderboard(org["name"], views, stats, coverage, photo_map,
                                     rising=rising, asset_root="../../", canonical=canonical,
                                     nav=nav, og_title=f"{org['name']} — NJIT", og_description=og)
    path = paths.leaderboard_path(out_root, college_seg, org["slug"])
    _write(path, html)
    return path


def _leadership_row(person: dict, photo_map: dict, assets_dir: str, *, title: str) -> dict:
    """Turn a leadership/chair person into a render row, reusing the dept person-card path.
    `person` needs a `slug`; `title` is the display title (the role for deans, or
    'Department Chair, <dept>' for chairs). Areas + photo come from the same source the dept
    pages use, so the card is identical to that person's leaderboard row."""
    slug = person["slug"]
    f = db.get_faculty(slug)
    if slug not in photo_map:
        photo_map[slug] = _resolve_photo(slug, f, assets_dir)
    return render._lb_row({
        "slug": slug, "name": f["name"], "title": title, "areas": f["areas"],
        "citations": None, "h_index": None, "rank_num": None,
    }, photo_map)


def build_college_hub(college_node: int, college_seg: str, out_root: str,
                      photo_map: dict = None) -> str:
    """College hub at <college>/index.html: college-wide stats + a card per dept/school +
    Dean / Associate Deans / Department Chairs sections (all from the KG)."""
    depts = db.dept_orgs_of_college(college_node)
    cards = []
    for d in depts:
        n, m = rank.coverage(d["node_id"])
        cards.append({"name": d["name"], "faculty": m, "scholar": n,
                      "url": f"{d['slug']}/index.html", "badge": _org_badge(d["slug"], d["name"])})
    photo_map = dict(photo_map or {})
    assets_dir = paths.assets_dir(out_root)
    stats = rank.college_rollup(college_node)
    lead = db.college_leadership(college_node)
    leadership = {
        "dean": [_leadership_row(p, photo_map, assets_dir, title=p["title"]) for p in lead["dean"]],
        "assoc_deans": [_leadership_row(p, photo_map, assets_dir, title=p["title"])
                        for p in lead["assoc_deans"]],
        "chairs": [_leadership_row(c, photo_map, assets_dir, title=f"Department Chair, {c['dept_name']}")
                   for c in rank.college_chairs(college_node)],
    }
    canonical = paths.canonical_url(f"{college_seg}/")
    cname = db.college_name(college_node)
    _, cm = db.college_coverage(college_node)
    nav = _crumbs("../", [("NJIT", ""), (cname, None)])
    og = f"{cm} faculty across {len(depts)} departments at {cname}, NJIT — with Google Scholar metrics."
    html = render.render_hub(cname, cards, eyebrow="College",
                             asset_root="../", canonical=canonical,
                             nav=nav, og_title=cname, og_description=og,
                             stats=stats, leadership=leadership)
    path = paths.college_hub_path(out_root, college_seg)
    _write(path, html)
    return path


def build_njit_hub(out_root: str) -> str:
    """NJIT hub at /index.html: a card per PUBLISHED college (subtree-distinct coverage)."""
    cards = []
    for slug in config.PUBLISHED_COLLEGES:            # registry order (deterministic)
        node = db.org_node_by_slug(slug)
        n, m = db.college_coverage(node)
        cards.append({"name": db.college_name(node), "faculty": m, "scholar": n,
                      "url": f"{slug}/index.html", "badge": _org_badge(slug, db.college_name(node), is_college=True)})
    canonical = paths.canonical_url("")
    nav = _crumbs("", [("NJIT", None)])
    og = (f"Faculty across {len(config.PUBLISHED_COLLEGES)} "
          f"college{'s' if len(config.PUBLISHED_COLLEGES) != 1 else ''} at NJIT — "
          "profiles, rankings, and Google Scholar metrics.")
    html = render.render_hub("New Jersey Institute of Technology", cards, eyebrow="University",
                             asset_root="", canonical=canonical,
                             nav=nav, og_title="NJIT faculty", og_description=og)
    path = paths.njit_hub_path(out_root)
    _write(path, html)
    return path


def _redirect_html(target_segment: str) -> str:
    """A minimal meta-refresh page pointing from a legacy segment to the new one."""
    rel = f"../{target_segment}/index.html"
    canon = f"{config.SITE_ORIGIN}/{target_segment}/"
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta http-equiv="refresh" content="0; url={rel}">\n'
        f'<link rel="canonical" href="{canon}">\n'
        '<title>FacultyFolio</title></head>\n'
        f'<body><p>Redirecting to the <a href="{rel}">FacultyFolio faculty directory</a>…</p></body></html>\n'
    )


def _college_of_dept(dept_slug: str) -> str:
    """Parent college slug of a dept, via KG part_of. Raises if dept/college unknown or unpublished."""
    node = db.org_node_by_slug(dept_slug)
    if node is None:
        raise ValueError(f"unknown dept slug {dept_slug!r}")
    for slug in config.PUBLISHED_COLLEGES:
        college_node = db.org_node_by_slug(slug)
        if any(d["slug"] == dept_slug for d in db.dept_orgs_of_college(college_node)):
            return slug
    raise ValueError(f"dept {dept_slug!r} is not under any published college")


def _build_dept_scope(college_seg, org, out_root, built, photo_map, college_name=None):
    """Build one dept's profiles + leaderboard into the shared maps."""
    assets_dir = paths.assets_dir(out_root)
    for s in db.faculty_slugs(org["node_id"]):
        if s in built:
            print(f"[facultyfolio] WARN dup-home faculty {s!r}: kept under {built[s]!r}, "
                  f"skipped under {org['slug']!r}")
            continue
        built[s] = org["slug"]
        faculty = db.get_faculty(s)
        if faculty["suppressed"]:
            continue
        ref = _resolve_photo(s, faculty, assets_dir)
        photo_map[s] = ref
        build_one(s, out_root, photo_ref=ref, college_seg=college_seg,
                  college_name=college_name, dept_slug=org["slug"], dept_name=org["name"])
    build_dept(org, out_root, college_seg, photo_map=photo_map, college_name=college_name)


def _occupied_root_segments(out_root: str) -> set:
    """Root-level segment names a legacy redirect stub must never clobber: the published college
    hubs (each occupies /<college>/) plus the reserved shared roots /p/ (profiles) and /assets/.
    Name-membership, not a filesystem scan — sound for this layout where the only real root pages
    are college hubs, profiles under /p/, and assets under /assets/."""
    return set(config.PUBLISHED_COLLEGES) | {"p", "assets"}


def _assert_slug_uniqueness() -> None:
    """Fail loudly if two DISTINCT faculty node keys resolve to the same URL slug (the shared
    flat /p/<slug>.html + photo namespace). A dup-home person (same key, two edges) is fine."""
    slug_keys = {}
    for cslug in config.PUBLISHED_COLLEGES:
        cnode = db.org_node_by_slug(cslug)
        for org in db.dept_orgs_of_college(cnode):
            for key in db.faculty_keys(org["node_id"]):
                slug = key.split("/")[-1]
                slug_keys.setdefault(slug, set()).add(key)
    collisions = {s: sorted(ks) for s, ks in slug_keys.items() if len(ks) > 1}
    if collisions:
        raise ValueError(f"FacultyFolio slug collision (distinct people, same /p/<slug>): {collisions}")


def _emit_redirects(out_root: str, occupied: set) -> None:
    for old, target in config.LEGACY_REDIRECTS.items():
        if old in occupied:                            # never clobber a real root page (C-1)
            continue
        _write(paths.redirect_path(out_root, old), _redirect_html(target))


def _all_published_urls(out_root: str) -> list:
    """Every canonical URL in the published site, for the sitemap (full set, even on a scoped build)."""
    urls = [paths.canonical_url("")]                   # NJIT hub
    for cslug in config.PUBLISHED_COLLEGES:
        cnode = db.org_node_by_slug(cslug)
        urls.append(paths.canonical_url(f"{cslug}/"))  # college hub
        for org in db.dept_orgs_of_college(cnode):
            urls.append(paths.canonical_url(f"{cslug}/{org['slug']}/"))
            for slug in db.faculty_slugs(org["node_id"]):
                urls.append(paths.canonical_url(f"p/{slug}.html"))
    # de-dup (dup-home faculty appear once) preserving order
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u); out.append(u)
    return out


def _emit_seo(out_root: str) -> None:
    _write(paths.sitemap_path(out_root), seo.sitemap_xml(_all_published_urls(out_root)))
    _write(paths.robots_path(out_root), seo.robots_txt())


def build_site(scope: dict = None, out_root: str = None) -> dict:
    """Scope-aware build. scope=None -> all published; {'college': s}; {'dept': s}.
    Always regenerates the NJIT hub + affected college hub(s) + SEO (ancestor consistency)."""
    out_root = out_root or config.OUT_ROOT
    built, photo_map = {}, {}

    if scope and "dept" in scope:
        college_slugs = [_college_of_dept(scope["dept"])]
        dept_filter = scope["dept"]
    elif scope and "college" in scope:
        if scope["college"] not in config.PUBLISHED_COLLEGES:
            raise ValueError(f"college {scope['college']!r} is not published")
        college_slugs = [scope["college"]]
        dept_filter = None
    else:
        college_slugs = list(config.PUBLISHED_COLLEGES)
        dept_filter = None

    if scope is None:
        _assert_slug_uniqueness()             # full build only: verify the flat /p/ namespace

    for cslug in college_slugs:
        cnode = db.org_node_by_slug(cslug)
        cname = db.college_name(cnode)
        for org in db.dept_orgs_of_college(cnode):
            if dept_filter and org["slug"] != dept_filter:
                continue
            _build_dept_scope(cslug, org, out_root, built, photo_map, college_name=cname)
        build_college_hub(cnode, cslug, out_root, photo_map=photo_map)

    build_njit_hub(out_root)                 # ancestor: always refreshed
    occupied = _occupied_root_segments(out_root)
    _emit_redirects(out_root, occupied)
    _emit_seo(out_root)
    assets.copy_assets(out_root)
    return {"built": sorted(built), "count": len(built)}


def build_all(out_root: str = None) -> dict:   # back-compat alias
    return build_site(scope=None, out_root=out_root)


def _scope_from_args(argv):
    import argparse
    p = argparse.ArgumentParser(prog="facultyfolio.build")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--college", metavar="SLUG")
    g.add_argument("--dept", metavar="SLUG")
    a = p.parse_args(argv)
    if a.college:
        return {"college": a.college}
    if a.dept:
        return {"dept": a.dept}
    return None


def main(argv=None):
    import sys
    scope = _scope_from_args(sys.argv[1:] if argv is None else argv)
    result = build_site(scope=scope)
    label = "all published" if scope is None else next(iter(scope.items()))
    print(f"FacultyFolio: built {result['count']} faculty ({label}) -> {config.OUT_ROOT}")


if __name__ == "__main__":
    main()
