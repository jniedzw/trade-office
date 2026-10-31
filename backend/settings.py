"""All of the app's settings in one place."""

import os

from dotenv import load_dotenv

load_dotenv()

# Secrets and machine-specific values, read from .env
TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
# The Auth Token is only used to check Twilio's signatures on webhooks.
TWILIO_AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
# Sending uses an API key, which can be revoked on its own if it ever leaks.
TWILIO_API_KEY_SID = os.environ["TWILIO_API_KEY_SID"]
TWILIO_API_KEY_SECRET = os.environ["TWILIO_API_KEY_SECRET"]
PUBLIC_URL = os.environ["PUBLIC_URL"]
# Optional: the mobile that gets a text when something fails.
ALERT_MOBILE = os.environ.get("ALERT_MOBILE")
# Optional: WhatsApp alerts stay off until both of these are set.
WHATSAPP_NUMBER = os.environ.get("WHATSAPP_NUMBER")
WHATSAPP_ALERT_TEMPLATE = os.environ.get("WHATSAPP_ALERT_TEMPLATE")
