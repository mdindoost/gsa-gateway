"""Momentum estimator + gates + membership + per-person trend (pure, no DB)."""
from facultyfolio import momentum as M


# ---- real-data fixtures (from the live DB, 2026-07-08) ----
ZHIHAO = [4, 14, 30, 77, 139]        # +129%/yr, monotone -> rising, NOT tiny (139>=25)
CONG = [144, 218, 317, 396, 450]     # +34%/yr, big base, rising
RAZA = [32, 23, 49, 68, 110]         # one dip (32->23) but still rising (tolerated)
JASON = [442, 388, 360, 310, 275]    # genuine decline -> NOT rising
FLAT = [100, 100, 100, 100, 100]     # genuinely flat (median slope 0) -> NOT rising
SPIKE = [2, 2, 2, 2, 45]             # one-jump small base -> excluded by gate/membership


def test_window_series_excludes_sync_year_and_needs_all_five():
    cpy = {"2021": 4, "2022": 14, "2023": 30, "2024": 77, "2025": 139, "2026": 5}
    years, values = M.window_series(cpy, 2026)
    assert years == [2021, 2022, 2023, 2024, 2025]   # 2026 (sync/partial) excluded
    assert values == ZHIHAO


def test_window_series_missing_year_returns_none_no_zero_fill():
    cpy = {"2021": 4, "2023": 30, "2024": 77, "2025": 139}   # 2022 missing
    assert M.window_series(cpy, 2026) is None
    assert M.window_series({}, 2026) is None
    assert M.window_series({"2025": 5}, 0) is None            # no sync year


def test_theil_sen_exact_on_log1p_linear_series():
    # values chosen so log1p(values) = [0,1,2,3,4] exactly -> every pairwise slope is 1.0
    from math import exp
    vals = [exp(k) - 1 for k in range(5)]
    assert abs(M.theil_sen(vals) - 1.0) < 1e-9


def test_p25_nearest_rank_is_third_smallest_of_ten():
    s = list(range(10))                 # 0..9, already sorted
    assert M.p25_nearest_rank(s) == s[2]    # ceil(0.25*10)=3 -> index 2


def test_momentum_pct_real_values():
    assert M.momentum_pct(ZHIHAO) == 129
    assert M.momentum_pct(CONG) == 34


def test_data_gate():
    assert M.passes_data_gate(ZHIHAO) is True
    assert M.passes_data_gate([1, 2, 3, 4]) is False           # <5 years
    assert M.passes_data_gate([2, 3, 4, 5, 6]) is False         # median 4 < floor 10
    assert M.passes_data_gate(SPIKE) is False                   # median 2 < floor 10


def test_is_rising_membership():
    assert M.is_rising(ZHIHAO) is True
    assert M.is_rising(CONG) is True
    assert M.is_rising(RAZA) is True          # one dip tolerated
    assert M.is_rising(JASON) is False        # genuine decline
    assert M.is_rising(FLAT) is False         # flat


def test_tiny_base_and_recent_rate():
    assert M.recent_rate(ZHIHAO) == 139
    assert M.tiny_base(ZHIHAO) is False       # 139 >= 25
    assert M.tiny_base([1, 3, 8, 12, 20]) is True   # latest 20 < 25


def test_recent_trend_growing_with_number():
    t = M.recent_trend({str(2021 + i): v for i, v in enumerate(ZHIHAO)}, 2026)
    assert t == {"kind": "growing", "glyph": False, "pct": 129, "window": "2021–2025"}


def test_recent_trend_steady_for_decliner_never_says_declining():
    cpy = {str(2021 + i): v for i, v in enumerate(JASON)}
    assert M.recent_trend(cpy, 2026) == {"kind": "steady"}
    cpy_flat = {str(2021 + i): v for i, v in enumerate(FLAT)}
    assert M.recent_trend(cpy_flat, 2026) == {"kind": "steady"}


def test_recent_trend_none_below_gate():
    assert M.recent_trend({"2025": 5}, 2026) is None                       # <5 years
    assert M.recent_trend({str(2021 + i): 3 for i in range(5)}, 2026) is None  # median<floor


def test_recent_trend_tiny_base_uses_glyph_not_number():
    tiny = [1, 3, 8, 30, 20]        # median 8 <10 would fail gate; bump values
    tiny = [8, 12, 16, 22, 20]      # median 16 >=10 passes gate; latest 20 <25 tiny; rising-ish
    t = M.recent_trend({str(2021 + i): v for i, v in enumerate(tiny)}, 2026)
    assert t and t["kind"] == "growing" and t["glyph"] is True and "pct" not in t


def test_rising_view_sorts_and_counts():
    roster = [
        {"slug": "zhihao", "name": "Zhihao Yao", "title": "Assistant Professor",
         "citations": 331, "updated_at": "2026-07-07",
         "cites_per_year": {str(2021 + i): v for i, v in enumerate(ZHIHAO)}},
        {"slug": "cong", "name": "Cong Shi", "title": "Associate Professor",
         "citations": 1944, "updated_at": "2026-07-07",
         "cites_per_year": {str(2021 + i): v for i, v in enumerate(CONG)}},
        {"slug": "jason", "name": "Jason Wang", "title": "Professor",
         "citations": 11328, "updated_at": "2026-07-07",
         "cites_per_year": {str(2021 + i): v for i, v in enumerate(JASON)}},
        {"slug": "noscholar", "name": "No Scholar", "title": "Professor",
         "citations": None, "updated_at": None, "cites_per_year": None},
    ]
    rows, funnel = M.rising_view(roster)
    assert [r["slug"] for r in rows] == ["zhihao", "cong"]   # decliner excluded, sorted by %
    assert rows[0]["momentum_pct"] == 129 and rows[0]["recent_rate"] == 139
    assert funnel == {"risers": 2, "gated": 3, "scholar": 3, "total": 4}
