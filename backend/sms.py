import json

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


def send_whatsapp_alert(to: str, who: str, status: str) -> str:
    """Send the missed-call template and return Twilio's message ID."""
    # WhatsApp only lets us message someone first with a template Meta has approved.
    message = client.messages.create(
        to=f"whatsapp:{to}",
        from_=f"whatsapp:{settings.WHATSAPP_NUMBER}",
        content_sid=settings.WHATSAPP_ALERT_TEMPLATE,
        content_variables=json.dumps({"1": who, "2": status}),
        status_callback=f"{settings.PUBLIC_URL}/whatsapp-status",
    )
    return message.sid


def count_old_twilio_records(cutoff) -> tuple[int, int]:
    """Return how many messages and calls Twilio holds from before cutoff."""
    # A dry run: this only counts, so we can check the numbers before deleting anything.
    messages = client.messages.list(date_sent_before=cutoff)
    calls = client.calls.list(start_time_before=cutoff)
    return len(messages), len(calls)
