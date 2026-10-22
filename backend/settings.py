"""All of the app's settings in one place."""

import os

from dotenv import load_dotenv

load_dotenv()

# Secrets and machine-specific values, read from .env
TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_PHONE_NUMBER = os.environ["TWILIO_PHONE_NUMBER"]
MY_MOBILE_NUMBER = os.environ["MY_MOBILE_NUMBER"]
PUBLIC_URL = os.environ["PUBLIC_URL"]

# Business wording (moves to the clients table in Phase 2)
BUSINESS_NAME = "Trade Office"
TEXT_BACK_MESSAGE = (
    f"Hi, it's {BUSINESS_NAME}. Sorry we missed your call. "
    "What can we help with? We'll call you back today."
)
GREETING_WITH_TEXT = (
    f"Sorry, {BUSINESS_NAME} can't take your call right now. We'll text you now."
)
GREETING_NO_TEXT = (
    f"Sorry, {BUSINESS_NAME} can't take your call right now. Please try again later."
)
