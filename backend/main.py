import os

from fastapi import FastAPI, Form, HTTPException, Request, Response
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse

from sms import send_sms

# Hard-coded for now; Session 4 moves these into settings.
BUSINESS_NAME = "Trade Office"
TEXT_BACK_MESSAGE = (
    f"Hi, it's {BUSINESS_NAME}. Sorry we missed your call. "
    "What can we help with? We'll call you back today."
)

app = FastAPI()
validator = RequestValidator(os.environ["TWILIO_AUTH_TOKEN"])


async def check_twilio_signature(request):
    """Stop the request with a 403 unless it was really sent by Twilio."""
    # Twilio signs our public address, not the 127.0.0.1 one our app sees.
    url = os.environ["PUBLIC_URL"] + request.url.path
    form = await request.form()
    signature = request.headers.get("X-Twilio-Signature", "")

    if not validator.validate(url, dict(form), signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")


def can_text(number):
    """Return True if number is a UK mobile we can text back."""
    # Withheld callers arrive as "anonymous", "+266696687" etc., and landlines
    # (+441, +442, +443) can't receive texts, so only UK mobiles (+447) pass.
    return number.startswith("+447")


@app.get("/")
def root():
    return {"message": "Trade Office is running"}


@app.post("/test-sms")
def test_sms():
    # Only ever texts our own number, so a stranger can't use this to text anyone else.
    # Trial accounts can only send Twilio's template names; change this after upgrading.
    sid = send_sms(os.environ["MY_MOBILE_NUMBER"], "sms_appointment_reminders")
    return {"sid": sid}


@app.post("/voice")
async def voice(request: Request, From: str = Form(), To: str = Form()):
    # Twilio calls this when a call reaches our number.
    await check_twilio_signature(request)

    if can_text(From):
        send_sms(From, TEXT_BACK_MESSAGE)
        message = f"Sorry, {BUSINESS_NAME} can't take your call right now. We'll text you now."
    else:
        message = f"Sorry, {BUSINESS_NAME} can't take your call right now. Please try again later."

    twiml = VoiceResponse()
    twiml.say(message, voice="Polly.Amy", language="en-GB")
    twiml.hangup()
    return Response(content=str(twiml), media_type="application/xml")
