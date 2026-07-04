from facultyfolio import chart as C

KOUTIS = {"2007": 8, "2008": 13, "2009": 37, "2010": 62, "2011": 107, "2012": 97, "2013": 152,
          "2014": 157, "2015": 203, "2016": 169, "2017": 161, "2018": 161, "2019": 208, "2020": 163,
          "2021": 140, "2022": 151, "2023": 174, "2024": 194, "2025": 251, "2026": 152}


def test_peak_excludes_partial():
    svg = C.render_chart(KOUTIS, 2026)
    assert 'class="bar peak"' in svg and ">251<" in svg          # 2025 is the peak, labelled
    assert "2026: 152 (partial)" in svg                          # latest == sync -> partial
    assert 'viewBox="0 0 660 134"' in svg


def test_partial_only_when_latest_eq_sync():
    eska = {"2018": 2, "2019": 3, "2020": 5, "2021": 8}          # latest 2021, sync 2026
    svg = C.render_chart(eska, 2026)
    assert "(partial)" not in svg and 'class="bar peak"' in svg  # 2021 is a full, peak-eligible bar


def test_gate_min_years():
    assert C.render_chart({"2024": 1, "2025": 2}, 2026) is None   # <4 years


def test_gate_peak_zero():
    assert C.render_chart({"2020": 0, "2021": 0, "2022": 0, "2023": 0}, 2026) is None  # peak==0


def test_koutis_bar_width_matches_reference():
    # gap=4, N=20 -> width (660-4*19)/20 = 29.2, exactly the koutis reference
    svg = C.render_chart(KOUTIS, 2026)
    assert 'width="29.2"' in svg
