import os

from fastapi import FastAPI, Form, Response
from twilio.twiml.voice_response import VoiceResponse

from sms import send_sms

# Hard-coded for now; Session 4 moves these into settings.
BUSINESS_NAME = "Trade Office"
TEXT_BACK_MESSAGE = (
    f"Hi, it's {BUSINESS_NAME}. Sorry we missed your call. "
    "What can we help with? We'll call you back today."
)

app = FastAPI()


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
def voice(From: str = Form(), To: str = Form()):
    # Twilio calls this when a call reaches our number.
    send_sms(From, TEXT_BACK_MESSAGE)

    twiml = VoiceResponse()
    twiml.say(
        f"Sorry, {BUSINESS_NAME} can't take your call right now. We'll text you now.",
        voice="Polly.Amy",
        language="en-GB",
    )
    twiml.hangup()
    return Response(content=str(twiml), media_type="application/xml")
