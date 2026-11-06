"""A client's data as an Excel spreadsheet: for their accountant, or to take with them."""

from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font

from diary import to_uk
from quotes import invoice_label, job_label, quote_label

# How long the download link works. Twilio fetches the file straight away to send it.
LINK_LASTS = timedelta(hours=1)

# The type browsers and WhatsApp use for Excel files.
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

MONEY = "£#,##0.00"
DAY = "DD/MM/YYYY"
DAY_AND_TIME = "DD/MM/YYYY HH:MM"


def pounds(pence):
    """Return pence as pounds, exactly, for a spreadsheet cell."""
    return Decimal(pence) / 100


def uk_day(moment):
    """Return a database time as a UK date with no timezone, which Excel needs."""
    return to_uk(moment).replace(tzinfo=None) if moment else None


def add_sheet(book, title, headings, rows, formats=None):
    """Add a sheet with bold headings that stay on screen, and one row per record.

    formats maps a column number (from 0) to an Excel number format, like MONEY.
    """
    sheet = book.create_sheet(title)
    sheet.append(headings)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"
    for row in rows:
        sheet.append(row)
    for column, number_format in (formats or {}).items():
        for cell in list(sheet.iter_cols(min_col=column + 1, max_col=column + 1, min_row=2))[0]:
            cell.number_format = number_format
    # Wide enough for the longest entry in each column, within reason.
    for cells in sheet.columns:
        longest = max(len(str(cell.value or "")) for cell in cells)
        sheet.column_dimensions[cells[0].column_letter].width = min(max(longest + 2, 10), 50)


def make_export(data, markup_percent):
    """Return the spreadsheet as bytes, from everything_for() in database.py."""
    book = Workbook()
    # Workbook() starts with an empty sheet we don't need.
    book.remove(book.active)

    # Quotes point at jobs by their database ID; show the job's own number instead.
    job_numbers = {job.id: job.number for job in data["jobs"]}
    jobs = []
    for job in data["jobs"]:
        booked = uk_day(job.starts_at)
        jobs.append([
            job_label(job.number), uk_day(job.time), job.customer_name, job.customer_phone or "",
            (job.address or "").replace("\n", ", "), job.work, job.status,
            booked, job.ends_on,
        ])
    add_sheet(
        book, "Jobs",
        ["Job", "Logged", "Customer", "Phone", "Address", "Work", "Status", "Booked for", "Last day"],
        jobs, {1: DAY, 7: DAY_AND_TIME, 8: DAY},
    )

    quotes = [
        [quote_label(quote.number), uk_day(quote.time), quote.customer_name, quote.title, quote.status,
         pounds(quote.total_pence), job_label(job_numbers[quote.job_id]) if quote.job_id in job_numbers else ""]
        for quote in data["quotes"]
    ]
    add_sheet(book, "Quotes", ["Quote", "Date", "Customer", "Work", "Status", "Total", "Job"], quotes, {1: DAY, 5: MONEY})

    invoices = [
        [invoice_label(invoice.number), uk_day(invoice.time), quote.customer_name,
         invoice.customer_address.replace("\n", ", "), quote_label(quote.number),
         pounds(quote.total_pence), uk_day(invoice.paid_at)]
        for invoice, quote in data["invoices"]
    ]
    add_sheet(
        book, "Invoices", ["Invoice", "Date", "Customer", "Address", "Quote", "Amount", "Paid on"],
        invoices, {1: DAY, 5: MONEY, 6: DAY},
    )

    prices = [[name, pounds(pence)] for name, pence in data["prices"]]
    prices.append(["Materials markup", f"{markup_percent}%"])
    add_sheet(book, "Prices", ["Item", "Price"], prices, {1: MONEY})

    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()

