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


def build_hub(out_root: str, college_node: int, depts: list) -> str:
    """Render the college hub at root index.html: a card per department."""
    cards = []
    for org in depts:
        n_scholar, m_total = rank.coverage(org["node_id"])   # coverage once per dept
        cards.append({"name": org["name"], "faculty": m_total, "scholar": n_scholar,
                      "url": f"{org['slug']}/index.html"})
    html = render.render_hub(db.college_name(college_node), cards)
    path = paths.hub_path(out_root)
    _write(path, html)
    return path


def _redirect_html(target_segment: str) -> str:
    """A minimal meta-refresh page pointing from a legacy segment to the new one."""
    url = f"../{target_segment}/index.html"
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta http-equiv="refresh" content="0; url={url}">\n'
        f'<link rel="canonical" href="{url}">\n'
        '<title>FacultyFolio</title></head>\n'
        f'<body><p>Redirecting to the <a href="{url}">FacultyFolio faculty directory</a>…</p></body></html>\n'
    )


def build_all(out_root: str = None) -> dict:
    out_root = out_root or config.OUT_ROOT
    assets_dir = paths.assets_dir(out_root)
    college_node = db.org_node_by_slug(config.COLLEGE_SLUG)
    depts = db.dept_orgs_of_college(college_node)      # sorted by slug, faculty>0

    photo_map, pages, built = {}, [], {}
    for org in depts:                                  # profiles: each unique home faculty once
        for s in db.faculty_slugs(org["node_id"]):
            if s in built:                             # dup-home (data regression) -> LOUD, keep first
                print(f"[facultyfolio] WARN dup-home faculty {s!r}: "
                      f"kept under {built[s]!r}, skipped under {org['slug']!r}")
                continue
            built[s] = org["slug"]
            faculty = db.get_faculty(s)
            if faculty["suppressed"]:
                continue
            ref = _resolve_photo(s, faculty, assets_dir)
            photo_map[s] = ref
            pages.append(build_one(s, out_root, photo_ref=ref))

    leaderboards = [build_dept(org, out_root, photo_map=photo_map) for org in depts]
    hub = build_hub(out_root, college_node, depts)
    dept_slugs = {d["slug"] for d in depts}
    for old, new in config.LEGACY_REDIRECTS.items():   # legacy URL continuity
        if old in dept_slugs:                          # never clobber a live dept directory
            continue
        _write(paths.redirect_path(out_root, old), _redirect_html(new))
    assets.copy_assets(out_root)

    profiles = [p for p in pages if p]
    return {"profiles": profiles, "leaderboards": leaderboards, "hub": hub,
            "count": len(profiles)}


def main():
    result = build_all()
    print(f"FacultyFolio: {result['count']} profiles + "
          f"{len(result['leaderboards'])} dept leaderboards + hub -> {config.OUT_ROOT}")


if __name__ == "__main__":
    main()
