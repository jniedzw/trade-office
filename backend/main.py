from contextlib import asynccontextmanager
from typing import NamedTuple

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Form, HTTPException, Request, Response
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse

import settings
from ai import change_quote, read_job, read_quote
from database import (
    mark_paid,
    unpaid_invoices,
    get_job,
    count_jobs_today,
    save_job,
    delete_price,
    get_prices,
    set_price,
    ask_question,
    count_quotes_today,
    create_tables,
    delete_old_calls,
    finish_quote,
    update_quote,
    get_business_details,
    get_invoice,
    get_invoice_for_quote,
    get_client,
    get_client_by_number,
    get_client_by_owner,
    get_quote,
    get_quote_lines,
    get_recent_calls,
    is_opted_out,
    is_skipped,
    log_call,
    now_utc,
    opt_in,
    opt_out,
    overdue_alert_sids,
    pop_pending_alert,
    pop_question,
    retention_cutoff,
    save_invoice,
    save_pending_alert,
    save_quote,
    set_whatsapp_alerts,
    whatsapp_alerts_on,
)
from menu import HELP_TEXT, MENU_TEXT, command_for_title
from pdfs import make_invoice_pdf, make_quote_pdf
from quotes import (
    QUOTE_HELP,
    link_is_valid,
    money,
    parse_job,
    parse_quote,
    signed_link,
    to_pence,
)
from sms import (
    count_old_twilio_records,
    send_sms,
    send_whatsapp,
    send_whatsapp_alert,
    send_whatsapp_buttons,
)

# The usual opt-out and opt-in words for text messages.
STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
START_WORDS = {"START", "UNSTOP"}

# Alerts stop after this many calls from a number we've already texted, so 50 scam
# calls can't flood the owner's phone, but someone ringing urgently still gets seen.
MAX_ALERTS = 3

# Far more quotes than a tradesperson makes in a day, so only a fault or misuse hits it.
MAX_QUOTES_PER_DAY = 50

WHATSAPP_NOT_A_CLIENT = "Hi, this is Ask Jack, admin for UK tradespeople. See askjack.uk or email hello@askjack.uk."

NOT_IN_SERVICE = "Sorry, this number isn't available right now. Please try again later."


def run_cleanup():
    """Delete old records and note how many went in the server's logs."""
    # flush=True sends the line straight to Render's logs instead of holding it back.
    print(f"Cleanup: deleted {delete_old_calls()} old calls", flush=True)
    messages, calls = count_old_twilio_records(retention_cutoff())
    print(f"Cleanup dry run: Twilio has {messages} messages, {calls} calls", flush=True)


def send_overdue_alerts():
    """Send by SMS any WhatsApp alert that hasn't been delivered in time."""
    for message_sid in overdue_alert_sids():
        fall_back_to_sms(message_sid)


@asynccontextmanager
async def lifespan(app):
    # The code before yield runs at startup, the code after it at shutdown.
    scheduler = BackgroundScheduler(timezone="UTC")
    # Every night at 3am, and once now in case a restart skipped last night's run.
    # It runs in the background, so a cleanup error can't stop calls being answered.
    scheduler.add_job(
        run_cleanup, "cron", hour=3, next_run_time=now_utc(), misfire_grace_time=3600
    )
    # Twilio tells us when delivery fails, but a phone that's off tells us nothing.
    scheduler.add_job(send_overdue_alerts, "interval", minutes=1)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
create_tables()  # Make sure the tables exist when the app starts
validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)


async def check_twilio_signature(request):
    """Stop the request with a 403 unless it was really sent by Twilio."""
    # Twilio signs our public address, not the 127.0.0.1 one our app sees.
    url = settings.PUBLIC_URL + request.url.path
    form = await request.form()
    signature = request.headers.get("X-Twilio-Signature", "")

    if not validator.validate(url, dict(form), signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")


def can_text(number):
    """Return True if number is a UK mobile we can text back."""
    # Withheld callers arrive as "anonymous", "+266696687" etc., and landlines
    # (+441, +442, +443) can't receive texts, so only UK mobiles (+447) pass.
    return number.startswith("+447")


def try_send(to, body, from_number):
    """Send a text and return True, or False if it failed.

    A failed text must never crash the call, so this catches every error.
    """
    try:
        send_sms(to, body, from_number)
        return True
    except Exception as error:
        # Only the error's type and code: its message can include a customer's number.
        code = getattr(error, "code", "no code")
        print(f"Text failed: {type(error).__name__} ({code})", flush=True)
        return False


def alert_james(client, problem, detail=""):
    """Tell James something failed: always in the logs, and by text if we can."""
    # detail can hold a customer's number, so it goes in the text but not the logs.
    print(f"Problem for {client.business_name}: {problem}", flush=True)
    if settings.ALERT_MOBILE:
        message = f"Ask Jack problem, {client.business_name}: {problem} {detail}"
        try_send(settings.ALERT_MOBILE, message.strip(), client.twilio_number)


def alert_by_sms(client, alert):
    """Text the owner an alert, and tell James if that fails."""
    if not try_send(client.owner_mobile, alert, client.twilio_number):
        alert_james(client, "couldn't alert the owner.", alert)


def fall_back_to_sms(message_sid):
    """Send a WhatsApp alert by SMS instead, unless it's already been dealt with."""
    pending = pop_pending_alert(message_sid)
    if pending is not None:
        client_id, alert = pending
        alert_by_sms(get_client(client_id), alert)


def whatsapp_alert_details(caller, texted, skipped, repeat):
    """Return the two gaps in the WhatsApp template: who rang, and what happened."""
    withheld = not caller.startswith("+") or caller == "+266696687"
    who = "a withheld number" if withheld else caller
    if texted:
        status = "we texted them back"
    elif repeat:
        status = "we texted them in the last 24 hours"
    elif skipped:
        status = "family and friends, so no text was sent"
    elif withheld:
        status = "no number to text"
    else:
        status = "we couldn't text them, so please ring back"
    return who, status


def send_owner_alert(client, caller, texted, skipped, repeat):
    """Alert the owner on WhatsApp if they've opted in, otherwise by SMS."""
    alert = owner_alert(caller, texted, skipped, repeat)
    use_whatsapp = settings.WHATSAPP_NUMBER and settings.WHATSAPP_ALERT_TEMPLATE
    if use_whatsapp and whatsapp_alerts_on(client.id):
        try:
            who, status = whatsapp_alert_details(caller, texted, skipped, repeat)
            message_sid = send_whatsapp_alert(client.owner_mobile, who, status)
            save_pending_alert(message_sid, client.id, alert)
            return
        except Exception as error:
            print(f"WhatsApp alert failed: {type(error).__name__}", flush=True)
    alert_by_sms(client, alert)


def owner_alert(caller, texted, skipped=False, repeat=False):
    """Return the text we send the tradesperson about a missed call."""
    if texted:
        return f"Missed call from {caller}. We've texted them back."
    elif repeat:
        return f"Missed call from {caller} again. We texted them in the last 24 hours."
    elif skipped:
        return f"Missed call from {caller} (family and friends, so no text was sent)."
    # "+266696687" spells ANONYMOUS on a keypad: Twilio's code for a withheld number.
    elif caller.startswith("+") and caller != "+266696687":
        return f"Missed call from {caller}. We couldn't text them, so please ring back."
    else:
        return "Missed call from a withheld number."


def greeting(business_name, texted):
    """Return what the caller hears before we hang up."""
    if texted:
        return f"Sorry, {business_name} can't take your call right now. We'll text you now."
    return f"Sorry, {business_name} can't take your call right now. Please try again later."


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
    quote = parse_quote(details) or read_quote(details)
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
    quote = read_quote(f"For {job.customer_name}: {job.work}. {answer}", typed=answer)
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
    if command in ("ALERTS ON", "ALERTS OFF", "PRICES", "HELP", "OWED", "PAID"):
        return True
    if words[:1] in (["QUOTE"], ["LOG"]):
        return True
    # FINAL 6, INVOICE 6 and CHANGE 6 need a quote number straight after.
    return words[:1] in (["FINAL"], ["INVOICE"], ["CHANGE"], ["PAID"]) and words[1:2] != [] and words[1].isdigit()

PRICE_HELP = (
    "To save a price, send PRICE, the job and the price, like:\nPRICE boiler service 85\n"
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
    return Reply("Your prices:\n" + "\n".join(lines))


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
    return Reply("Sorry, I lost track of that. Please start again.")


LOG_HELP = (
    "To log a job, send LOG and the job in your own words, like:\n"
    "LOG Mrs Jones, 12 Elm Road, 07700 900123, leaking radiator in the kitchen"
)


def log_reply(client, details):
    """Save a new job from what the client wrote, and return our Reply.

    With no details, as when the tradesperson taps Log a job, Jack asks about the job.
    """
    if details.strip() == "":
        ask_question(client.id, "new_job", 0)
        return Reply("Tell me about the job: who it's for, their address and number if you have them, and the work.")
    # The same cap as quotes, since reading a job uses the AI too.
    if count_jobs_today(client.id) >= MAX_QUOTES_PER_DAY:
        alert_james(client, "hit the daily job limit.")
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
    "HELP": lambda client, rest: Reply(HELP_TEXT),
    "ALERTS": lambda client, rest: alerts_reply(client, rest),
    "PAID": lambda client, rest: paid_reply(client, rest),
    "OWED": lambda client, rest: owed_reply(client),
}


def whatsapp_reply(sender, body=""):
    """Handle a WhatsApp message and return our Reply."""
    # Twilio writes WhatsApp numbers as "whatsapp:+447...", so take that off first.
    client = get_client_by_owner(sender.removeprefix("whatsapp:"))
    if client is None or not client.active:
        return Reply(WHATSAPP_NOT_A_CLIENT)

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


def forward_message(sender, body):
    """Return the text we pass on to the tradesperson when a customer replies."""
    if body.strip() == "":
        return f"From {sender}: (a picture or empty message, so please ring them back)"
    return f"From {sender}: {body}"


@app.get("/")
def root():
    return {"message": "Trade Office is running"}


@app.post("/voice")
async def voice(request: Request, From: str = Form(), To: str = Form()):
    # Twilio calls this when a call reaches our number.
    await check_twilio_signature(request)

    # Which tradesperson owns the number that was called?
    client = get_client_by_number(To)
    if client is None or not client.active:
        message = NOT_IN_SERVICE
    else:
        # Family and friends on the skip list never get the business text.
        skipped = is_skipped(client.id, From)
        opted_out = is_opted_out(client.id, From)
        # Only a text that really went out counts, so a failed one gets retried.
        recent_calls = get_recent_calls(client.id, From)
        repeat = any(call.texted for call in recent_calls)
        texted = can_text(From) and not skipped and not opted_out and not repeat
        if texted:
            texted = try_send(From, client.text_back_message, client.twilio_number)
            if not texted:
                alert_james(client, "couldn't text a customer back.")
        message = greeting(client.business_name, texted)

        # If the text failed, this tells the owner to ring the customer instead.
        too_many = repeat and len(recent_calls) >= MAX_ALERTS
        if not too_many:
            send_owner_alert(client, From, texted, skipped, repeat)

        try:
            log_call(client.id, From, texted)
        except Exception:
            alert_james(client, "couldn't log a call.")

    twiml = VoiceResponse()
    twiml.say(message, voice="Polly.Amy", language="en-GB")
    twiml.hangup()
    return Response(content=str(twiml), media_type="application/xml")


@app.post("/sms")
async def incoming_sms(
    request: Request, From: str = Form(), To: str = Form(), Body: str = Form("")
):
    # Twilio calls this when someone texts our number.
    await check_twilio_signature(request)

    client = get_client_by_number(To)

    # Texts from the owner will be commands for the assistant later; for now
    # we only pass on customers' messages so their replies aren't lost.
    if client is not None and client.active and From != client.owner_mobile:
        word = Body.strip().upper()
        reply = None
        if word in STOP_WORDS:
            opt_out(client.id, From)
            reply = f"You won't get any more texts from {client.business_name}. Reply START to opt back in."
        elif word in START_WORDS:
            opt_in(client.id, From)
            reply = f"You're opted back in to texts from {client.business_name}."

        if reply and not try_send(From, reply, client.twilio_number):
            alert_james(client, "couldn't confirm a STOP or START.")

        # The owner always sees the message, including a STOP.
        forwarded = forward_message(From, Body)
        if not try_send(client.owner_mobile, forwarded, client.twilio_number):
            alert_james(client, "couldn't forward a reply.", forwarded)

    # An empty reply, so Twilio doesn't send the customer anything back.
    return Response(content=str(MessagingResponse()), media_type="application/xml")


@app.post("/whatsapp")
async def incoming_whatsapp(
    request: Request,
    From: str = Form(),
    Body: str = Form(""),
    ButtonPayload: str = Form(""),
    ListId: str = Form(""),
):
    # Twilio calls this when someone messages our WhatsApp number.
    await check_twilio_signature(request)

    # A tapped button sends its title as Body, and the ID we gave it as ButtonPayload.
    # We make each ID a command, like "FINAL 6", so buttons and typing work the same.
    # Replying in the response sends it straight back through WhatsApp.
    # A choice from a list menu arrives the same way, with its ID in ListId.
    reply = whatsapp_reply(From, ButtonPayload or ListId or Body)
    twiml = MessagingResponse()
    if reply.buttons:
        reply = send_with_buttons(From, reply)
        if reply is None:
            # Already sent, so the response must be empty or Jack would reply twice.
            return Response(content=str(twiml), media_type="application/xml")

    message = twiml.message(reply.text)
    if reply.link:
        # Twilio fetches the file from the link and sends it with the words.
        message.media(reply.link)
    return Response(content=str(twiml), media_type="application/xml")


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


@app.get("/quotes/{quote_id}.pdf")
def quote_pdf(quote_id: int, expires: int = 0, sig: str = ""):
    # Anyone can reach this address, so only a link we signed gets the PDF.
    if not link_is_valid("quote", quote_id, expires, sig, now_utc()):
        raise HTTPException(status_code=403, detail="This link isn't valid")
    quote = get_quote(quote_id)
    if quote is None:
        raise HTTPException(status_code=404, detail="Quote not found")
    business_name = get_client(quote.client_id).business_name
    details = get_business_details(quote.client_id)
    pdf = make_quote_pdf(quote, business_name, get_quote_lines(quote_id), details)
    return Response(content=pdf, media_type="application/pdf")


@app.get("/invoices/{invoice_id}.pdf")
def invoice_pdf(invoice_id: int, expires: int = 0, sig: str = ""):
    # Signed like quote links, but only for invoices.
    if not link_is_valid("invoice", invoice_id, expires, sig, now_utc()):
        raise HTTPException(status_code=403, detail="This link isn't valid")
    invoice = get_invoice(invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    quote = get_quote(invoice.quote_id)
    business_name = get_client(invoice.client_id).business_name
    details = get_business_details(invoice.client_id)
    lines = get_quote_lines(quote.id)
    pdf = make_invoice_pdf(invoice, quote, business_name, lines, details)
    return Response(content=pdf, media_type="application/pdf")


@app.post("/whatsapp-status")
async def whatsapp_status(
    request: Request, MessageSid: str = Form(), MessageStatus: str = Form()
):
    # Twilio calls this as a WhatsApp alert moves along: sent, delivered, read...
    await check_twilio_signature(request)

    if MessageStatus in ("delivered", "read"):
        pop_pending_alert(MessageSid)
    elif MessageStatus in ("failed", "undelivered"):
        fall_back_to_sms(MessageSid)

    return Response(status_code=204)
