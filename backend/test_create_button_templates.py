from create_button_templates import TEMPLATES, template


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
