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
    get_job_by_number,
    get_quote_by_number,
    quotes_for_customer,
    same_customer,
    create_login_link,
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
from quotes import (
    QUOTE_HELP,
    invoice_label,
    job_label,
    money,
    parse_job,
    parse_quote,
    quote_label,
    read_number,
    signed_link,
    to_pence,
)
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
    # "QUOTE J4", or "QUOTE JOB 4" as sent by the Quote it button under a logged job.
    job_number = read_number(details, ("JOB", "J"), need_prefix=True)
    if job_number is not None:
        return quote_job_reply(client, job_number)
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


def quote_job_reply(client, number):
    """Ask for the price and work for a logged job, so it can be quoted."""
    job = get_job_by_number(client.id, number)
    if job is None:
        return Reply(f"There's no job {job_label(number)} for {client.business_name}.")
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
    number = get_quote(quote_id).number
    count = len(quote["lines"])
    lines = "1 line" if count == 1 else f"{count} lines"
    text = (
        f"Draft quote {quote_label(number)} saved: {quote['title']} for "
        f"{quote['customer_name']}, {money(quote['total_pence'])}, {lines} of work. "
        f"Check it, then send FINAL {quote_label(number)} for the version to send."
    )
    return with_pdf(text, "quote", quote_id, buttons_for(settings.WHATSAPP_DRAFT_BUTTONS, number))


def find_quote(client, ref, status):
    """Find a client's quote from what they typed: a number like Q12, or a customer's name.

    Return (quote, None), or (None, a Reply saying what's wrong), or (None, None) if
    they typed nothing. A name only matches quotes with this status, like "final".
    """
    ref = ref.strip()
    if ref == "":
        return None, None
    number = read_number(ref, ("Q",))
    if number is not None:
        quote = get_quote_by_number(client.id, number)
        if quote is None:
            return None, Reply(f"There's no quote {quote_label(number)} for {client.business_name}.")
        return quote, None
    quotes = [quote for quote in quotes_for_customer(client.id, ref) if quote.status == status]
    if not quotes:
        return None, Reply(f"There's no {status} quote for {ref}.")
    if len(quotes) > 1:
        # More than one match: list them and let the tradesperson pick, rather than guess.
        choices = ", ".join(
            f"{quote_label(quote.number)} ({quote.title}, {money(quote.total_pence)})" for quote in quotes[:5]
        )
        return None, Reply(f"{ref} has more than one {status} quote: {choices}. Which one? Send its number.")
    return quotes[0], None


def with_pdf(text, kind, item_id, buttons=None):
    """Return our reply with a link to the PDF, if links can work."""
    # Without the secret no link would work, so send the words on their own.
    if not settings.QUOTE_LINK_SECRET:
        return Reply(text, None, buttons)
    return Reply(text, signed_link(kind, item_id, now_utc()), buttons)


def final_reply(client, ref):
    """Make a quote final and return our reply and a link to the clean PDF."""
    quote, problem = find_quote(client, ref, "draft")
    if problem:
        return problem
    if quote is None:
        return Reply("To finish a quote, send FINAL and its number or the customer's name, like FINAL Q3.")
    finish_quote(client.id, quote.id)
    return with_pdf(
        f"Quote {quote_label(quote.number)} is final. Forward this PDF to {quote.customer_name}.",
        "quote",
        quote.id,
        buttons_for(settings.WHATSAPP_FINAL_BUTTONS, quote.number),
    )


INVOICE_HELP = (
    "To make an invoice, send INVOICE and the quote's number or the customer's name, "
    "then their address if Jack doesn't have it:\nINVOICE Q3\n12 Elm Road\nLiverpool L1 0AA"
)


def address_from(lines):
    """Return address lines tidied up and joined, one part per line."""
    return "\n".join(line.strip() for line in lines if line.strip())


def invoice_reply(client, details):
    """Make an invoice from a final quote and return our reply.

    With no address, as when the tradesperson taps Make invoice, Jack asks for one.
    """
    first_line, *address_lines = details.strip().splitlines() or [""]
    quote, problem = find_quote(client, first_line, "final")
    if problem:
        return problem
    if quote is None:
        return Reply(INVOICE_HELP)
    return invoice_for(client, quote, address_from(address_lines))


def invoice_for(client, quote, address):
    """Make the invoice for one of the client's quotes, asking for the address if needed."""
    label = quote_label(quote.number)
    if quote.status != "final":
        return Reply(f"Quote {label} is still a draft. Send FINAL {label} first.")
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
        f"Invoice {invoice_label(invoice.number)} saved for {quote.customer_name}, "
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
    if command in ("ALERTS ON", "ALERTS OFF", "PRICES", "HELP", "OWED", "PAID", "WEEK", "EXPORT", "PAGE"):
        return True
    if words[:1] in (["QUOTE"], ["LOG"], ["BOOK"]):
        return True
    # FINAL Q6, INVOICE Q6, CHANGE Q6 and PAID INV-0244 need a number straight after.
    # A name ("final price 1300") could be part of an answer, so only numbers count here.
    if words[:1] in (["FINAL"], ["INVOICE"], ["CHANGE"], ["PAID"]) and len(words) > 1:
        return read_number(words[1], ("INVOICE", "INV", "Q")) is not None
    return False

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


CHANGE_HELP = "To change a quote, send CHANGE, its number and what to change, like:\nCHANGE Q3 price is 1300"


def change_reply(client, details):
    """Change a quote using what the tradesperson wrote, and return a new draft Reply.

    With nothing to change, as when the tradesperson taps Change, Jack asks what to change.
    """
    ref, _, change = details.strip().partition(" ")
    number = read_number(ref, ("Q",))
    if number is None:
        return Reply(CHANGE_HELP)
    quote = get_quote_by_number(client.id, number)
    if quote is None:
        return Reply(f"There's no quote {quote_label(number)} for {client.business_name}.")
    return change_for(client, quote, change)


def change_for(client, quote, change):
    """Change one of the client's quotes, or ask what to change if they didn't say."""
    label = quote_label(quote.number)
    # The invoice is made from the quote, so changing it would change the invoice.
    if get_invoice_for_quote(quote.id) is not None:
        return Reply(f"Quote {label} has been invoiced, so it can't change. Send a new QUOTE instead.")
    if change.strip() == "":
        ask_question(client.id, "change", quote.id)
        return Reply(f"What needs changing on quote {label}?")

    changed = change_quote(quote, get_quote_lines(quote.id), change)
    if changed is None:
        return Reply(
            f"Sorry, I couldn't make that change. If it's a new price, include it, "
            f"like: CHANGE {label} price is 1300"
        )
    update_quote(quote.id, **changed)
    count = len(changed["lines"])
    lines = "1 line" if count == 1 else f"{count} lines"
    text = (
        f"Draft quote {label} changed: {changed['title']} for {changed['customer_name']}, "
        f"{money(changed['total_pence'])}, {lines} of work."
    )
    return with_pdf(text, "quote", quote.id, buttons_for(settings.WHATSAPP_DRAFT_BUTTONS, quote.number))


def answer_question(client, question, answer):
    """Handle the reply to a question Jack asked, and return our Reply."""
    asked, quote_id = question
    # The question remembers the quote's own ID, not the number the tradesperson sees.
    if asked == "invoice_address":
        return invoice_for(client, get_quote(quote_id), address_from(answer.splitlines()))
    if asked == "change":
        return change_for(client, get_quote(quote_id), answer)
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
    number = get_job(job_id).number
    lines = [f"Job {job_label(number)} logged: {job['customer_name']}, {job['work']}."]
    if job.get("address"):
        lines.append("Address: " + job["address"].replace("\n", ", "))
    if job.get("customer_phone"):
        lines.append(f"Phone: {job['customer_phone']}")
    return Reply("\n".join(lines), None, buttons_for(settings.WHATSAPP_JOB_BUTTONS, number))


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
            f"{invoice_label(invoice['number'])}: {invoice['customer_name']}, "
            f"{money(invoice['total_pence'])}{late}"
        )
    total = sum(invoice["total_pence"] for invoice in invoices)
    return Reply("Who owes you:\n" + "\n".join(lines) + f"\nTotal: {money(total)}")


def paid_reply(client, details):
    """Mark an invoice as paid, by its number or the customer's name.

    With neither, list what's owed and ask which.
    """
    ref = details.strip()
    if ref == "":
        owed = owed_reply(client)
        if not unpaid_invoices(client.id):
            return owed
        ask_question(client.id, "mark_paid", 0)
        return Reply(owed.text + "\n\nWhich invoice has been paid? Send its number or the customer's name.")
    number = read_number(ref, ("INVOICE", "INV"))
    if number is None:
        # A name: find their unpaid invoice, and ask if there's more than one.
        theirs = [invoice for invoice in unpaid_invoices(client.id) if same_customer(invoice["customer_name"], ref)]
        if not theirs:
            return Reply(f"There's no unpaid invoice for {ref}.")
        if len(theirs) > 1:
            choices = ", ".join(
                f"{invoice_label(invoice['number'])} ({money(invoice['total_pence'])})" for invoice in theirs
            )
            return Reply(f"{ref} has more than one unpaid invoice: {choices}. Which one? Send PAID and its number.")
        number = theirs[0]["number"]
    label = invoice_label(number)
    result = mark_paid(client.id, number)
    if result is None:
        return Reply(f"There's no invoice {label} for {client.business_name}.")
    if result == "already paid":
        return Reply(f"{label} was already marked as paid.")
    return Reply(f"{label} marked as paid.")


BOOK_HELP = "To book a job, send BOOK, the job and when, like:\nBOOK Mrs Jones, leaking radiator, Monday 9am"


def book_reply(client, details):
    """Book a logged job in ("BOOK J4"), or log and book a new one in one go."""
    job_number = read_number(details, ("JOB", "J"), need_prefix=True)
    if job_number is not None:
        job = get_job_by_number(client.id, job_number)
        if job is None:
            return Reply(f"There's no job {job_label(job_number)} for {client.business_name}.")
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


def page_reply(client):
    """Send the client a one-time link to log in to their page."""
    token = create_login_link(client.id)
    return Reply(
        f"Here's your page: {settings.PUBLIC_URL}/login/{token}\n"
        "The link works once, for 15 minutes. Don't forward it: it logs in as you."
    )


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
    "FINAL": lambda client, rest: final_reply(client, rest),
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
    "PAGE": lambda client, rest: page_reply(client),
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
