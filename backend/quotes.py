"""Quotes: reading them from WhatsApp, and turning them into PDFs."""

import hashlib
import hmac
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

import settings

QUOTE_HELP = (
    "To make a quote, send:\n"
    "QUOTE Mrs Jones | Bathroom refit | BR | 1250.50\n"
    "- Remove old suite\n"
    "- Fit new basin and toilet"
)
# Phones often turn "-" into a bullet, so take either.
BULLETS = ("-", "•")

# How long a quote PDF link works for.
LINK_DAYS = 7

BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=11, leading=16)
GREY = ParagraphStyle("grey", parent=BODY, textColor=colors.HexColor("#666666"))
BOLD = ParagraphStyle("bold", parent=BODY, fontName="Helvetica-Bold")
BIG = ParagraphStyle("big", parent=BODY, fontName="Helvetica-Bold", fontSize=18, leading=24)


def money(pence):
    """Return whole pence as pounds, like 125050 -> £1,250.50."""
    # divmod gives the pounds and the pence left over, with no float rounding.
    pounds, pence = divmod(pence, 100)
    return f"£{pounds:,}.{pence:02}"


def uk_date(day):
    """Return a date like 1 November 2026."""
    # Not strftime("%d"), which would give "01 November".
    return f"{day.day} {day.strftime('%B %Y')}"


def to_pence(price):
    """Turn a price like "£1,250.50" into 125050, or None if it isn't one."""
    # Decimal, not float, so "1250.50" is exactly 1250.50.
    try:
        pounds = Decimal(price.replace("£", "").replace(",", "").strip())
    except InvalidOperation:
        return None
    # Rule out 0, negatives, "nan" and fractions of a penny like 10.999.
    if not pounds.is_finite() or pounds <= 0 or pounds != round(pounds, 2):
        return None
    return int(pounds * 100)


def parse_scope(lines):
    """Return the text of lines like "- Fit new basin", or None if one isn't a bullet."""
    scope = []
    for line in lines:
        line = line.strip()
        if line == "":
            continue
        if not line.startswith(BULLETS) or line[1:].strip() == "":
            return None
        scope.append(line[1:].strip())
    return scope


def parse_quote(details):
    """Read "Mrs Jones | Bathroom refit | BR | 1250.50" and any "- " lines
    under it into a dict, or None if it isn't written that way."""
    first_line, *other_lines = details.strip().splitlines() or [""]
    scope = parse_scope(other_lines)
    if scope is None:
        return None
    parts = [part.strip() for part in first_line.split("|")]
    if len(parts) != 4 or "" in parts:
        return None
    customer_name, title, job_code, price = parts
    # Job codes go in references, so keep them to a few plain letters.
    if not (job_code.isalpha() and job_code.isascii() and len(job_code) <= 4):
        return None
    total_pence = to_pence(price)
    if total_pence is None:
        return None
    return {
        "customer_name": customer_name,
        "title": title,
        "job_code": job_code.upper(),
        "total_pence": total_pence,
        "lines": scope,
    }


def reference(prefix, job_code, day):
    """Return a quote reference like DPL-BR-011126.

    prefix is the business's letters, job_code the job's (BR for bathroom).
    """
    return f"{prefix}-{job_code}-{day.strftime('%d%m%y')}"


def make_quote_pdf(quote, business_name, lines=()):
    """Return the quote as a PDF, as bytes ready to send."""
    # Built in memory, not saved: the server's disk is no place for customer files.
    buffer = BytesIO()
    heading = "DRAFT QUOTATION" if quote.status == "draft" else "QUOTATION"
    # The quote's ID makes the reference unique until clients have their own letters.
    ref = reference(f"Q{quote.id}", quote.job_code, quote.time)

    # escape() stops a name like "Smith & Sons" breaking reportlab's markup.
    content = [
        Paragraph(escape(business_name), BIG),
        Paragraph(f"{heading}, {uk_date(quote.time)}, Ref: {ref}", GREY),
        Spacer(1, 10 * mm),
        Paragraph(f"For: {escape(quote.customer_name)}", BODY),
        Paragraph(f"Work: {escape(quote.title)}", BODY),
        Spacer(1, 6 * mm),
    ]
    if lines:
        content.append(Paragraph("Scope of works", BOLD))
        content += [Paragraph(f"• {escape(line)}", BODY) for line in lines]
        content.append(Spacer(1, 6 * mm))
    content.append(Paragraph(f"Total: {money(quote.total_pence)}", BIG))
    document = SimpleDocTemplate(buffer, pagesize=A4, title=f"Quote {ref}")
    document.build(content)
    return buffer.getvalue()


def link_signature(quote_id, expires):
    """Return a code that only someone with the secret can make for this link."""
    message = f"{quote_id}:{expires}".encode()
    secret = settings.QUOTE_LINK_SECRET.encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def quote_link(quote_id, now):
    """Return a private link to the quote's PDF that stops working after LINK_DAYS."""
    expires = int((now + timedelta(days=LINK_DAYS)).timestamp())
    signature = link_signature(quote_id, expires)
    return f"{settings.PUBLIC_URL}/quotes/{quote_id}.pdf?expires={expires}&sig={signature}"


def link_is_valid(quote_id, expires, signature, now):
    """Return True if the link was made by us for this quote and hasn't run out."""
    if not settings.QUOTE_LINK_SECRET or expires < now.timestamp():
        return False
    # compare_digest takes the same time whatever matches, so the code can't be guessed bit by bit.
    return hmac.compare_digest(signature, link_signature(quote_id, expires))
