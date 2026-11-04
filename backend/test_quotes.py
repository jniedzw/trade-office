from datetime import date, datetime, timezone

from database import BusinessDetails, Quote
from quotes import make_quote_pdf, money, parse_quote, reference, to_pence, uk_date


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


def test_make_quote_pdf_returns_a_pdf():
    quote = Quote(
        id=1,
        client_id=1,
        customer_name="Smith & Sons",
        title="Boiler service",
        job_code="BS",
        total_pence=8500,
        time=datetime(2026, 11, 4, tzinfo=timezone.utc),
    )

    pdf = make_quote_pdf(quote, "Test Plumbing")

    # Every PDF file starts with these bytes.
    assert pdf.startswith(b"%PDF-")


def test_parse_quote_reads_the_scope_of_works():
    quote = parse_quote(
        "Mrs Jones | Bathroom refit | BR | 1250\n- Remove old suite\n\n• Fit new basin "
    )

    assert quote["lines"] == ["Remove old suite", "Fit new basin"]


def test_parse_quote_rejects_a_line_that_isnt_a_bullet():
    assert parse_quote("Mrs Jones | Bathroom | BR | 1250\nRemove old suite") is None
    assert parse_quote("Mrs Jones | Bathroom | BR | 1250\n- ") is None


def test_make_quote_pdf_with_a_scope_of_works():
    quote = Quote(
        id=1,
        client_id=1,
        customer_name="Mrs Jones",
        title="Bathroom refit",
        job_code="BR",
        total_pence=125050,
        time=datetime(2026, 11, 4, tzinfo=timezone.utc),
    )

    pdf = make_quote_pdf(quote, "Test Plumbing", ["Remove <old> suite", "Fit basin"])

    assert pdf.startswith(b"%PDF-")


def test_make_quote_pdf_with_business_details():
    quote = Quote(
        id=7,
        client_id=1,
        customer_name="Mrs Jones",
        title="Bathroom refit",
        job_code="BR",
        total_pence=125050,
        time=datetime(2026, 11, 4, tzinfo=timezone.utc),
    )
    details = BusinessDetails(
        client_id=1,
        legal_name="Test Person t/a Test & Co",
        address="1 Test Street\nLiverpool",
        ref_letters="TP",
        bank_details="Test Bank",
    )

    assert make_quote_pdf(quote, "Test & Co", ["Fit basin"], details).startswith(b"%PDF-")
