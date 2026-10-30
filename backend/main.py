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
    is_opted_out,
    is_skipped,
    log_call,
    now_utc,
    opt_in,
    opt_out,
)
from sms import send_sms

# The usual opt-out and opt-in words for text messages.
STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
START_WORDS = {"START", "UNSTOP"}

NOT_IN_SERVICE = "Sorry, this number isn't available right now. Please try again later."


def run_cleanup():
    """Delete old records and note how many went in the server's logs."""
    print(f"Cleanup: deleted {delete_old_calls()} old calls")


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


def owner_alert(caller, texted, skipped=False):
    """Return the text we send the tradesperson about a missed call."""
    if texted:
        return f"Missed call from {caller}. We've texted them back."
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
        return f"From {sender}: (a picture or empty message, check your Twilio logs)"
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
        texted = can_text(From) and not skipped and not opted_out
        if texted:
            send_sms(From, client.text_back_message, client.twilio_number)
        message = greeting(client.business_name, texted)

        log_call(client.id, From, texted)
        alert = owner_alert(From, texted, skipped)
        send_sms(client.owner_mobile, alert, client.twilio_number)

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
        if word in STOP_WORDS:
            opt_out(client.id, From)
            reply = f"You won't get any more texts from {client.business_name}. Reply START to opt back in."
            send_sms(From, reply, client.twilio_number)
        elif word in START_WORDS:
            opt_in(client.id, From)
            reply = f"You're opted back in to texts from {client.business_name}."
            send_sms(From, reply, client.twilio_number)

        # The owner always sees the message, including a STOP.
        forwarded = forward_message(From, Body)
        send_sms(client.owner_mobile, forwarded, client.twilio_number)

    # An empty reply, so Twilio doesn't send the customer anything back.
    return Response(content=str(MessagingResponse()), media_type="application/xml")
