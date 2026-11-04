"""Small helpers for quotes and invoices: money, dates and references."""

from decimal import Decimal, InvalidOperation

QUOTE_HELP = "To make a quote, send: QUOTE Mrs Jones | Bathroom refit | BR | 1250.50"


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


def parse_quote(details):
    """Read "Mrs Jones | Bathroom refit | BR | 1250.50" into a dict, or None."""
    parts = [part.strip() for part in details.split("|")]
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
    }


def reference(prefix, job_code, day):
    """Return a quote reference like DPL-BR-011126.

    prefix is the business's letters, job_code the job's (BR for bathroom).
    """
    return f"{prefix}-{job_code}-{day.strftime('%d%m%y')}"
