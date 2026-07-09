"""Output-path single source of truth (URL seam).

Multi-college layout: NJIT hub at /index.html, college hub at /<college>/index.html,
dept leaderboard at /<college>/<dept>/index.html, profiles flat at /p/<slug>.html.
Canonical + sitemap URLs are absolute (config.SITE_ORIGIN + path).
"""
import os

from . import config


def profile_path(out_root: str, slug: str) -> str:
    return os.path.join(out_root, "p", f"{slug}.html")


def leaderboard_path(out_root: str, college_seg: str, dept_seg: str) -> str:
    return os.path.join(out_root, college_seg, dept_seg, "index.html")


def college_hub_path(out_root: str, college_seg: str) -> str:
    return os.path.join(out_root, college_seg, "index.html")


def njit_hub_path(out_root: str) -> str:
    return os.path.join(out_root, "index.html")


def sitemap_path(out_root: str) -> str:
    return os.path.join(out_root, "sitemap.xml")


def robots_path(out_root: str) -> str:
    return os.path.join(out_root, "robots.txt")


def redirect_path(out_root: str, old_segment: str) -> str:
    return os.path.join(out_root, old_segment, "index.html")


def assets_dir(out_root: str) -> str:
    return os.path.join(out_root, "assets")


def rel_root(depth: int) -> str:
    """asset_root for a page `depth` directory levels below the site root."""
    return "../" * depth


def canonical_url(rel_path: str) -> str:
    """Absolute canonical URL. rel_path has no leading slash; a directory ends with '/'."""
    return f"{config.SITE_ORIGIN}/{rel_path}"
