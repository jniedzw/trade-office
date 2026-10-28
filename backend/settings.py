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


def read_skip_numbers():
    """Return the family and friends numbers that never get the business text."""
    # In .env as one comma-separated line; optional, so it can be missing or empty.
    numbers = []
    for number in os.environ.get("SKIP_NUMBERS", "").split(","):
        if number.strip():
            numbers.append(number.strip())
    return numbers


SKIP_NUMBERS = read_skip_numbers()

# Missed-call log (contains callers' numbers, so it's gitignored)
CALL_LOG_FILE = "calls.csv"
