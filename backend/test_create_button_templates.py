import whatsapp
from create_button_templates import (
    ACCEPTED, MAIN_MENU, MENU_NAMES, TEMPLATES, WEEKLY, accepted_template, menu_template, template,
)


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


def test_every_menu_fits_whatsapps_limits():
    for name in MENU_NAMES:
        made = menu_template(name)
        kind = next(iter(made["types"]))
        if kind == "twilio/quick-reply":
            buttons = made["types"][kind]["actions"]
            assert len(buttons) <= 3 and all(len(b["title"]) <= 20 for b in buttons), name
        else:
            items = made["types"][kind]["items"]
            assert len(items) <= 10 and all(len(i["item"]) <= 24 for i in items), name


def test_every_menu_choice_is_a_command_jack_knows():
    for name in MENU_NAMES:
        made = menu_template(name)
        kind = next(iter(made["types"]))
        choices = made["types"][kind].get("actions") or made["types"][kind]["items"]
        assert all(whatsapp.is_new_command(choice["id"]) for choice in choices), name


def test_the_main_menu_opens_each_group():
    assert [button["id"] for button in MAIN_MENU] == ["MENU JOBS", "MENU MONEY", "MENU MORE"]


def test_weekly_template_follows_whatsapps_rules():
    body = WEEKLY["body"]
    # WhatsApp rejects a template that starts or ends with a variable.
    assert not body.startswith("{{") and not body.endswith("}}")
    assert all(whatsapp.is_new_command(button["id"]) for button in WEEKLY["actions"])


def test_accepted_template_follows_whatsapps_rules():
    body = ACCEPTED["body"]
    assert not body.startswith("{{") and not body.endswith("}}")
    assert all(whatsapp.is_new_command(button["id"]) for button in ACCEPTED["actions"])
    assert set(accepted_template()["variables"]) == {"1", "2", "3"}
