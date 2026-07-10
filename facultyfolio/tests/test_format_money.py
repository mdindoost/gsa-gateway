from facultyfolio import format as F


def test_date_long_and_month_year():
    assert F.date_long("2026-07-10") == "Jul 10, 2026"
    assert F.month_year("2026-07-10") == "Jul 2026"
