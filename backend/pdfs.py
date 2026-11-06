"""Quote PDFs in the style of a smart trade quote: dark header, one accent colour."""

from datetime import timedelta
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from quotes import invoice_label, money, reference, uk_date

DARK = colors.HexColor("#1a1a1a")
GREY = colors.HexColor("#666666")
# Used when a client hasn't chosen their own colour.
DEFAULT_ACCENT = "#b8975a"
PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 20 * mm
HEADER_HEIGHT = 42 * mm

BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=15, textColor=DARK)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=9, leading=13, textColor=GREY)
TITLE = ParagraphStyle("title", parent=BODY, fontName="Helvetica-Bold", fontSize=15, leading=20)
BOX_TEXT = ParagraphStyle("box", parent=BODY, fontSize=9, textColor=colors.white)
BOX_TOTAL = ParagraphStyle(
    "total", parent=BODY, fontName="Helvetica-Bold", fontSize=20, leading=24,
    textColor=colors.white, alignment=2,  # 2 means right-aligned
)


def lines_of(text):
    """Return text with its new lines kept, safe for reportlab."""
    # escape() stops a name like "Smith & Sons" breaking reportlab's markup.
    return escape(text).replace("\n", "<br/>")


def draw_page(canvas, doc, page):
    """Draw the header, the footer and, on drafts, a DRAFT mark."""
    top = PAGE_HEIGHT - HEADER_HEIGHT
    right = PAGE_WIDTH - MARGIN
    canvas.saveState()

    canvas.setFillColor(DARK)
    canvas.rect(0, top, PAGE_WIDTH, HEADER_HEIGHT, stroke=0, fill=1)
    canvas.setFillColor(page["accent"])
    canvas.setFont("Helvetica-Bold", 22)
    canvas.drawString(MARGIN, top + 22 * mm, page["business_name"])
    canvas.setFont("Helvetica", 8)
    canvas.drawString(MARGIN, top + 14 * mm, page["legal_name"])

    canvas.setFont("Helvetica-Bold", 12)
    canvas.drawRightString(right, top + 25 * mm, page["heading"])
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica", 8.5)
    for number, line in enumerate(page["lines"]):
        canvas.drawRightString(right, top + (18 - number * 5) * mm, line)

    canvas.setStrokeColor(page["accent"])
    canvas.line(MARGIN, 18 * mm, right, 18 * mm)
    canvas.setFillColor(GREY)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(MARGIN, 13 * mm, page["business_name"])
    canvas.drawRightString(right, 13 * mm, f"Page {doc.page}")

    if page["draft"]:
        # Big, pale and tilted, so a draft can't be mistaken for the real thing.
        canvas.setFillColor(colors.HexColor("#e6e6e6"))
        canvas.setFont("Helvetica-Bold", 90)
        canvas.translate(PAGE_WIDTH / 2, PAGE_HEIGHT / 2 - 30 * mm)
        canvas.rotate(35)
        canvas.drawCentredString(0, 0, "DRAFT")
    canvas.restoreState()


def from_text(details):
    """Return who a document is from: name, address and, if there is one, contact."""
    text = f"{details.legal_name}\n{details.address}"
    return f"{text}\n{details.contact}" if details.contact else text


def section(heading, items, accent, style=BODY):
    """Return a heading in the accent colour with a paragraph for each item."""
    heading_style = ParagraphStyle("section", parent=BODY, fontName="Helvetica-Bold", textColor=accent)
    parts = [Paragraph(heading, heading_style), Spacer(1, 2 * mm)]
    for item in items:
        parts += [Paragraph(lines_of(item), style), Spacer(1, 1.5 * mm)]
    parts.append(Spacer(1, 3 * mm))
    return parts


def total_box(label, pence, note, accent):
    """Return the dark box that shows the total."""
    width = PAGE_WIDTH - 2 * MARGIN
    label_style = ParagraphStyle("label", parent=BODY, fontName="Helvetica-Bold", fontSize=11, textColor=accent)
    box = Table(
        [[Paragraph(label, label_style), Paragraph(money(pence), BOX_TOTAL)],
         [Paragraph(escape(note), BOX_TEXT), ""]],
        colWidths=[width * 0.65, width * 0.35],
    )
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), DARK),
        ("SPAN", (1, 0), (1, 1)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8 * mm),
        ("TOPPADDING", (0, 0), (-1, 0), 5 * mm),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 5 * mm),
    ]))
    return box


def make_quote_pdf(quote, business_name, lines=(), details=None):
    """Return the quote as a PDF, as bytes ready to send.

    details is the client's BusinessDetails, or None if they haven't been added.
    """
    accent = colors.HexColor(details.brand_colour if details else DEFAULT_ACCENT)
    ref = quote_reference(quote, details.ref_letters if details else "Q")
    page = {
        "accent": accent,
        "business_name": business_name,
        "legal_name": details.legal_name if details else "",
        "heading": "QUOTATION",
        "lines": [uk_date(quote.time), f"Ref: {ref}"],
        "draft": quote.status == "draft",
    }

    content = [Paragraph(escape(quote.title.upper()), TITLE), Spacer(1, 2 * mm)]
    content += [Paragraph(f"For {escape(quote.customer_name)}", SMALL), Spacer(1, 6 * mm)]
    if lines:
        content += section("SCOPE OF WORKS", lines, accent)
    content += [total_box("TOTAL QUOTATION", quote.total_pence, "", accent), Spacer(1, 7 * mm)]
    if details:
        content += section("FROM", [from_text(details)], accent, SMALL)
        days = details.payment_days
        content += section("PAYMENT", [f"Payment due within {days} days of the invoice."], accent, SMALL)

    return build(f"Quote {ref}", page, content)


def build(title, page, content):
    """Return the PDF made from content, with page drawn on every page."""
    # Built in memory, not saved: the server's disk is no place for customer files.
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4, title=title,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=HEADER_HEIGHT + 10 * mm, bottomMargin=24 * mm,
    )
    # reportlab calls draw_page for every page, so long documents keep the header.
    document.build(
        content,
        onFirstPage=lambda canvas, doc: draw_page(canvas, doc, page),
        onLaterPages=lambda canvas, doc: draw_page(canvas, doc, page),
    )
    return buffer.getvalue()


def quote_reference(quote, letters):
    """Return a quote's reference, like DPL-BR-041126-7."""
    # The quote's number (Q7 is 7) on the end keeps two jobs on the same day apart.
    return f"{reference(letters, quote.job_code, quote.time)}-{quote.number or quote.id}"


def make_invoice_pdf(invoice, quote, business_name, lines, details):
    """Return the invoice for a final quote as a PDF, as bytes ready to send.

    Unlike a quote, an invoice always needs details, for the bank details.
    """
    accent = colors.HexColor(details.brand_colour)
    # The same label Jack uses on WhatsApp, so the tradesperson and customer see one number.
    number = invoice_label(invoice.number)
    due = uk_date(invoice.time + timedelta(days=details.payment_days))
    page = {
        "accent": accent,
        "business_name": business_name,
        "legal_name": details.legal_name,
        "heading": "INVOICE",
        "lines": [uk_date(invoice.time), f"No: {number}", f"Due: {due}"],
        "draft": False,
    }

    content = [Paragraph(escape(quote.title.upper()), TITLE), Spacer(1, 4 * mm)]
    content += section("INVOICE TO", [f"{quote.customer_name}\n{invoice.customer_address}"], accent)
    content += section("FROM", [from_text(details)], accent)
    # Invoices must show when the work was done. For now we assume it was finished
    # the day it's invoiced, which is when tradespeople usually send them.
    content += section("WORK CARRIED OUT", lines or [quote.title], accent)
    content += [Paragraph(f"Date of work: {uk_date(invoice.time)}", SMALL), Spacer(1, 4 * mm)]
    content += [total_box("AMOUNT DUE", quote.total_pence, "", accent), Spacer(1, 7 * mm)]
    payment = [
        f"Please pay by {due}.",
        details.bank_details,
        f"Please use {number} as the payment reference.",
        f"Quote ref: {quote_reference(quote, details.ref_letters)}",
    ]
    content += section("PAYMENT", payment, accent, SMALL)
    return build(f"Invoice {number}", page, content)
