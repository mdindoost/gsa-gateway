"""Build orchestrator — dict -> photo -> render -> write; then leaderboard; then assets.

Idempotent: re-running regenerates every page. A faculty photo that resolved to a real
image is cached by slug (`assets/photos/<slug>.jpg`), so a rebuild reuses it with no
network I/O and produces byte-identical output. A monogram-only person (no photo anywhere)
has nothing to cache, so their NJIT lookup is re-attempted each run — cheap, output-stable.
The leaderboard reuses the exact photo refs the profile pass resolved (threaded in via
`photo_map`), so it never re-resolves or diverges from the profile pages.
"""
import os

from . import assets, config, db, paths, rank, render


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
    html = render.render_profile(faculty, photo_ref=photo_ref)
    path = paths.profile_path(out_root, slug)
    _write(path, html)
    return path


def build_dept(org: dict, out_root: str, photo_map: dict = None) -> str:
    """Render one department's 3-view leaderboard at <org slug>/index.html.

    `org` = {"node_id","slug","name","faculty"} from db.dept_orgs_of_college.
    Reuses the profile pass's photo refs when given; resolves any missing itself.
    """
    roster = rank.roster(org["node_id"])
    coverage = rank.coverage(org["node_id"])
    views = {"rank": rank.by_rank(roster), "citations": rank.by_citations(roster),
             "az": rank.by_name(roster)}
    stats = rank.leaderboard_stats(roster, coverage)
    assets_dir = paths.assets_dir(out_root)
    photo_map = dict(photo_map or {})
    for r in roster:                              # fill any slug the caller didn't supply
        if r["slug"] not in photo_map:
            photo_map[r["slug"]] = _resolve_photo(r["slug"], db.get_faculty(r["slug"]), assets_dir)
    html = render.render_leaderboard(org["name"], views, stats, coverage, photo_map)
    path = paths.leaderboard_path(out_root, org["slug"])
    _write(path, html)
    return path


def build_hub(out_root: str, college_node: int, depts: list) -> str:
    """Render the college hub at root index.html: a card per department."""
    cards = [
        {"name": org["name"], "faculty": rank.coverage(org["node_id"])[1],
         "scholar": rank.coverage(org["node_id"])[0], "url": f"{org['slug']}/index.html"}
        for org in depts
    ]
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
    for old, new in config.LEGACY_REDIRECTS.items():   # legacy URL continuity
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
