from facultyfolio import format as F


def test_money_exact_under_1m():
    assert F.money(327808) == "$327,808"


def test_money_compact_at_and_over_1m():
    assert F.money(4078362) == "$4.08M"
    assert F.money(1653383) == "$1.65M"
    assert F.money(37401075) == "$37.40M"


def test_money_exact_always_commas():
    assert F.money_exact(1653383) == "$1,653,383"
    assert F.money_exact(0) == "$0"


def test_money_none_is_zero():
    assert F.money(None) == "$0"


def test_date_long_and_month_year():
    assert F.date_long("2026-07-10") == "Jul 10, 2026"
    assert F.month_year("2026-07-10") == "Jul 2026"
