"""Output-path single source of truth.

Every file the build writes gets its path from here, so the flat -> hierarchical
URL restructure (spec: 2026-07-05 display-flags plan, Task 7) is a one-module change
rather than a hunt through build.py + templates. Today it reproduces the current flat
layout exactly (p/<slug>.html, cs/index.html, assets/) — no behavior change.
"""
import os


def profile_path(out_root: str, slug: str) -> str:
    return os.path.join(out_root, "p", f"{slug}.html")


def leaderboard_path(out_root: str) -> str:
    return os.path.join(out_root, "cs", "index.html")


def assets_dir(out_root: str) -> str:
    return os.path.join(out_root, "assets")
