from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Form, HTTPException, Request, Response
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse

import settings
from database import (
    create_tables,
    delete_old_calls,
    get_client_by_number,
    get_recent_calls,
    is_opted_out,
    is_skipped,
    log_call,
    now_utc,
    opt_in,
    opt_out,
    retention_cutoff,
)
from sms import count_old_twilio_records, send_sms

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


@asynccontextmanager
async def lifespan(app):
    # The code before yield runs at startup, the code after it at shutdown.
    scheduler = BackgroundScheduler(timezone="UTC")
    # Every night at 3am, and once now in case a restart skipped last night's run.
    # It runs in the background, so a cleanup error can't stop calls being answered.
    scheduler.add_job(
        run_cleanup, "cron", hour=3, next_run_time=now_utc(), misfire_grace_time=3600
    )
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
        alert = owner_alert(From, texted, skipped, repeat)
        too_many = repeat and len(recent_calls) >= MAX_ALERTS
        if not too_many and not try_send(
            client.owner_mobile, alert, client.twilio_number
        ):
            alert_james(client, "couldn't alert the owner.", alert)

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
