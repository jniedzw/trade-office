from twilio.rest import Client

import settings

client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def send_sms(to: str, body: str) -> str:
    """Send a text from our Twilio number and return Twilio's message ID."""
    message = client.messages.create(
        to=to,
        from_=settings.TWILIO_PHONE_NUMBER,
        body=body,
    )
    return message.sid
