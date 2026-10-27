from twilio.rest import Client

import settings

client = Client(
    settings.TWILIO_API_KEY_SID,
    settings.TWILIO_API_KEY_SECRET,
    settings.TWILIO_ACCOUNT_SID,
)


def send_sms(to: str, body: str) -> str:
    """Send a text from our Twilio number and return Twilio's message ID."""
    message = client.messages.create(
        to=to,
        from_=settings.TWILIO_PHONE_NUMBER,
        body=body,
    )
    return message.sid
