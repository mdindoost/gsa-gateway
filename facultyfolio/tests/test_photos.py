import os
import hashlib
from facultyfolio import photos

SIL = "https://scholar.google.com/citations/images/avatar_scholar_128.png"
REAL_SCHOLAR = "https://scholar.googleusercontent.com/citations?view_op=view_photo&user=X"
PLACEHOLDER = b"NJIT_DEFAULT_BYTES"
_PLACEHOLDER_MD5 = hashlib.md5(PLACEHOLDER).hexdigest()


def test_njit_url_is_deterministic():
    assert photos.njit_photo_url("oria") == "https://uws.njit.edu/ldapimage.php?format=full&uid=oria"


def test_scholar_first(tmp_path, monkeypatch):
    monkeypatch.setattr(photos, "_download", lambda url: b"REALJPEGBYTES")
    ref = photos.ensure_photo("koutis", REAL_SCHOLAR, "Ioannis Koutis", str(tmp_path))
    assert ref == "../assets/photos/koutis.jpg"
    assert os.path.exists(os.path.join(tmp_path, "photos", "koutis.jpg"))


def test_silhouette_falls_to_njit(tmp_path, monkeypatch):
    # scholar is the silhouette -> skipped; njit returns a real (non-placeholder) photo
    monkeypatch.setattr(photos, "_download", lambda url: b"NJIT_REAL_PHOTO")
    ref = photos.ensure_photo("oria", SIL, "Vincent Oria", str(tmp_path))
    assert ref == "../assets/photos/oria.jpg"
    assert os.path.exists(os.path.join(tmp_path, "photos", "oria.jpg"))


def test_njit_placeholder_gives_monogram(tmp_path, monkeypatch):
    monkeypatch.setattr(photos, "_NJIT_PLACEHOLDER_MD5", _PLACEHOLDER_MD5)
    monkeypatch.setattr(photos, "_download", lambda url: PLACEHOLDER)   # njit returns default
    ref = photos.ensure_photo("calvin", SIL, "James Calvin", str(tmp_path))
    assert ref == "monogram:JC"
    assert not os.path.exists(os.path.join(tmp_path, "photos", "calvin.jpg"))


def test_cached_not_refetched(tmp_path, monkeypatch):
    photos_dir = os.path.join(tmp_path, "photos")
    os.makedirs(photos_dir)
    open(os.path.join(photos_dir, "koutis.jpg"), "wb").write(b"cached")

    def boom(url):
        raise AssertionError("must not download when cached")
    monkeypatch.setattr(photos, "_download", boom)
    ref = photos.ensure_photo("koutis", REAL_SCHOLAR, "Ioannis Koutis", str(tmp_path))
    assert ref == "../assets/photos/koutis.jpg"
