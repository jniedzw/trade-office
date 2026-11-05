"""Quotes: reading them from WhatsApp, money and dates, and signed PDF links."""

import hashlib
import hmac
from datetime import timedelta
from decimal import Decimal, InvalidOperation

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


def parse_job(details):
    """Read "Mrs Jones | 12 Elm Road, Liverpool | Leaking radiator" into a dict, or None.

    The address is optional, so "Mrs Jones | Leaking radiator" works too.
    """
    parts = [part.strip() for part in details.split("|")]
    if len(parts) not in (2, 3) or "" in parts:
        return None
    address = parts[1].replace(", ", "\n") if len(parts) == 3 else None
    return {"customer_name": parts[0], "work": parts[-1], "address": address}


def reference(prefix, job_code, day):
    """Return a quote reference like DPL-BR-011126.

    prefix is the business's letters, job_code the job's (BR for bathroom).
    """
    return f"{prefix}-{job_code}-{day.strftime('%d%m%y')}"


def link_signature(kind, item_id, expires):
    """Return a code that only someone with the secret can make for this link."""
    # kind is "quote" or "invoice", so a quote's code can't open invoice 1 as well.
    message = f"{kind}:{item_id}:{expires}".encode()
    secret = settings.QUOTE_LINK_SECRET.encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def signed_link(kind, item_id, now):
    """Return a private link to a quote's or invoice's PDF that stops working after LINK_DAYS."""
    expires = int((now + timedelta(days=LINK_DAYS)).timestamp())
    signature = link_signature(kind, item_id, expires)
    return f"{settings.PUBLIC_URL}/{kind}s/{item_id}.pdf?expires={expires}&sig={signature}"


def link_is_valid(kind, item_id, expires, signature, now):
    """Return True if we made this link for this item and it hasn't run out."""
    if not settings.QUOTE_LINK_SECRET or expires < now.timestamp():
        return False
    # compare_digest takes the same time whatever matches, so the code can't be guessed bit by bit.
    return hmac.compare_digest(signature, link_signature(kind, item_id, expires))
