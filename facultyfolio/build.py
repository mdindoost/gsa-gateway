"""Build orchestrator — dict -> photo -> render -> write; then leaderboard; then assets.

Idempotent: re-running regenerates every page. A faculty photo that resolved to a real
image is cached by slug (`assets/photos/<slug>.jpg`), so a rebuild reuses it with no
network I/O and produces byte-identical output. A monogram-only person (no photo anywhere)
has nothing to cache, so their NJIT lookup is re-attempted each run — cheap, output-stable.
The leaderboard reuses the exact photo refs the profile pass resolved (threaded in via
`photo_map`), so it never re-resolves or diverges from the profile pages.
"""
import os

from . import assets, config, db, paths, rank, render, seo


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


def build_one(slug: str, out_root: str, photo_ref: str = None) -> str:
    """Generate one faculty profile page; return the written path (or '' if suppressed).

    When `photo_ref` is supplied (by build_all) the photo is not re-resolved; otherwise it
    is resolved here so build_one stays usable standalone.
    """
    faculty = db.get_faculty(slug)
    if faculty["suppressed"]:
        return ""
    if photo_ref is None:
        photo_ref = _resolve_photo(slug, faculty, paths.assets_dir(out_root))
    html = render.render_profile(
        faculty, photo_ref=photo_ref,
        asset_root="../", canonical=paths.canonical_url(f"p/{slug}.html"))
    path = paths.profile_path(out_root, slug)
    _write(path, html)
    return path


def build_dept(org: dict, out_root: str, college_seg: str, photo_map: dict = None) -> str:
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
    html = render.render_leaderboard(org["name"], views, stats, coverage, photo_map,
                                     rising=rising, asset_root="../../", canonical=canonical)
    path = paths.leaderboard_path(out_root, college_seg, org["slug"])
    _write(path, html)
    return path


def build_college_hub(college_node: int, college_seg: str, out_root: str) -> str:
    """College hub at <college>/index.html: a card per dept/school with faculty>0."""
    depts = db.dept_orgs_of_college(college_node)
    cards = []
    for d in depts:
        n, m = rank.coverage(d["node_id"])
        cards.append({"name": d["name"], "faculty": m, "scholar": n,
                      "url": f"{d['slug']}/index.html"})
    canonical = paths.canonical_url(f"{college_seg}/")
    html = render.render_hub(db.college_name(college_node), cards, eyebrow="College",
                             asset_root="../", canonical=canonical)
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
                      "url": f"{slug}/index.html"})
    canonical = paths.canonical_url("")
    html = render.render_hub("New Jersey Institute of Technology", cards, eyebrow="University",
                             asset_root="", canonical=canonical)
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


def _build_dept_scope(college_seg, org, out_root, built, photo_map):
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
        build_one(s, out_root, photo_ref=ref)
    build_dept(org, out_root, college_seg, photo_map=photo_map)


def _occupied_root_segments(out_root: str) -> set:
    """Root-level segment names that already hold a real page (published college hubs +
    any existing root dir). Used so a legacy stub never clobbers a real page (C-1)."""
    occupied = set(config.PUBLISHED_COLLEGES)          # e.g. 'ywcc' hub occupies /ywcc/
    return occupied


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

    for cslug in college_slugs:
        cnode = db.org_node_by_slug(cslug)
        for org in db.dept_orgs_of_college(cnode):
            if dept_filter and org["slug"] != dept_filter:
                continue
            _build_dept_scope(cslug, org, out_root, built, photo_map)
        build_college_hub(cnode, cslug, out_root)

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
