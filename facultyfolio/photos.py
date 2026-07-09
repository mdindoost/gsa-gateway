"""Photo resolution — override -> cached -> NJIT people-card -> Scholar -> monogram.

Photos are output ASSETS, never DB data. The chosen image is written to
assets/photos/<slug>.jpg (the durable copy); with no override a rebuild skips any slug
already saved. Auto order is NJIT-FIRST (official headshots), Scholar as fallback. A
per-person override (drop-in file assets/photos_manual/<slug>.* or config.PHOTO_OVERRIDES)
wins over the cache and the auto order. The NJIT headshot is a deterministic ldapimage URL.
"""
import hashlib
import os
import urllib.request

from . import config
from .format import initials

UA = "GSA-Gateway-FacultyFolio/1.0 (NJIT GSA project)"        # project UA, no personal email
NJIT_IMG = "https://uws.njit.edu/ldapimage.php?format=full&uid={slug}"

# Known "no photo" defaults, fingerprinted so we never ship them (spec §4).
_SCHOLAR_SILHOUETTE = "avatar_scholar_128"                     # URL marker (Scholar's no-photo URL form)
_NJIT_PLACEHOLDER_MD5 = "6c7ddedf95d43600e59046af39862f0c"     # ldapimage default headshot
_SCHOLAR_AVATAR_MD5 = "31cb65bf3c565b39a5c4a575843028a4"       # Scholar generic gray graduation-cap avatar
# The URL marker above only catches Scholar's `avatar_scholar_128` form; a real-photo URL (e.g. a stale
# `citpid=N`) can still SERVE the gray avatar bytes (this is exactly how cliu shipped a gray avatar), so
# _try ALSO rejects by CONTENT md5 against both placeholders (built inline so a test can monkeypatch either).

# Drop-in manual overrides (tracked in the repo, source of truth for pinned photos).
_MANUAL_DIR = os.path.join(os.path.dirname(__file__), "assets", "photos_manual")
_MANUAL_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def _download(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=20).read()


def njit_photo_url(slug: str) -> str:
    return NJIT_IMG.format(slug=slug)


def _try(url: str, is_njit: bool):
    """Return image bytes if the URL yields a REAL photo, else None."""
    if not url:
        return None
    if not is_njit and _SCHOLAR_SILHOUETTE in url:
        return None
    try:
        data = _download(url)
    except Exception:
        return None
    if not data:
        return None
    if hashlib.md5(data).hexdigest() in {_NJIT_PLACEHOLDER_MD5, _SCHOLAR_AVATAR_MD5}:  # content reject (any source)
        return None
    return data


def _manual_file(slug: str):
    for ext in _MANUAL_EXTS:
        p = os.path.join(_MANUAL_DIR, slug + ext)
        if os.path.exists(p):
            return p
    return None


def _override_bytes(slug: str, scholar_url: str):
    """A per-person override's image bytes, or None if no override / it can't resolve.
    Drop-in file beats the config map; config value = njit | scholar | <url> | <local path>."""
    mf = _manual_file(slug)
    if mf:
        with open(mf, "rb") as fh:
            return fh.read()
    directive = config.PHOTO_OVERRIDES.get(slug)
    if not directive:
        return None
    if directive == "njit":
        return _try(njit_photo_url(slug), is_njit=True)
    if directive == "scholar":
        return _try(scholar_url, is_njit=False)
    if directive.startswith("http://") or directive.startswith("https://"):
        try:
            return _download(directive)                       # explicit URL — no silhouette check
        except Exception:
            return None
    if os.path.exists(directive):                             # local path
        with open(directive, "rb") as fh:
            return fh.read()
    return None


def ensure_photo(slug: str, scholar_photo_url: str, name: str, out_dir: str) -> str:
    """Resolve + save the faculty photo; return a relative ref or a monogram sentinel.

    Order: per-person override -> cached jpg -> NJIT ldapimage -> Scholar -> monogram.
    """
    photos_dir = os.path.join(out_dir, "photos")
    os.makedirs(photos_dir, exist_ok=True)
    dest = os.path.join(photos_dir, f"{slug}.jpg")
    ref = f"assets/photos/{slug}.jpg"      # root-relative; templates prepend their page's asset_root

    override = _override_bytes(slug, scholar_photo_url)        # wins over cache + auto
    if override is not None:
        with open(dest, "wb") as fh:
            fh.write(override)
        return ref

    if os.path.exists(dest):                                   # cached -> zero network (idempotent)
        return ref

    data = _try(njit_photo_url(slug), is_njit=True) or _try(scholar_photo_url, is_njit=False)
    if data:
        with open(dest, "wb") as fh:
            fh.write(data)
        return ref
    return f"monogram:{initials(name)}"
