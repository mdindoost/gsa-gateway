import datetime
from facultyfolio import render

TODAY = datetime.date(2026, 7, 10)   # deterministic

WEI = {"funding": {
    "nsf": {"updated_at": "2026-07-10", "njit_total": 327808, "awards": [
        {"id": "1659472", "title": "REU Site: X", "start": "05/01/2017",
         "exp": "04/30/2022", "obligated": 327808, "at_njit": True}]},
    "nih": {"updated_at": "2026-07-10", "njit_total": 1653383, "projects": [
        {"core": "R15HG012087", "title": "Deep Learning", "total": 900883,
         "role": "contact", "fy_first": 2021, "fy_last": 2024, "appl_id": 10974530},
        {"core": "R35GM158529", "title": "Single-cell Omics", "total": 752500,
         "role": "contact", "fy_first": 2025, "fy_last": 2026, "appl_id": 11378084}]}}}


def test_both_groups_present_and_ordered():
    v = render.funding_view(WEI, today=TODAY)
    assert [g["agency"] for g in v["groups"]] == ["NSF awards", "NIH projects"]
    nih = v["groups"][1]
    # recency-first: FY2025-2026 row before FY2021-2024
    assert nih["rows"][0]["years"] == "FY2025 – FY2026"
    assert nih["rows"][1]["years"] == "FY2021 – FY2024"


def test_summaries_and_units():
    v = render.funding_view(WEI, today=TODAY)
    nsf, nih = v["groups"]
    assert nsf["summary"] == "$327,808 obligated · 1 award"
    assert nih["summary"] == "$1,653,383 project costs · 2 projects (as contact PI)"
    assert nsf["rows"][0]["unit"] == "obligated"
    assert nih["rows"][0]["unit"] == "costs"


def test_active_chip_rules():
    v = render.funding_view(WEI, today=TODAY)
    nsf, nih = v["groups"]
    assert nsf["rows"][0]["active"] is False          # exp 2022 < today
    assert nih["rows"][0]["active"] is True            # fy_last 2026 >= FY2026
    assert nih["rows"][1]["active"] is False           # fy_last 2024


def test_links_and_provenance():
    v = render.funding_view(WEI, today=TODAY)
    assert v["groups"][0]["rows"][0]["url"] == "https://www.nsf.gov/awardsearch/showAward?AWD_ID=1659472"
    assert v["groups"][1]["rows"][0]["url"] == "https://reporter.nih.gov/project-details/11378084"
    assert v["provenance"] == "From NSF and NIH public award records · as of Jul 10, 2026"


def test_nsf_only_omits_nih_group():
    f = {"funding": {"nsf": WEI["funding"]["nsf"]}}
    v = render.funding_view(f, today=TODAY)
    assert [g["agency"] for g in v["groups"]] == ["NSF awards"]
    assert "NSF public award records" in v["provenance"]


def test_no_funding_returns_none():
    assert render.funding_view({"funding": {}}, today=TODAY) is None
    assert render.funding_view({}, today=TODAY) is None


def test_prior_institution_nsf_excluded():
    f = {"funding": {"nsf": {"updated_at": "2026-07-10", "njit_total": 0, "awards": [
        {"id": "1", "title": "Old", "start": "01/01/2010", "exp": "01/01/2014",
         "obligated": 500000, "at_njit": False}]}}}
    assert render.funding_view(f, today=TODAY) is None    # no at_njit rows -> no group -> None


def test_copi_only_summary_variant():
    f = {"funding": {"nih": {"updated_at": "2026-07-10", "njit_total": 0, "projects": [
        {"core": "U54X", "title": "Center", "total": 2400000, "role": "co_pi",
         "fy_first": 2023, "fy_last": 2028, "appl_id": 999}]}}}
    v = render.funding_view(f, today=TODAY)
    g = v["groups"][0]
    assert g["summary"] == "co-investigator on 1 project"
    assert g["rows"][0]["copi"] is True


def test_dollar_formatting_compact_and_exact():
    f = {"funding": {"nsf": {"updated_at": "2026-07-10", "njit_total": 9076163, "awards": [
        {"id": "a", "title": "T", "start": "08/01/2021", "exp": "07/31/2027",
         "obligated": 4078362, "at_njit": True}]}}}
    row = render.funding_view(f, today=TODAY)["groups"][0]["rows"][0]
    assert row["amount"] == "$4.08M"
    assert row["active"] is True                        # exp 2027 >= today


def test_nih_appl_id_missing_yields_null_url():
    f = {"funding": {"nih": {"updated_at": "2026-07-10", "njit_total": 900883, "projects": [
        {"core": "R15HG012087", "title": "Deep Learning", "total": 900883,
         "role": "contact", "fy_first": 2021, "fy_last": 2024}]}}}     # no appl_id key
    row = render.funding_view(f, today=TODAY)["groups"][0]["rows"][0]
    assert row["url"] is None

    f2 = {"funding": {"nih": {"updated_at": "2026-07-10", "njit_total": 900883, "projects": [
        {"core": "R15HG012087", "title": "Deep Learning", "total": 900883,
         "role": "contact", "fy_first": 2021, "fy_last": 2024, "appl_id": None}]}}}   # explicit None
    row2 = render.funding_view(f2, today=TODAY)["groups"][0]["rows"][0]
    assert row2["url"] is None


def test_nih_mixed_contact_and_copi_ordering_and_units():
    f = {"funding": {"nih": {"updated_at": "2026-07-10", "njit_total": 900883, "projects": [
        {"core": "U54X", "title": "Center", "total": 2400000, "role": "co_pi",
         "fy_first": 2023, "fy_last": 2028, "appl_id": 999},
        {"core": "R15HG012087", "title": "Deep Learning", "total": 900883,
         "role": "contact", "fy_first": 2021, "fy_last": 2024, "appl_id": 10974530},
    ]}}}
    g = render.funding_view(f, today=TODAY)["groups"][0]
    # contact row(s) before co-PI row(s)
    assert g["rows"][0]["copi"] is False
    assert g["rows"][0]["unit"] == "costs"
    assert g["rows"][1]["copi"] is True
    assert g["rows"][1]["unit"] == "project"
    # contact-variant summary; co-PI excluded from njit_total
    assert g["summary"] == "$900,883 project costs · 1 project (as contact PI)"


def test_nsf_multi_award_sorted_by_exp_then_obligated():
    f = {"funding": {"nsf": {"updated_at": "2026-07-10", "njit_total": 600000, "awards": [
        {"id": "A", "title": "Oldest", "start": "01/01/2010", "exp": "01/01/2025",
         "obligated": 100000, "at_njit": True},
        {"id": "B", "title": "Newest-lower", "start": "01/01/2020", "exp": "01/01/2027",
         "obligated": 200000, "at_njit": True},
        {"id": "C", "title": "Newest-higher", "start": "01/01/2021", "exp": "01/01/2027",
         "obligated": 300000, "at_njit": True},
    ]}}}
    rows = render.funding_view(f, today=TODAY)["groups"][0]["rows"]
    # newest exp first; among ties on exp, higher obligated first
    assert [r["meta"] for r in rows] == ["NSF C", "NSF B", "NSF A"]
