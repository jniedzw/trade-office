import os

from fastapi import FastAPI

from sms import send_sms

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
