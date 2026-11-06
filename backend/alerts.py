"""Sending texts safely, and telling James when something goes wrong."""

import settings
from sms import send_sms


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
