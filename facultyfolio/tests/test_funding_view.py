import datetime
from facultyfolio import render

TODAY = datetime.date(2026, 7, 10)

WEI = {"funding": {
    "nsf": {"updated_at": "2026-07-10", "njit_total": 327808, "awards": [
        {"id": "1659472", "title": "REU Site: X", "start": "05/01/2017",
         "exp": "04/30/2022", "obligated": 327808, "at_njit": True}]},
    "nih": {"updated_at": "2026-07-10", "njit_total": 1653383, "projects": [
        {"core": "R35GM158529", "title": "Single-cell Omics", "total": 752500,
         "role": "contact", "fy_first": 2025, "fy_last": 2026, "appl_id": 11378084},
        {"core": "R15HG012087", "title": "Deep Learning", "total": 900883,
         "role": "contact", "fy_first": 2021, "fy_last": 2024, "appl_id": 10974530}]}}}


def test_groups_are_nsf_then_nih_no_dollars():
    v = render.funding_view(WEI, today=TODAY)
    assert [g["agency"] for g in v["groups"]] == ["NSF", "NIH"]
    blob = repr(v)
    assert "$" not in blob                      # no dollars anywhere in the view-model
    # rows carry no amount/unit/copi keys
    for g in v["groups"]:
        for r in g["rows"]:
            assert set(r.keys()) == {"title", "url", "meta", "years", "active"}


def test_count_summaries_with_pi_wording():
    v = render.funding_view(WEI, today=TODAY)
    nsf, nih = v["groups"]
    assert nsf["summary"] == "1 award · as Principal Investigator"
    assert nih["summary"] == "2 projects · as Contact PI"


def test_nih_recency_order_and_active():
    v = render.funding_view(WEI, today=TODAY)
    nih = v["groups"][1]
    assert nih["rows"][0]["years"] == "FY2025 – FY2026"   # fy_last 2026 first
    assert nih["rows"][0]["active"] is True                # fy_last 2026 >= FY2026
    assert nih["rows"][1]["active"] is False               # fy_last 2024
    assert v["groups"][0]["rows"][0]["active"] is False    # NSF exp 2022 < today


def test_links_and_meta_and_provenance():
    v = render.funding_view(WEI, today=TODAY)
    assert v["groups"][0]["rows"][0]["url"] == "https://www.nsf.gov/awardsearch/showAward?AWD_ID=1659472"
    assert v["groups"][0]["rows"][0]["meta"] == "NSF 1659472"
    assert v["groups"][1]["rows"][0]["url"] == "https://reporter.nih.gov/project-details/11378084"
    assert v["provenance"] == "From NSF and NIH public award records · as of Jul 10, 2026"


def test_nih_co_pi_dropped_entirely():
    f = {"funding": {"nih": {"updated_at": "2026-07-10", "njit_total": 0, "projects": [
        {"core": "U54X", "title": "Center", "total": 2400000, "role": "co_pi",
         "fy_first": 2023, "fy_last": 2028, "appl_id": 999}]}}}
    # only co-PI projects -> no NIH group at all -> None
    assert render.funding_view(f, today=TODAY) is None


def test_nih_contact_kept_copi_filtered_out():
    f = {"funding": {"nih": {"updated_at": "2026-07-10", "njit_total": 500000, "projects": [
        {"core": "R01A", "title": "Contact one", "total": 500000, "role": "contact",
         "fy_first": 2022, "fy_last": 2025, "appl_id": 1},
        {"core": "U54B", "title": "CoPI one", "total": 2400000, "role": "co_pi",
         "fy_first": 2023, "fy_last": 2028, "appl_id": 2}]}}}
    v = render.funding_view(f, today=TODAY)
    g = v["groups"][0]
    assert g["summary"] == "1 project · as Contact PI"
    assert [r["meta"] for r in g["rows"]] == ["NIH R01A"]     # co-PI row absent


def test_nsf_only_and_none_cases():
    nsf_only = {"funding": {"nsf": WEI["funding"]["nsf"]}}
    assert [g["agency"] for g in render.funding_view(nsf_only, today=TODAY)["groups"]] == ["NSF"]
    assert "From NSF public award records" in render.funding_view(nsf_only, today=TODAY)["provenance"]
    assert render.funding_view({"funding": {}}, today=TODAY) is None
    assert render.funding_view({}, today=TODAY) is None


def test_prior_institution_nsf_excluded():
    f = {"funding": {"nsf": {"updated_at": "2026-07-10", "njit_total": 0, "awards": [
        {"id": "1", "title": "Old", "start": "01/01/2010", "exp": "01/01/2014",
         "obligated": 500000, "at_njit": False}]}}}
    assert render.funding_view(f, today=TODAY) is None       # no at_njit rows -> None


def test_nsf_multi_award_recency_order():
    f = {"funding": {"nsf": {"updated_at": "2026-07-10", "njit_total": 3, "awards": [
        {"id": "old", "title": "Old", "start": "01/01/2012", "exp": "01/01/2016", "obligated": 1, "at_njit": True},
        {"id": "new", "title": "New", "start": "08/01/2021", "exp": "07/31/2027", "obligated": 2, "at_njit": True}]}}}
    rows = render.funding_view(f, today=TODAY)["groups"][0]["rows"]
    assert [r["meta"] for r in rows] == ["NSF new", "NSF old"]   # newer exp first
    assert rows[0]["active"] is True and rows[1]["active"] is False
