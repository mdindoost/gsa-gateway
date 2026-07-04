"""Photo resolution — Scholar -> NJIT people-card -> monogram (spec §4, §4a).

Photos are output ASSETS, never DB data. The chosen image is downloaded to
assets/photos/<slug>.jpg (the durable copy); a rebuild skips any slug already saved.
The NJIT headshot is a deterministic URL (ldapimage), so no HTML parsing is needed.
"""
import hashlib
import os
import urllib.request

from .format import initials

UA = "GSA-Gateway-FacultyFolio/1.0 (NJIT GSA project)"        # project UA, no personal email
NJIT_IMG = "https://uws.njit.edu/ldapimage.php?format=full&uid={slug}"

# Known "no photo" defaults, fingerprinted so we never ship them (spec §4).
_SCHOLAR_SILHOUETTE = "avatar_scholar_128"                     # URL marker
_NJIT_PLACEHOLDER_MD5 = "6c7ddedf95d43600e59046af39862f0c"     # ldapimage default headshot


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
    if is_njit and hashlib.md5(data).hexdigest() == _NJIT_PLACEHOLDER_MD5:
        return None
    return data


def ensure_photo(slug: str, scholar_photo_url: str, name: str, out_dir: str) -> str:
    """Resolve + save the faculty photo; return a relative ref or a monogram sentinel.

    Order: cached jpg -> Scholar (non-silhouette) -> NJIT ldapimage -> monogram.
    """
    photos_dir = os.path.join(out_dir, "photos")
    os.makedirs(photos_dir, exist_ok=True)
    dest = os.path.join(photos_dir, f"{slug}.jpg")
    ref = f"../assets/photos/{slug}.jpg"

    if os.path.exists(dest):                      # cached -> zero network (idempotent)
        return ref

    data = _try(scholar_photo_url, is_njit=False) or _try(njit_photo_url(slug), is_njit=True)
    if data:
        with open(dest, "wb") as fh:
            fh.write(data)
        return ref
    return f"monogram:{initials(name)}"
