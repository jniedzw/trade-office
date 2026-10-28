from twilio.rest import Client

import settings

client = Client(
    settings.TWILIO_API_KEY_SID,
    settings.TWILIO_API_KEY_SECRET,
    settings.TWILIO_ACCOUNT_SID,
)


def send_sms(to: str, body: str, from_number: str) -> str:
    """Send a text from one of our Twilio numbers and return Twilio's message ID."""
    message = client.messages.create(
        to=to,
        from_=from_number,
        body=body,
    )
    return message.sid
