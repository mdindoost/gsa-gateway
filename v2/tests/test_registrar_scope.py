from pathlib import Path

from v2.core.ingestion import registrar_crawl as rc

FIX = Path(__file__).parent / "fixtures" / "registrar"
SEED = "https://www.njit.edu/registrar/"


def test_in_scope_is_path_prefix_bound():
    sp = "/registrar/"
    assert rc._in_scope(sp, "/registrar/withdrawal")
    assert rc._in_scope(sp, "/registrar/directory/mallstaff.php")
    assert rc._in_scope(sp, "/registrar/node/455")
    assert not rc._in_scope(sp, "/bursar/")              # off-path same host
    assert not rc._in_scope(sp, "/graduatestudies/")


def test_real_homepage_reaches_staff_and_stays_in_scope():
    home = (FIX / "home.html").read_text(encoding="utf-8")
    staff = (FIX / "staff.html").read_text(encoding="utf-8")
    stub = '<html><body><div role="main"><h1>x</h1>body</div></body></html>'
    seen = []

    def fetch(u):
        seen.append(u)
        if u == SEED:
            return home
        if u.endswith("mallstaff.php"):
            return staff
        return stub

    res = rc.extract_entry(SEED, fetch, max_depth=2, budget=40)
    # every fetched URL stayed under /registrar/
    assert all("/registrar" in u for u in seen)
    # the staff directory was reached and parsed into the 13-person roster
    assert len(res.staff) == 13
