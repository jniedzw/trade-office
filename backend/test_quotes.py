from datetime import date

from quotes import money, reference, uk_date


def test_money_adds_pound_sign_and_pence():
    assert money(8500) == "£85.00"


def test_money_adds_commas():
    assert money(125050) == "£1,250.50"


def test_money_pads_small_pence():
    assert money(105) == "£1.05"


def test_uk_date_has_no_leading_zero():
    assert uk_date(date(2026, 11, 1)) == "1 November 2026"


def test_reference_uses_prefix_code_and_date():
    assert reference("DPL", "BR", date(2026, 11, 1)) == "DPL-BR-011126"
