"""Reading a tradesperson's rough WhatsApp message into a quote, using Claude."""

import json
import re

import anthropic
from pydantic import BaseModel

import settings
from quotes import to_pence

AI_MODEL = "claude-sonnet-5-5"

INSTRUCTIONS = """You read WhatsApp messages from a UK tradesperson who wants to quote a customer.
Pick out:
- customer_name: who the quote is for, as they wrote it.
- title: a short name for the job, like "Bathroom refit".
- job_code: 2 to 4 capital letters for the job, like BR for bathroom refit.
- price: the total price as a number only, with no £ sign, commas or words,
  like "1250" or "1250.50". It must be a number they wrote.
  Never work out, round or guess a price. Use null if there isn't one, if it's a
  range like "500-600", if they say they're not sure, or if they add extras with
  no price, like "plus parts".
- lines: each piece of work they mention, as short lines in their order.
If there's no customer name, use an empty string."""


CHANGE_INSTRUCTIONS = """You update a UK tradesperson's draft quote.
You get the quote as JSON, then the change they want, in their own words.
Answer with the whole quote after the change, in the same fields:
- Keep everything they didn't ask to change exactly as it was.
- price: a number only, like "1250" or "1250.50". Keep the old price unless they
  give a new one. Never work out, round or guess a price: if they want a change
  that needs a new price but don't give one, keep the old price.
- lines: the full list of work after the change, in order."""


JOB_INSTRUCTIONS = """You read WhatsApp messages from a UK tradesperson logging a new job.
Pick out:
- customer_name: who the job is for, as they wrote it. Empty string if there isn't one.
- customer_phone: the customer's phone number exactly as written, or null.
- address: the job's address as written, with each part on a new line, or null.
- work: a short description of the work, like "Leaking radiator in the kitchen".
Never make up a phone number or address: use null if they didn't write one."""


class JobDraft(BaseModel):
    """The shape Claude must answer in when reading a new job."""

    customer_name: str
    customer_phone: str | None
    address: str | None
    work: str


class QuoteDraft(BaseModel):
    """The shape Claude must answer in."""

    customer_name: str
    title: str
    job_code: str
    price: str | None
    lines: list[str]


def prices_in(message):
    """Return every amount written in the message, in pence."""
    # A digit, then any more digits or commas, then maybe a decimal point and pence.
    amounts = re.findall(r"\d[\d,]*(?:\.\d+)?", message)
    return {to_pence(amount) for amount in amounts} - {None}


def ask_claude(message, system=INSTRUCTIONS, shape=None):
    """Send the message to Claude and return its answer in shape, or None.

    shape is the Pydantic class to answer in: a QuoteDraft unless we say otherwise.
    """
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=30)
    response = client.beta.messages.parse(
        model=AI_MODEL,
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": message}],
        output_format=shape or QuoteDraft,
        # Picking out details is simple work, so Claude needn't think hard.
        output_config={"effort": "low"},
        # If Claude declines, another model tries instead of us getting nothing.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    # parsed_output is None if Claude refused or stopped early.
    return response.parsed_output


def safely_ask_claude(message, system=INSTRUCTIONS, shape=None):
    """Return Claude's answer, or None if there's no key or the AI failed."""
    if not settings.ANTHROPIC_API_KEY:
        return None
    try:
        return ask_claude(message, system, shape)
    except anthropic.APIError as error:
        # Only the error's type: the message could include the customer's details.
        print(f"AI quote failed: {type(error).__name__}", flush=True)
        return None


def read_quote(message):
    """Turn a rough message into the same dict parse_quote makes, or None."""
    # The price must be one the tradesperson actually wrote, so the AI can't invent one.
    return tidy(safely_ask_claude(message), prices_in(message))


def change_quote(quote, lines, change):
    """Apply a change in plain words to a saved quote. Return the new dict, or None."""
    current = {
        "customer_name": quote.customer_name,
        "title": quote.title,
        "job_code": quote.job_code,
        "price": f"{quote.total_pence / 100:.2f}",
        "lines": lines,
    }
    draft = safely_ask_claude(f"{json.dumps(current)}\nChange: {change}", CHANGE_INSTRUCTIONS)
    # The price can stay as it was, or be one they wrote in the change. Nothing else.
    return tidy(draft, prices_in(change) | {quote.total_pence})


def tidy(draft, allowed_prices):
    """Check Claude's QuoteDraft and return it as a quote dict, or None."""
    if draft is None or draft.price is None or draft.customer_name.strip() == "":
        return None

    total_pence = to_pence(draft.price)
    if total_pence is None or total_pence not in allowed_prices:
        return None

    job_code = draft.job_code.strip().upper()[:4]
    if not (job_code.isalpha() and job_code.isascii()):
        job_code = "JOB"

    return {
        "customer_name": draft.customer_name.strip(),
        "title": draft.title.strip() or "Work",
        "job_code": job_code,
        "total_pence": total_pence,
        "lines": [line.strip() for line in draft.lines if line.strip()],
    }


def uk_mobile(number, message):
    """Return number as +447..., or None if it isn't a UK mobile written in message."""
    digits = "".join(c for c in number if c.isdigit())
    # The digits must be in what they typed, so the AI can't make a number up.
    if digits == "" or digits not in "".join(c for c in message if c.isdigit()):
        return None
    if digits.startswith("07") and len(digits) == 11:
        return "+44" + digits[1:]
    if digits.startswith("447") and len(digits) == 12:
        return "+" + digits
    return None


def read_job(message):
    """Turn a rough message about a new job into a dict for save_job, or None."""
    draft = safely_ask_claude(message, JOB_INSTRUCTIONS, JobDraft)
    if draft is None or draft.customer_name.strip() == "" or draft.work.strip() == "":
        return None
    address = (draft.address or "").strip()
    return {
        "customer_name": draft.customer_name.strip(),
        "work": draft.work.strip(),
        "customer_phone": uk_mobile(draft.customer_phone or "", message),
        # An address must be one they wrote, at least in part, not one the AI made up.
        "address": address if address and address.split()[0].lower() in message.lower() else None,
    }
