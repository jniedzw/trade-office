"""Jack on WhatsApp: the commands a tradesperson sends, and Jack's replies.

main.py receives each message and hands it to whatsapp_reply here.
"""

from typing import NamedTuple

import ai
import export
import settings
from ai import change_quote, read_booking, read_job, read_quote
from alerts import alert_james
from database import (
    ask_question,
    book_job,
    booked_jobs,
    count_jobs_today,
    count_quotes_today,
    delete_price,
    finish_quote,
    get_business_details,
    get_client_by_owner,
    get_invoice_for_quote,
    get_job,
    get_prices,
    get_quote,
    get_quote_lines,
    mark_paid,
    now_utc,
    pop_question,
    save_invoice,
    save_job,
    save_quote,
    set_markup,
    set_price,
    set_whatsapp_alerts,
    unpaid_invoices,
    update_quote,
)
from diary import uk_now, week_ahead, when
from menu import HELP_TEXT, MENU_TEXT, command_for_title
from quotes import QUOTE_HELP, money, parse_job, parse_quote, signed_link, to_pence
from sms import send_whatsapp, send_whatsapp_buttons

# Far more quotes than a tradesperson makes in a day, so only a fault or misuse hits it.
MAX_QUOTES_PER_DAY = 50

WHATSAPP_NOT_A_CLIENT = "Hi, this is Ask Jack, admin for UK tradespeople. See askjack.uk or email hello@askjack.uk."


class Reply(NamedTuple):
    """What Jack sends back: words, maybe a PDF link, and maybe buttons."""

    text: str
    link: str | None = None
    # A button template's ID, and what fills its gaps besides the words in {{1}}.
    buttons: tuple[str, dict] | None = None


def buttons_for(template, number):
    """Return buttons for a quote or job, or None if that template isn't set up."""
    return (template, {"2": str(number)}) if template else None


def menu_reply(client):
    """Return the menu, or plain help if the menu template isn't set up."""
    if not settings.WHATSAPP_MENU:
        return Reply(HELP_TEXT)
    return Reply(MENU_TEXT, None, (settings.WHATSAPP_MENU, {}))


def quote_reply(client, details):
    """Save a draft quote and return our reply and a link to its PDF (or None).

    With no details, as when the tradesperson taps New quote, Jack asks about the job.
    """
    if details.strip() == "":
        # 0 because there's no quote yet for the question to be about.
        ask_question(client.id, "new_quote", 0)
        return Reply("Tell me about the job: who it's for, the work, and the price.")
    # "QUOTE JOB 4", as sent by the Quote it button under a logged job.
    words = details.split()
    if len(words) == 2 and words[0].upper() == "JOB" and words[1].isdigit():
        return quote_job_reply(client, int(words[1]))
    # A cap, so a fault or misuse can't run up a big AI bill.
    if count_quotes_today(client.id) >= MAX_QUOTES_PER_DAY:
        alert_james(client, "hit the daily quote limit.")
        return Reply(f"That's {MAX_QUOTES_PER_DAY} quotes today, the daily limit. Try again tomorrow.")
    # The exact typed format first: it's free and can't be misread. Then the AI.
    quote = parse_quote(details) or read_quote(
        details, prices=get_prices(client.id), markup_percent=client.markup_percent
    )
    if quote is None:
        return Reply(QUOTE_HELP)
    return draft_reply(save_quote(client.id, **quote), quote)


def quote_job_reply(client, job_id):
    """Ask for the price and work for a logged job, so it can be quoted."""
    job = get_job(job_id)
    if job is None or job.client_id != client.id:
        return Reply(f"There's no job {job_id} for {client.business_name}.")
    ask_question(client.id, "quote_job", job.id)
    return Reply(f"What's the price for {job.customer_name}'s job ({job.work}), and the work to list?")


def quote_from_job(client, job_id, answer):
    """Make a draft quote for a logged job from the tradesperson's answer, and return a Reply."""
    if count_quotes_today(client.id) >= MAX_QUOTES_PER_DAY:
        return Reply(f"That's {MAX_QUOTES_PER_DAY} quotes today, the daily limit. Try again tomorrow.")
    job = get_job(job_id)
    # Start with the job's details, so the tradesperson only has to add the price and the work.
    # Only his answer counts for the price, not numbers in the job like "3 radiators".
    quote = read_quote(
        f"For {job.customer_name}: {job.work}. {answer}",
        typed=answer,
        prices=get_prices(client.id),
        markup_percent=client.markup_percent,
    )
    if quote is None:
        return Reply("Sorry, I couldn't make that quote. Make sure it has a price, like: 450, fit new radiator")
    # The customer is the job's customer, whatever the AI read.
    quote["customer_name"] = job.customer_name
    return draft_reply(save_quote(client.id, **quote, job_id=job.id), quote)


def draft_reply(quote_id, quote):
    """Return the reply for a newly saved draft quote, with its PDF and buttons."""
    count = len(quote["lines"])
    lines = "1 line" if count == 1 else f"{count} lines"
    text = (
        f"Draft quote {quote_id} saved: {quote['title']} for "
        f"{quote['customer_name']}, {money(quote['total_pence'])}, {lines} of work. "
        f"Check it, then send FINAL {quote_id} for the version to send."
    )
    return with_pdf(text, "quote", quote_id, buttons_for(settings.WHATSAPP_DRAFT_BUTTONS, quote_id))


def with_pdf(text, kind, item_id, buttons=None):
    """Return our reply with a link to the PDF, if links can work."""
    # Without the secret no link would work, so send the words on their own.
    if not settings.QUOTE_LINK_SECRET:
        return Reply(text, None, buttons)
    return Reply(text, signed_link(kind, item_id, now_utc()), buttons)


def final_reply(client, number):
    """Make a quote final and return our reply and a link to the clean PDF."""
    if not number.isdigit():
        return Reply("To finish a quote, send FINAL and its number, like FINAL 3.")
    quote = finish_quote(client.id, int(number))
    if quote is None:
        return Reply(f"There's no quote {number} for {client.business_name}.")
    return with_pdf(
        f"Quote {quote.id} is final. Forward this PDF to {quote.customer_name}.",
        "quote",
        quote.id,
        buttons_for(settings.WHATSAPP_FINAL_BUTTONS, quote.id),
    )


INVOICE_HELP = "To make an invoice, send INVOICE, the quote number, then the customer's address:\nINVOICE 3\n12 Elm Road\nLiverpool L1 0AA"


def invoice_reply(client, details):
    """Make an invoice from a final quote and return our reply.

    With no address, as when the tradesperson taps Make invoice, Jack asks for one.
    """
    first_line, *address_lines = details.strip().splitlines() or [""]
    number = first_line.strip()
    address = "\n".join(line.strip() for line in address_lines if line.strip())
    if not number.isdigit():
        return Reply(INVOICE_HELP)

    quote = get_quote(int(number))
    if quote is None or quote.client_id != client.id:
        return Reply(f"There's no quote {number} for {client.business_name}.")
    if quote.status != "final":
        return Reply(f"Quote {number} is still a draft. Send FINAL {number} first.")
    # Bank details come from here, and an invoice without them can't be paid.
    if get_business_details(client.id) is None:
        alert_james(client, "tried to invoice without business details.")
        return Reply("Your invoice details aren't set up yet. We've been told and will sort it.")
    # A quote from a logged job already knows the address, so Jack needn't ask.
    job = get_job(quote.job_id) if quote.job_id else None
    if address == "" and job is not None and job.address:
        address = job.address
    if address == "" and get_invoice_for_quote(quote.id) is None:
        ask_question(client.id, "invoice_address", quote.id)
        return Reply(
            f"What's {quote.customer_name}'s address? "
            "Send it in one message, with each part on a new line."
        )

    # Sending INVOICE twice for one quote gives the same invoice, not a second one.
    invoice = get_invoice_for_quote(quote.id) or save_invoice(client.id, quote.id, address)
    text = (
        f"Invoice {invoice.number} saved for {quote.customer_name}, "
        f"{money(quote.total_pence)}. Forward this PDF to them."
    )
    return with_pdf(text, "invoice", invoice.id)


# The first words of every command, as lists so they compare with split()[:1].
def is_new_command(command):
    """Return True if a message is clearly a new command, not an answer to Jack.

    Answers often start with words like "change" or "price" ("change the price
    to 1300"), so only messages that can't be an answer count as commands.
    """
    words = command.split()
    if command in ("ALERTS ON", "ALERTS OFF", "PRICES", "HELP", "OWED", "PAID", "WEEK", "EXPORT"):
        return True
    if words[:1] in (["QUOTE"], ["LOG"], ["BOOK"]):
        return True
    # FINAL 6, INVOICE 6 and CHANGE 6 need a quote number straight after.
    return words[:1] in (["FINAL"], ["INVOICE"], ["CHANGE"], ["PAID"]) and words[1:2] != [] and words[1].isdigit()

PRICE_HELP = (
    "To save a price, send PRICE, the job and the price, like:\nPRICE boiler service 85\n"
    "Rates work the same way, like PRICE day rate 250\n"
    "To remove one: PRICE boiler service DELETE\nTo see them all: PRICES"
)


def price_reply(client, details):
    """Save, change or delete one of the client's standard prices."""
    name, _, last_word = details.strip().rpartition(" ")
    if name.strip() == "":
        return Reply(PRICE_HELP)
    if last_word.upper() == "DELETE":
        if delete_price(client.id, name):
            return Reply(f"Deleted the price for {name.strip().lower()}.")
        return Reply(f"There's no price for {name.strip().lower()}. Send PRICES to see them.")
    pence = to_pence(last_word)
    if pence is None:
        return Reply(PRICE_HELP)
    set_price(client.id, name, pence)
    return Reply(f"Saved: {name.strip().lower()}, {money(pence)}.")


def prices_reply(client):
    """List the client's standard prices."""
    prices = get_prices(client.id)
    if not prices:
        return Reply("You haven't saved any prices yet.\n" + PRICE_HELP)
    lines = [f"{name}: {money(pence)}" for name, pence in prices]
    lines.append(f"Materials markup: {client.markup_percent}%")
    return Reply("Your prices:\n" + "\n".join(lines))


def markup_reply(client, details):
    """Save the percentage the client adds to materials."""
    percent = details.strip().removesuffix("%").strip()
    if not percent.isdigit() or int(percent) > 100:
        return Reply("To set your materials markup, send MARKUP and a percentage, like MARKUP 15.")
    set_markup(client.id, int(percent))
    return Reply(f"Saved: {percent}% added to materials on your quotes.")


CHANGE_HELP = "To change a quote, send CHANGE, its number and what to change, like:\nCHANGE 3 price is 1300"


def change_reply(client, details):
    """Change a quote using what the tradesperson wrote, and return a new draft Reply.

    With nothing to change, as when the tradesperson taps Change, Jack asks what to change.
    """
    number, _, change = details.strip().partition(" ")
    if not number.isdigit():
        return Reply(CHANGE_HELP)
    quote = get_quote(int(number))
    if quote is None or quote.client_id != client.id:
        return Reply(f"There's no quote {number} for {client.business_name}.")
    # The invoice is made from the quote, so changing it would change the invoice.
    if get_invoice_for_quote(quote.id) is not None:
        return Reply(f"Quote {number} has been invoiced, so it can't change. Send a new QUOTE instead.")
    if change.strip() == "":
        ask_question(client.id, "change", quote.id)
        return Reply(f"What needs changing on quote {number}?")

    changed = change_quote(quote, get_quote_lines(quote.id), change)
    if changed is None:
        return Reply(
            f"Sorry, I couldn't make that change. If it's a new price, include it, "
            f"like: CHANGE {number} price is 1300"
        )
    update_quote(quote.id, **changed)
    count = len(changed["lines"])
    lines = "1 line" if count == 1 else f"{count} lines"
    text = (
        f"Draft quote {quote.id} changed: {changed['title']} for {changed['customer_name']}, "
        f"{money(changed['total_pence'])}, {lines} of work."
    )
    return with_pdf(text, "quote", quote.id, buttons_for(settings.WHATSAPP_DRAFT_BUTTONS, quote.id))


def answer_question(client, question, answer):
    """Handle the reply to a question Jack asked, and return our Reply."""
    asked, quote_id = question
    if asked == "invoice_address":
        return invoice_reply(client, f"{quote_id}\n{answer}")
    if asked == "change":
        return change_reply(client, f"{quote_id} {answer}")
    if asked == "new_quote":
        return quote_reply(client, answer)
    if asked == "new_job":
        return log_reply(client, answer)
    if asked == "quote_job":
        return quote_from_job(client, quote_id, answer)
    if asked == "mark_paid":
        return paid_reply(client, answer)
    if asked == "book_job":
        return book_from_answer(client, quote_id, answer)
    if asked == "new_booking":
        return book_reply(client, answer)
    return Reply("Sorry, I lost track of that. Please start again.")


LOG_HELP = (
    "To log a job, send LOG and the job in your own words, like:\n"
    "LOG Mrs Jones, 12 Elm Road, 07700 900123, leaking radiator in the kitchen"
)


def too_many_jobs(client):
    """Return True, and tell James, if the client has hit the daily job limit."""
    # The same cap as quotes, since reading a job uses the AI too.
    if count_jobs_today(client.id) >= MAX_QUOTES_PER_DAY:
        alert_james(client, "hit the daily job limit.")
        return True
    return False


def log_reply(client, details):
    """Save a new job from what the client wrote, and return our Reply.

    With no details, as when the tradesperson taps Log a job, Jack asks about the job.
    """
    if details.strip() == "":
        ask_question(client.id, "new_job", 0)
        return Reply("Tell me about the job: who it's for, their address and number if you have them, and the work.")
    if too_many_jobs(client):
        return Reply(f"That's {MAX_QUOTES_PER_DAY} jobs today, the daily limit. Try again tomorrow.")
    job = parse_job(details) or read_job(details)
    if job is None:
        return Reply(LOG_HELP)
    job_id = save_job(client.id, **job)
    lines = [f"Job {job_id} logged: {job['customer_name']}, {job['work']}."]
    if job.get("address"):
        lines.append("Address: " + job["address"].replace("\n", ", "))
    if job.get("customer_phone"):
        lines.append(f"Phone: {job['customer_phone']}")
    return Reply("\n".join(lines), None, buttons_for(settings.WHATSAPP_JOB_BUTTONS, job_id))


def owed_reply(client):
    """List who owes the client money, oldest first, with how overdue each is."""
    invoices = unpaid_invoices(client.id)
    if not invoices:
        return Reply("Nobody owes you anything. Nice one.")
    details = get_business_details(client.id)
    days_to_pay = details.payment_days if details else 14
    lines = []
    for invoice in invoices:
        overdue = (now_utc() - invoice["time"]).days - days_to_pay
        late = f", {overdue} days overdue" if overdue > 0 else ""
        lines.append(
            f"Invoice {invoice['number']}: {invoice['customer_name']}, "
            f"{money(invoice['total_pence'])}{late}"
        )
    total = sum(invoice["total_pence"] for invoice in invoices)
    return Reply("Who owes you:\n" + "\n".join(lines) + f"\nTotal: {money(total)}")


def paid_reply(client, details):
    """Mark an invoice as paid. With no number, list what's owed and ask which."""
    number = details.strip().removeprefix("INVOICE").removeprefix("invoice").strip()
    if number == "":
        owed = owed_reply(client)
        if not unpaid_invoices(client.id):
            return owed
        ask_question(client.id, "mark_paid", 0)
        return Reply(owed.text + "\n\nWhich invoice has been paid? Send its number.")
    if not number.isdigit():
        return Reply("To mark an invoice as paid, send PAID and its number, like PAID 3.")
    result = mark_paid(client.id, int(number))
    if result is None:
        return Reply(f"There's no invoice {number} for {client.business_name}.")
    if result == "already paid":
        return Reply(f"Invoice {number} was already marked as paid.")
    return Reply(f"Invoice {number} marked as paid.")


BOOK_HELP = "To book a job, send BOOK, the job and when, like:\nBOOK Mrs Jones, leaking radiator, Monday 9am"


def book_reply(client, details):
    """Book a logged job in ("BOOK JOB 4"), or log and book a new one in one go."""
    words = details.split()
    if len(words) == 2 and words[0].upper() == "JOB" and words[1].isdigit():
        job = get_job(int(words[1]))
        if job is None or job.client_id != client.id:
            return Reply(f"There's no job {words[1]} for {client.business_name}.")
        ask_question(client.id, "book_job", job.id)
        return Reply(f"When's {job.customer_name}'s job? Like: Monday 9am, or Mon to Fri for a longer job.")
    if details.strip() == "":
        ask_question(client.id, "new_booking", 0)
        return Reply("Tell me the job and when: who it's for, the work, and the day and time.")

    # A new job and its time in one message, like "Mrs Jones, leaking rad, Monday 9am".
    if too_many_jobs(client):
        return Reply(f"That's {MAX_QUOTES_PER_DAY} jobs today, the daily limit. Try again tomorrow.")
    job = read_job(details)
    if job is None:
        return Reply(BOOK_HELP)
    return book_from_answer(client, save_job(client.id, **job), details)


def book_from_answer(client, job_id, answer):
    """Book a job for the time in the tradesperson's answer, or ask again if it's unclear."""
    job = get_job(job_id)
    booking = read_booking(answer, uk_now(now_utc()))
    if booking is None:
        ask_question(client.id, "book_job", job.id)
        return Reply(f"When's {job.customer_name}'s job? I couldn't tell from that. Like: Monday 9am.")
    starts_at, ends_on = booking
    book_job(job.id, starts_at, ends_on)
    return Reply(f"Booked: {job.customer_name}, {job.work}, {when(starts_at, ends_on)}.")


def week_reply(client):
    """Return the client's booked jobs for the next 7 days."""
    today = uk_now(now_utc()).date()
    week = week_ahead(booked_jobs(client.id, now_utc()), today)
    return Reply(f"Your week:\n{week}" if week else "Nothing booked for the next 7 days.")


def export_reply(client):
    """Send the client all their data as a spreadsheet."""
    if not settings.QUOTE_LINK_SECRET:
        alert_james(client, "couldn't export: QUOTE_LINK_SECRET isn't set.")
        return Reply("Sorry, exports aren't working right now. We've been told.")
    link = signed_link("export", client.id, now_utc(), lasts=export.LINK_LASTS, extension="xlsx")
    return Reply("Here's all your data: jobs, quotes, invoices and prices.", link)


def alerts_reply(client, details):
    """Turn WhatsApp alerts on or off."""
    if details.strip().upper() == "ON":
        set_whatsapp_alerts(client.id, True)
        return Reply("Done. Missed-call alerts will come here. Send ALERTS OFF for texts.")
    if details.strip().upper() == "OFF":
        set_whatsapp_alerts(client.id, False)
        return Reply("Done. Missed-call alerts will come by text.")
    return Reply("Send ALERTS ON for alerts here, or ALERTS OFF for texts.")


# Every command, by its first word. Each handler gets the client and the rest of the message.
COMMANDS = {
    "LOG": lambda client, rest: log_reply(client, rest),
    "QUOTE": lambda client, rest: quote_reply(client, rest),
    "CHANGE": lambda client, rest: change_reply(client, rest),
    "FINAL": lambda client, rest: final_reply(client, rest.strip()),
    "INVOICE": lambda client, rest: invoice_reply(client, rest),
    "PRICE": lambda client, rest: price_reply(client, rest),
    "PRICES": lambda client, rest: prices_reply(client),
    "MARKUP": lambda client, rest: markup_reply(client, rest),
    "HELP": lambda client, rest: Reply(HELP_TEXT),
    "ALERTS": lambda client, rest: alerts_reply(client, rest),
    "PAID": lambda client, rest: paid_reply(client, rest),
    "OWED": lambda client, rest: owed_reply(client),
    "BOOK": lambda client, rest: book_reply(client, rest),
    "WEEK": lambda client, rest: week_reply(client),
    "EXPORT": lambda client, rest: export_reply(client),
}


def whatsapp_reply(sender, body=""):
    """Handle a WhatsApp message and return our Reply."""
    # Twilio writes WhatsApp numbers as "whatsapp:+447...", so take that off first.
    client = get_client_by_owner(sender.removeprefix("whatsapp:"))
    if client is None or not client.active:
        return Reply(WHATSAPP_NOT_A_CLIENT)
    # Any AI calls for this message are logged against this client.
    ai.current_client_id.set(client.id)

    # A menu choice may arrive as its title, like "New quote", so use its command.
    body = command_for_title(body) or body.strip()
    command = body.upper()
    # Jack's question only gets an answer if this isn't a new command.
    question = pop_question(client.id)
    if question is not None and not is_new_command(command):
        return answer_question(client, question, body)

    # The first word picks the command, so it can be followed by a space or a new line.
    first_word = command.split()[0] if command else ""
    if first_word in COMMANDS:
        return COMMANDS[first_word](client, body[len(first_word):])
    return menu_reply(client)


def send_with_buttons(to, reply):
    """Send the PDF, then the words with buttons.

    Return None if it all went, or what's left to send as a plain reply.
    """
    # Buttons can't go in a TwiML reply, so both are sent through Twilio's API,
    # the PDF first so the buttons arrive underneath it.
    template, gaps = reply.buttons
    pdf_sent = False
    try:
        if reply.link:
            send_whatsapp(to, media_url=reply.link)
            pdf_sent = True
        send_whatsapp_buttons(to, template, {"1": reply.text, **gaps})
        return None
    except Exception as error:
        # A plain reply is better than none, but don't send the PDF twice.
        print(f"Buttons failed: {type(error).__name__}", flush=True)
        return reply._replace(link=None) if pdf_sent else reply
