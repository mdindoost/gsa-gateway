from v2.core.database.schema import create_all
from v2.core.retrieval.office_intent import resolve_office_slug, resolve_office_org_id


def test_procedural_queries_resolve_to_office():
    assert resolve_office_slug("who handles a registration hold?") == "registrar"
    assert resolve_office_slug("how do I get a refund on my tuition") == "bursar"
    assert resolve_office_slug("I need help with my I-20 and OPT") == "ogi"
    assert resolve_office_slug("where do I apply for a scholarship") == "financialaid"


def test_non_procedural_query_returns_none():
    assert resolve_office_slug("tell me about the MTSM management school") is None
    assert resolve_office_slug("who is professor Oria") is None


def test_longest_cue_wins_on_overlap():
    # "career fair" (career-development) should win over a bare generic token
    assert resolve_office_slug("when is the career fair") == "career-development"


def test_resolve_org_id_maps_to_live_office(tmp_path):
    conn = create_all(str(tmp_path / "t.db"))
    conn.execute("INSERT INTO organizations(id,slug,name,type) VALUES (24,'registrar','Reg','office')")
    assert resolve_office_org_id("registration hold help", conn) == 24
    # office not present in this DB -> None (no crash)
    assert resolve_office_org_id("my tuition refund", conn) is None
    # non-procedural -> None
    assert resolve_office_org_id("who is professor Oria", conn) is None
