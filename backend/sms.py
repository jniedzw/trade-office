import os

from dotenv import load_dotenv
from twilio.rest import Client

load_dotenv()

client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])


def send_sms(to: str, body: str) -> str:
    """Send a text from our Twilio number and return Twilio's message ID."""
    message = client.messages.create(
        to=to,
        from_=os.environ["TWILIO_PHONE_NUMBER"],
        body=body,
    )
    return message.sid
