"""Create the WhatsApp button templates in Twilio. Run it once, from your laptop:
    python create_button_templates.py
Then put the two IDs it prints in .env and in Render's Environment.

These are only sent as replies inside WhatsApp's 24-hour window, which Twilio
says needs no approval, so they're never submitted to Meta.
"""

import requests

import settings

CONTENT_API = "https://content.twilio.com/v1/Content"

# {{1}} is Jack's reply and {{2}} the quote number. Each button's id is the
# command a tap sends back to us (see ButtonPayload in main.py).
TEMPLATES = {
    "WHATSAPP_DRAFT_BUTTONS": [
        {"title": "Make final", "id": "FINAL {{2}}"},
        {"title": "Start again", "id": "QUOTE"},
    ],
    "WHATSAPP_FINAL_BUTTONS": [
        {"title": "Make invoice", "id": "INVOICE {{2}}"},
    ],
}


def template(name, buttons):
    """Return what the Content API needs to create one quick reply template."""
    return {
        "friendly_name": name.lower(),
        "language": "en",
        # Example values, which Twilio asks for with every variable.
        "variables": {"1": "Draft quote 1 saved.", "2": "1"},
        "types": {"twilio/quick-reply": {"body": "{{1}}", "actions": buttons}},
    }


def main():
    auth = (settings.TWILIO_API_KEY_SID, settings.TWILIO_API_KEY_SECRET)
    for name, buttons in TEMPLATES.items():
        response = requests.post(CONTENT_API, json=template(name, buttons), auth=auth, timeout=30)
        response.raise_for_status()
        print(f"{name}={response.json()['sid']}")


if __name__ == "__main__":
    main()
