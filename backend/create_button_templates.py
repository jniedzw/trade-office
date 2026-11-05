"""Create the WhatsApp button templates in Twilio. Run it once, from your laptop:
    python create_button_templates.py
Then put the IDs it prints in .env and in Render's Environment.

These are only sent as replies inside WhatsApp's 24-hour window, which Twilio
says needs no approval, so they're never submitted to Meta.
"""

import sys

import requests

import settings
from menu import MENU_CHOICES

CONTENT_API = "https://content.twilio.com/v1/Content"

# {{1}} is Jack's reply and {{2}} the quote number. Each button's id is the
# command a tap sends back to us (see ButtonPayload in main.py).
TEMPLATES = {
    "WHATSAPP_DRAFT_BUTTONS": [
        {"title": "Make final", "id": "FINAL {{2}}"},
        {"title": "Change", "id": "CHANGE {{2}}"},
    ],
    "WHATSAPP_FINAL_BUTTONS": [
        {"title": "Make invoice", "id": "INVOICE {{2}}"},
    ],
    # Under a logged job, where {{2}} is the job number.
    "WHATSAPP_JOB_BUTTONS": [
        {"title": "Quote it", "id": "QUOTE JOB {{2}}"},
    ],
}


# The Options menu: a button that opens a list, built from menu.py. Each item's id is a command.
MENU = {
    "body": "{{1}}",
    "button": "Options",
    "items": [
        {"item": title, "id": command, "description": description}
        for title, command, description in MENU_CHOICES
    ],
}


def menu_template():
    """Return what the Content API needs to create the Options menu."""
    return {
        "friendly_name": "whatsapp_menu",
        "language": "en",
        "variables": {"1": "Hi, it's Jack. What do you need?"},
        "types": {"twilio/list-picker": MENU},
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
    # Name templates to make just those, like: python create_button_templates.py WHATSAPP_DRAFT_BUTTONS
    names = sys.argv[1:] or [*TEMPLATES, "WHATSAPP_MENU"]
    auth = (settings.TWILIO_API_KEY_SID, settings.TWILIO_API_KEY_SECRET)
    for name in names:
        made = menu_template() if name == "WHATSAPP_MENU" else template(name, TEMPLATES[name])
        response = requests.post(CONTENT_API, json=made, auth=auth, timeout=30)
        response.raise_for_status()
        print(f"{name}={response.json()['sid']}")


if __name__ == "__main__":
    main()
