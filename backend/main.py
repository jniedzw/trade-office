from fastapi import FastAPI, Form, HTTPException, Request, Response
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse

import settings
from call_log import log_call
from sms import send_sms

app = FastAPI()
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


def owner_alert(caller, texted):
    """Return the text we send the tradesperson about a missed call."""
    if texted:
        return f"Missed call from {caller}. We've texted them back."
    # "+266696687" spells ANONYMOUS on a keypad: Twilio's code for a withheld number.
    elif caller.startswith("+") and caller != "+266696687":
        return f"Missed call from {caller}. We couldn't text them, so please ring back."
    else:
        return "Missed call from a withheld number."


def forward_message(sender, body):
    """Return the text we pass on to the tradesperson when a customer replies."""
    if body.strip() == "":
        return f"From {sender}: (a picture or empty message, check your Twilio logs)"
    return f"From {sender}: {body}"


@app.get("/")
def root():
    return {"message": "Trade Office is running"}


@app.post("/test-sms")
def test_sms():
    # Only ever texts our own number, so a stranger can't use this to text anyone else.
    sid = send_sms(settings.MY_MOBILE_NUMBER, "Test from Trade Office")
    return {"sid": sid}


@app.post("/voice")
async def voice(request: Request, From: str = Form(), To: str = Form()):
    # Twilio calls this when a call reaches our number.
    await check_twilio_signature(request)

    texted = can_text(From)
    if texted:
        send_sms(From, settings.TEXT_BACK_MESSAGE)
        message = settings.GREETING_WITH_TEXT
    else:
        message = settings.GREETING_NO_TEXT

    log_call(From, To, texted)
    send_sms(settings.OWNER_MOBILE_NUMBER, owner_alert(From, texted))

    twiml = VoiceResponse()
    twiml.say(message, voice="Polly.Amy", language="en-GB")
    twiml.hangup()
    return Response(content=str(twiml), media_type="application/xml")


@app.post("/sms")
async def incoming_sms(request: Request, From: str = Form(), Body: str = Form("")):
    # Twilio calls this when someone texts our number.
    await check_twilio_signature(request)

    # Texts from the owner will be commands for the assistant later; for now
    # we only pass on customers' messages so their replies aren't lost.
    if From != settings.OWNER_MOBILE_NUMBER:
        send_sms(settings.OWNER_MOBILE_NUMBER, forward_message(From, Body))

    # An empty reply, so Twilio doesn't send the customer anything back.
    return Response(content=str(MessagingResponse()), media_type="application/xml")
