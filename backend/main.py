from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Form, HTTPException, Request, Response
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse

import settings
import web
from alerts import alert_by_sms, alert_james, try_send
from database import (
    create_tables,
    delete_old_calls,
    everything_for,
    get_business_details,
    get_client,
    get_client_by_number,
    get_invoice,
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
    retention_cutoff,
    save_pending_alert,
    whatsapp_alerts_on,
)
from export import XLSX, make_export
from pdfs import make_invoice_pdf, make_quote_pdf
from quotes import link_is_valid
from sms import count_old_twilio_records, send_whatsapp_alert
from weekly import send_weekly_updates
from whatsapp import send_with_buttons, whatsapp_reply

# The usual opt-out and opt-in words for text messages.
STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
START_WORDS = {"START", "UNSTOP"}

# Alerts stop after this many calls from a number we've already texted, so 50 scam
# calls can't flood the owner's phone, but someone ringing urgently still gets seen.
MAX_ALERTS = 3

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
    # Sunday 6pm UK time, so it moves with the clocks rather than drifting an hour.
    scheduler.add_job(
        send_weekly_updates, "cron", day_of_week="sun", hour=18,
        timezone="Europe/London", misfire_grace_time=3600,
    )
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
# The tradesperson's own pages live in web.py.
app.include_router(web.router)
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


@app.get("/exports/{client_id}.xlsx")
def export_file(client_id: int, expires: int = 0, sig: str = ""):
    # Everything a client has, so only a link we signed in the last hour works.
    if not link_is_valid("export", client_id, expires, sig, now_utc()):
        raise HTTPException(status_code=403, detail="This link isn't valid")
    client = get_client(client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    spreadsheet = make_export(everything_for(client_id), client.markup_percent)
    return Response(content=spreadsheet, media_type=XLSX)


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
