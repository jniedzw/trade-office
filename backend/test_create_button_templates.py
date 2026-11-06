import main
from create_button_templates import MENU, TEMPLATES, WEEKLY, template


def test_buttons_fit_whatsapps_limits():
    for buttons in TEMPLATES.values():
        # In the 24-hour window WhatsApp allows 3 buttons, with short titles.
        assert len(buttons) <= 3
        assert all(len(button["title"]) <= 20 for button in buttons)


def test_template_uses_the_reply_as_its_body():
    made = template("WHATSAPP_DRAFT_BUTTONS", TEMPLATES["WHATSAPP_DRAFT_BUTTONS"])

    assert made["friendly_name"] == "whatsapp_draft_buttons"
    assert made["types"]["twilio/quick-reply"]["body"] == "{{1}}"
    assert made["types"]["twilio/quick-reply"]["actions"][0]["id"] == "FINAL {{2}}"


def test_menu_choices_are_commands_jack_knows():
    for choice in MENU["items"]:
        # WhatsApp list items must be short, and each id must be a command.
        assert len(choice["item"]) <= 24
        assert main.is_new_command(choice["id"])


def test_weekly_template_follows_whatsapps_rules():
    body = WEEKLY["body"]
    # WhatsApp rejects a template that starts or ends with a variable.
    assert not body.startswith("{{") and not body.endswith("}}")
    assert all(main.is_new_command(button["id"]) for button in WEEKLY["actions"])
