"""Small helpers for quotes and invoices: money, dates and references."""

from datetime import date


def money(pence):
    """Return whole pence as pounds, like 125050 -> £1,250.50."""
    # divmod gives the pounds and the pence left over, with no float rounding.
    pounds, pence = divmod(pence, 100)
    return f"£{pounds:,}.{pence:02}"


def uk_date(day):
    """Return a date like 1 November 2026."""
    # Not strftime("%d"), which would give "01 November".
    return f"{day.day} {day.strftime('%B %Y')}"


def reference(prefix, job_code, day):
    """Return a quote reference like DPL-BR-011126.

    prefix is the business's letters, job_code the job's (BR for bathroom).
    """
    return f"{prefix}-{job_code}-{day.strftime('%d%m%y')}"
