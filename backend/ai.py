"""Reading a tradesperson's rough WhatsApp message into a quote, using Claude."""

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


def ask_claude(message):
    """Send the message to Claude and return a QuoteDraft, or None."""
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=30)
    response = client.beta.messages.parse(
        model=AI_MODEL,
        max_tokens=2000,
        system=INSTRUCTIONS,
        messages=[{"role": "user", "content": message}],
        output_format=QuoteDraft,
        # Picking out details is simple work, so Claude needn't think hard.
        output_config={"effort": "low"},
        # If Claude declines, another model tries instead of us getting nothing.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    # parsed_output is None if Claude refused or stopped early.
    return response.parsed_output


def read_quote(message):
    """Turn a rough message into the same dict parse_quote makes, or None."""
    if not settings.ANTHROPIC_API_KEY:
        return None
    try:
        draft = ask_claude(message)
    except anthropic.APIError as error:
        # Only the error's type: the message could include the customer's details.
        print(f"AI quote failed: {type(error).__name__}", flush=True)
        return None
    if draft is None or draft.price is None or draft.customer_name.strip() == "":
        return None

    # The price must be one the tradesperson actually wrote, so the AI can't invent one.
    total_pence = to_pence(draft.price)
    if total_pence is None or total_pence not in prices_in(message):
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
