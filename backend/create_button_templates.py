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
from menu import MENU_GROUPS

CONTENT_API = "https://content.twilio.com/v1/Content"

# {{1}} is Jack's reply and {{2}} the quote number. Each button's id is the
# command a tap sends back to us (see ButtonPayload in main.py).
TEMPLATES = {
    "WHATSAPP_DRAFT_BUTTONS": [
        {"title": "Make final", "id": "FINAL {{2}}"},
        {"title": "Change", "id": "CHANGE {{2}}"},
    ],
    "WHATSAPP_FINAL_BUTTONS": [
        {"title": "Send to customer", "id": "SEND {{2}}"},
        {"title": "Make invoice", "id": "INVOICE {{2}}"},
    ],
    # Under Who owes me, when something's overdue. No number: it reminds everyone overdue.
    "WHATSAPP_OWED_BUTTONS": [
        {"title": "Send reminders", "id": "REMIND"},
    ],
    # Under a logged job, where {{2}} is the job number.
    "WHATSAPP_JOB_BUTTONS": [
        {"title": "Quote it", "id": "QUOTE JOB {{2}}"},
        {"title": "Book it", "id": "BOOK JOB {{2}}"},
    ],
}


# The main menu: three buttons, one for each group in menu.py.
MAIN_MENU = [{"title": title, "id": f"MENU {group}"} for group, (title, _) in MENU_GROUPS.items()]


def menu_template(name):
    """Return the Content API request for the main menu or one group's menu.

    A group with up to 3 choices is buttons. More than that is a list, opened by a button.
    """
    if name == "WHATSAPP_MENU":
        return template(name, MAIN_MENU)
    group = name.removeprefix("WHATSAPP_").removesuffix("_MENU")
    title, choices = MENU_GROUPS[group]
    if len(choices) <= 3:
        return template(name, [{"title": t, "id": command} for t, command, _ in choices])
    items = [{"item": t, "id": command, "description": d} for t, command, d in choices]
    return {
        "friendly_name": name.lower(),
        "language": "en",
        "variables": {"1": f"{title}: what would you like?"},
        "types": {"twilio/list-picker": {"body": "{{1}}", "button": title, "items": items}},
    }


MENU_NAMES = ["WHATSAPP_MENU", *(f"WHATSAPP_{group}_MENU" for group in MENU_GROUPS)]


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
    # Example values, which Twilio asks for with every variable it uses.
    variables = {"1": "Draft quote 1 saved."}
    if any("{{2}}" in button["id"] for button in buttons):
        variables["2"] = "1"
    return {
        "friendly_name": name.lower(),
        "language": "en",
        "variables": variables,
        "types": {"twilio/quick-reply": {"body": "{{1}}", "actions": buttons}},
    }


def main():
    # Name templates to make just those, like: python create_button_templates.py WHATSAPP_DRAFT_BUTTONS
    names = sys.argv[1:] or [*TEMPLATES, *MENU_NAMES]
    auth = (settings.TWILIO_API_KEY_SID, settings.TWILIO_API_KEY_SECRET)
    for name in names:
        if name in MENU_NAMES:
            made = menu_template(name)
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
