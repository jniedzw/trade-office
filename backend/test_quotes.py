from datetime import date

from quotes import (
    invoice_label,
    job_label,
    money,
    parse_job,
    parse_quote,
    quote_label,
    read_number,
    reference,
    to_pence,
    uk_date,
)


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


def test_parse_quote_reads_all_four_parts():
    assert parse_quote(" Mrs Jones | Bathroom refit | br | £1,250.50 ") == {
        "customer_name": "Mrs Jones",
        "title": "Bathroom refit",
        "job_code": "BR",
        "total_pence": 125050,
        "lines": [],
    }


def test_parse_quote_needs_four_parts():
    assert parse_quote("Mrs Jones | Bathroom refit | 1250") is None
    assert parse_quote("Mrs Jones | Bathroom refit | BR | 1250 | extra") is None


def test_parse_quote_rejects_empty_parts():
    assert parse_quote("Mrs Jones |  | BR | 1250") is None


def test_parse_quote_rejects_bad_job_codes():
    assert parse_quote("Mrs Jones | Bathroom | B-R | 1250") is None
    assert parse_quote("Mrs Jones | Bathroom | BATHROOM | 1250") is None


def test_to_pence():
    assert to_pence("85") == 8500
    assert to_pence("0.1") == 10


def test_to_pence_rejects_bad_prices():
    for price in ["abc", "0", "-50", "10.999", "nan", "inf", ""]:
        assert to_pence(price) is None, price


def test_parse_quote_reads_the_scope_of_works():
    quote = parse_quote(
        "Mrs Jones | Bathroom refit | BR | 1250\n- Remove old suite\n\n• Fit new basin "
    )

    assert quote["lines"] == ["Remove old suite", "Fit new basin"]


def test_parse_quote_rejects_a_line_that_isnt_a_bullet():
    assert parse_quote("Mrs Jones | Bathroom | BR | 1250\nRemove old suite") is None
    assert parse_quote("Mrs Jones | Bathroom | BR | 1250\n- ") is None


def test_parse_job_with_and_without_an_address():
    assert parse_job("Mrs Jones | 12 Test Road, Liverpool | Leaking radiator") == {
        "customer_name": "Mrs Jones",
        "work": "Leaking radiator",
        "address": "12 Test Road\nLiverpool",
    }
    assert parse_job("Mrs Jones | Leaking radiator")["address"] is None
    assert parse_job("leaking radiator for mrs jones") is None
    assert parse_job("Mrs Jones |  | Leaking radiator") is None


def test_labels():
    assert (job_label(4), quote_label(12), invoice_label(244)) == ("J4", "Q12", "INV-0244")


def test_read_number_takes_every_way_of_writing_it():
    for text in ["12", "Q12", "q12", "#12", "Q 12", "q-12"]:
        assert read_number(text, ("Q",)) == 12, text
    for text in ["244", "INV-0244", "inv 244", "INVOICE 244", "#0244"]:
        assert read_number(text, ("INVOICE", "INV")) == 244, text


def test_read_number_needs_digits():
    assert read_number("mrs jones", ("Q",)) is None
    assert read_number("Q", ("Q",)) is None


def test_a_job_needs_its_letter():
    assert read_number("J4", ("JOB", "J"), need_prefix=True) == 4
    assert read_number("JOB 4", ("JOB", "J"), need_prefix=True) == 4
    assert read_number("4", ("JOB", "J"), need_prefix=True) is None
