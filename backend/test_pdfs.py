from datetime import datetime, timezone

from database import BusinessDetails, Invoice, Quote
from pdfs import make_invoice_pdf, make_quote_pdf


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


def test_make_invoice_pdf():
    quote = Quote(
        id=7,
        client_id=1,
        customer_name="Mrs Jones",
        title="Bathroom refit",
        job_code="BR",
        total_pence=125050,
        status="final",
        time=datetime(2026, 11, 4, tzinfo=timezone.utc),
    )
    invoice = Invoice(
        id=1,
        client_id=1,
        quote_id=7,
        number=1,
        customer_address="12 Test Road\nLiverpool",
        time=datetime(2026, 11, 20, tzinfo=timezone.utc),
    )
    details = BusinessDetails(
        client_id=1,
        legal_name="Test Person t/a Test & Co",
        address="1 Test Street\nLiverpool",
        ref_letters="TP",
        bank_details="Test Bank\nSort code 00-00-00",
    )

    pdf = make_invoice_pdf(invoice, quote, "Test & Co", ["Fit basin"], details)

    assert pdf.startswith(b"%PDF-")
