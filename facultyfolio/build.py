"""Build orchestrator — dict -> photo -> render -> write; then leaderboard; then assets.

Idempotent: re-running regenerates every page. Photos are cached by slug, so a rebuild
does no network I/O and produces byte-identical output.
"""
import os

from . import assets, config, db, rank, render


def _write(path: str, html: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)


def build_one(slug: str, out_root: str) -> str:
    """Generate one faculty profile page; return the written path (or '' if suppressed)."""
    faculty = db.get_faculty(slug)
    if faculty["suppressed"]:
        return ""
    assets_dir = os.path.join(out_root, "assets")
    scholar = faculty.get("scholar") or {}
    photo_ref = photos_ensure(slug, scholar.get("photo"), faculty["name"], assets_dir)
    html = render.render_profile(faculty, photo_ref=photo_ref)
    path = os.path.join(out_root, "p", f"{slug}.html")
    _write(path, html)
    return path


def photos_ensure(slug, scholar_photo_url, name, assets_dir):
    # thin indirection so tests can stub network without importing photos everywhere
    from .photos import ensure_photo
    return ensure_photo(slug, scholar_photo_url, name, assets_dir)


def build_leaderboard(out_root: str) -> str:
    ranked = rank.ranked_list(config.CS_ORG_ID)
    coverage = rank.coverage(config.CS_ORG_ID)
    html = render.render_leaderboard("Computer Science", ranked, coverage)
    path = os.path.join(out_root, "cs", "index.html")
    _write(path, html)
    return path


def build_all(out_root: str = None) -> dict:
    out_root = out_root or config.OUT_ROOT
    slugs = db.cs_faculty_slugs()                 # already excludes suppressed
    pages = [build_one(s, out_root) for s in slugs]
    lb = build_leaderboard(out_root)
    assets.copy_assets(out_root)
    return {"profiles": [p for p in pages if p], "leaderboard": lb, "count": len([p for p in pages if p])}


def main():
    result = build_all()
    print(f"FacultyFolio: {result['count']} profiles + leaderboard -> {config.OUT_ROOT}")


if __name__ == "__main__":
    main()
