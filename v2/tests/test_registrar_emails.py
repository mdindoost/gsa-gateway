from pathlib import Path

from v2.core.ingestion import registrar_crawl as rc

FIX = Path(__file__).parent / "fixtures" / "registrar"
SEED = "https://www.njit.edu/registrar/"


def test_per_person_emails_captured_from_mailto_anchors():
    """The staff page exposes each person's email only in a mailto: href (clean_text strips it);
    extract_entry must recover them from the raw HTML and attach one per person, matched by name."""
    staff = (FIX / "staff.html").read_text(encoding="utf-8")
    home = ('<html><body><div role="main"><h1>Office of the Registrar</h1>welcome'
            '<a href="/registrar/directory/mallstaff.php">staff</a></div></body></html>')
    pages = {SEED: home, "https://www.njit.edu/registrar/directory/mallstaff.php": staff}

    res = rc.extract_entry(SEED, lambda u: pages.get(u), max_depth=2, budget=20)
    by_name = {s.name: s.email for s in res.staff}
    assert by_name["Jerry Trombella"] == "jerry.trombella@njit.edu"
    assert by_name["Diane McKeown"] == "mckeown@njit.edu"
    assert by_name["Niki Gardiner"] == "niki.gardiner@njit.edu"   # trailing space in href trimmed
    # all 13 got a non-empty personal njit.edu address
    assert len(res.staff) == 13
    assert all(e.endswith("@njit.edu") for e in by_name.values())


def test_function_mailbox_is_never_attached_to_a_person():
    """Anti-fab: a departmental function mailbox under a person-shaped anchor is not attached."""
    html = ('<html><body><div role="main">'
            '<a href="mailto:registrar@njit.edu">Trombella, Jerry</a>'
            '<a href="mailto:info@njit.edu">Help Desk</a>'
            '</div></body></html>')
    emails = rc._emails_from_html(html)
    assert emails == {}            # registrar@ is a function mailbox; "Help Desk" is not a name
