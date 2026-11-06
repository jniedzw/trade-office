"""Create the WhatsApp button templates in Twilio. Run it once, from your laptop:
    python create_button_templates.py
Then put the IDs it prints in .env and in Render's Environment.

Most are only sent as replies inside WhatsApp's 24-hour window, which Twilio
says needs no approval. The weekly one is the exception: name it on its own
(python create_button_templates.py WHATSAPP_WEEKLY_TEMPLATE) to create it and
submit it to Meta.
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
        {"title": "Book it", "id": "BOOK JOB {{2}}"},
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


# The Sunday week-ahead opener. Jack messages first, outside the 24-hour window,
# so unlike the others this one must be approved by Meta. WhatsApp won't allow new
# lines in a variable, so it's a short summary, and a tap opens the window for details.
WEEKLY = {
    "body": "Hi, it's Jack with your week ahead: {{1}} booked, and {{2}} owed to you. Tap below for the details.",
    "actions": [
        {"title": "See my week", "id": "WEEK"},
        {"title": "Who owes me", "id": "OWED"},
    ],
}


def weekly_template():
    """Return what the Content API needs to create the Sunday week-ahead template."""
    return {
        "friendly_name": "weekly_update",
        "language": "en",
        # Meta checks the example values when it reviews the template.
        "variables": {"1": "4 jobs", "2": "£1,335.50"},
        "types": {"twilio/quick-reply": WEEKLY},
    }


def submit_for_approval(sid, auth):
    """Ask WhatsApp to approve a template. Approval can take a day or two."""
    approval = {"name": "weekly_update", "category": "UTILITY"}
    response = requests.post(
        f"{CONTENT_API}/{sid}/ApprovalRequests/whatsapp", json=approval, auth=auth, timeout=30
    )
    response.raise_for_status()
    print(f"Submitted for approval: {response.json().get('status')}")


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
        if name == "WHATSAPP_MENU":
            made = menu_template()
        elif name == "WHATSAPP_WEEKLY_TEMPLATE":
            made = weekly_template()
        else:
            made = template(name, TEMPLATES[name])
        response = requests.post(CONTENT_API, json=made, auth=auth, timeout=30)
        response.raise_for_status()
        sid = response.json()["sid"]
        print(f"{name}={sid}")
        if name == "WHATSAPP_WEEKLY_TEMPLATE":
            submit_for_approval(sid, auth)


if __name__ == "__main__":
    main()
