from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO

from openpyxl import load_workbook

from database import Invoice, Job, Quote
from export import make_export


def made_up_data():
    quote = Quote(id=3, client_id=1, customer_name="Mrs Jones", title="Bathroom refit", job_code="BR",
                  total_pence=125050, status="final", job_id=1,
                  time=datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc))
    invoice = Invoice(id=1, client_id=1, quote_id=3, number=1, customer_address="12 Test Road\nLiverpool",
                      time=datetime(2026, 11, 20, tzinfo=timezone.utc),
                      paid_at=datetime(2026, 11, 25, tzinfo=timezone.utc))
    job = Job(id=1, client_id=1, customer_name="Mrs Jones", work="Bathroom refit", status="paid",
              address="12 Test Road\nLiverpool", time=datetime(2026, 11, 1, tzinfo=timezone.utc),
              starts_at=datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc), ends_on=date(2026, 11, 13))
    return {"jobs": [job], "quotes": [quote], "invoices": [(invoice, quote)], "prices": [("day rate", 25000)]}


def open_export(data=None, markup=15):
    return load_workbook(BytesIO(make_export(data or made_up_data(), markup)))


def test_export_has_a_sheet_for_each_kind_of_data():
    assert open_export().sheetnames == ["Jobs", "Quotes", "Invoices", "Prices"]


def test_money_is_a_number_in_pounds_not_text():
    cell = open_export()["Quotes"]["F2"]

    # A real number, so the accountant can add up a column.
    assert cell.value == Decimal("1250.5")
    assert cell.number_format == "£#,##0.00"


def test_invoices_show_when_they_were_paid():
    sheet = open_export()["Invoices"]

    assert [cell.value for cell in sheet[1]] == ["Invoice", "Date", "Customer", "Address", "Quote", "Amount", "Paid on"]
    assert sheet["D2"].value == "12 Test Road, Liverpool"
    assert sheet["G2"].value == datetime(2026, 11, 25)


def test_times_are_in_uk_time():
    # 8am UTC on 9 November is 8am in the UK (GMT), and the Excel cell has no timezone.
    assert open_export()["Jobs"]["H2"].value == datetime(2026, 11, 9, 8, 0)


def test_prices_include_the_markup():
    sheet = open_export(markup=15)["Prices"]

    assert [[cell.value for cell in row] for row in sheet.iter_rows(min_row=2)] == [
        ["day rate", 250], ["Materials markup", "15%"]
    ]


def test_an_empty_export_still_works():
    data = {"jobs": [], "quotes": [], "invoices": [], "prices": []}

    assert open_export(data)["Jobs"].max_row == 1
