import os
import hashlib
from facultyfolio import photos

SIL = "https://scholar.google.com/citations/images/avatar_scholar_128.png"
REAL_SCHOLAR = "https://scholar.googleusercontent.com/citations?view_op=view_photo&user=X"


def _dl(mapping):
    """Fake _download returning bytes keyed by a substring of the URL."""
    def f(url):
        for k, v in mapping.items():
            if k in url:
                return v
        raise AssertionError(f"unexpected fetch: {url}")
    return f


def _read(tmp_path, slug):
    return open(os.path.join(tmp_path, "photos", f"{slug}.jpg"), "rb").read()


def test_njit_url_is_deterministic():
    assert photos.njit_photo_url("oria") == "https://uws.njit.edu/ldapimage.php?format=full&uid=oria"


def test_njit_first(tmp_path, monkeypatch):
    # both sources have a real photo -> NJIT wins (new order)
    monkeypatch.setattr(photos, "_download", _dl({"ldapimage": b"NJIT", "scholar": b"SCHOLAR"}))
    ref = photos.ensure_photo("koutis", REAL_SCHOLAR, "Ioannis Koutis", str(tmp_path))
    assert ref == "assets/photos/koutis.jpg"       # root-relative; templates prepend page asset_root
    assert _read(tmp_path, "koutis") == b"NJIT"


def test_njit_placeholder_falls_to_scholar(tmp_path, monkeypatch):
    monkeypatch.setattr(photos, "_NJIT_PLACEHOLDER_MD5", hashlib.md5(b"NJITDEFAULT").hexdigest())
    monkeypatch.setattr(photos, "_download", _dl({"ldapimage": b"NJITDEFAULT", "scholar": b"SCHOLAR"}))
    ref = photos.ensure_photo("x", REAL_SCHOLAR, "X Y", str(tmp_path))
    assert _read(tmp_path, "x") == b"SCHOLAR"


def test_silhouette_and_placeholder_gives_monogram(tmp_path, monkeypatch):
    monkeypatch.setattr(photos, "_NJIT_PLACEHOLDER_MD5", hashlib.md5(b"NJITDEFAULT").hexdigest())
    monkeypatch.setattr(photos, "_download", _dl({"ldapimage": b"NJITDEFAULT"}))
    ref = photos.ensure_photo("calvin", SIL, "James Calvin", str(tmp_path))   # scholar = silhouette
    assert ref == "monogram:JC"
    assert not os.path.exists(os.path.join(tmp_path, "photos", "calvin.jpg"))


def test_cached_not_refetched(tmp_path, monkeypatch):
    os.makedirs(os.path.join(tmp_path, "photos"))
    open(os.path.join(tmp_path, "photos", "koutis.jpg"), "wb").write(b"cached")

    def boom(url):
        raise AssertionError("must not download when cached and un-overridden")
    monkeypatch.setattr(photos, "_download", boom)
    ref = photos.ensure_photo("koutis", REAL_SCHOLAR, "Ioannis Koutis", str(tmp_path))
    assert ref == "assets/photos/koutis.jpg"
    assert _read(tmp_path, "koutis") == b"cached"


def test_override_dropin_file_wins_over_cache(tmp_path, monkeypatch):
    man = tmp_path / "manual"; man.mkdir()
    (man / "koutis.png").write_bytes(b"MANUAL")
    monkeypatch.setattr(photos, "_MANUAL_DIR", str(man))
    out = tmp_path / "out"
    os.makedirs(out / "photos")
    (out / "photos" / "koutis.jpg").write_bytes(b"OLDCACHE")

    def boom(url):
        raise AssertionError("drop-in override needs no network")
    monkeypatch.setattr(photos, "_download", boom)
    photos.ensure_photo("koutis", REAL_SCHOLAR, "K", str(out))
    assert _read(out, "koutis") == b"MANUAL"


def test_override_config_scholar_forces_scholar(tmp_path, monkeypatch):
    monkeypatch.setattr(photos.config, "PHOTO_OVERRIDES", {"koutis": "scholar"})
    monkeypatch.setattr(photos, "_download", _dl({"ldapimage": b"NJIT", "scholar": b"SCHOLAR"}))
    photos.ensure_photo("koutis", REAL_SCHOLAR, "K", str(tmp_path))
    assert _read(tmp_path, "koutis") == b"SCHOLAR"      # forced Scholar even though NJIT has a photo


def test_override_config_url(tmp_path, monkeypatch):
    monkeypatch.setattr(photos.config, "PHOTO_OVERRIDES", {"x": "https://pics.example/x.jpg"})
    monkeypatch.setattr(photos, "_download", _dl({"pics.example/x.jpg": b"CUSTOM"}))
    photos.ensure_photo("x", None, "X", str(tmp_path))
    assert _read(tmp_path, "x") == b"CUSTOM"


def test_scholar_gray_avatar_md5_is_registered():
    # The real Scholar gray-avatar fingerprint must be in the placeholder set _try rejects.
    assert photos._SCHOLAR_AVATAR_MD5 == "31cb65bf3c565b39a5c4a575843028a4"


def test_scholar_gray_avatar_rejected_by_content(tmp_path, monkeypatch):
    # cliu's failure mode: NJIT serves its default headshot AND a real-photo Scholar URL (NOT the
    # avatar_scholar_128 silhouette form) nonetheless serves the generic gray avatar bytes. Both must
    # be rejected by CONTENT fingerprint -> monogram, no file written.
    njit_def = hashlib.md5(b"NJITDEFAULT").hexdigest()
    gray = hashlib.md5(b"GRAYAVATAR").hexdigest()
    monkeypatch.setattr(photos, "_NJIT_PLACEHOLDER_MD5", njit_def)
    monkeypatch.setattr(photos, "_SCHOLAR_AVATAR_MD5", gray)
    monkeypatch.setattr(photos, "_download", _dl({"ldapimage": b"NJITDEFAULT", "scholar": b"GRAYAVATAR"}))
    ref = photos.ensure_photo("cliu", REAL_SCHOLAR, "Chengjun Liu", str(tmp_path))
    assert ref == "monogram:CL"
    assert not os.path.exists(os.path.join(tmp_path, "photos", "cliu.jpg"))
