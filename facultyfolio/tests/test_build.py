import os
import hashlib
from facultyfolio import build, photos


def test_build_koutis(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "photos_ensure", lambda *a, **k: "monogram:IK")
    path = build.build_one("ikoutis", str(tmp_path))
    assert os.path.exists(path)
    html = open(path).read()
    assert "Ioannis Koutis" in html


def test_build_all_and_leaderboard(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "photos_ensure", lambda slug, *a, **k: f"monogram:{slug[:2].upper()}")
    from facultyfolio import db
    res = build.build_all(str(tmp_path))
    assert res["count"] == len(db.cs_faculty_slugs())          # a page per home faculty (self-relative)
    assert os.path.exists(os.path.join(tmp_path, "cs", "index.html"))
    assert os.path.exists(os.path.join(tmp_path, "p", "ikoutis.html"))
    assert os.path.exists(os.path.join(tmp_path, "assets", "style.css"))


def test_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "photos_ensure", lambda slug, *a, **k: f"monogram:{slug[:2].upper()}")

    def digest():
        out = {}
        for sub in ("p", "cs"):
            d = os.path.join(tmp_path, sub)
            for f in sorted(os.listdir(d)):
                out[f] = hashlib.md5(open(os.path.join(d, f), "rb").read()).hexdigest()
        return out

    build.build_all(str(tmp_path))
    h1 = digest()
    build.build_all(str(tmp_path))
    h2 = digest()
    assert h1 == h2                    # byte-identical rebuild
