import csv

from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

import main
import settings

client = TestClient(main.app)

# Ofcom's fictional numbers: reserved for testing, never belong to anyone
MOBILE_CALLER = "+447700900123"
OUR_NUMBER = "+447700900456"
OWNER = "+447700900789"


def use_fakes(monkeypatch, tmp_path):
    """Stop real texts, use a fake owner number and keep the call log in a temporary folder.

    Returns a list that collects every number we "texted".
    """
    texted_numbers = []

    def fake_send_sms(to, body):
        texted_numbers.append(to)
        return "SM_FAKE"

    monkeypatch.setattr(main, "send_sms", fake_send_sms)
    monkeypatch.setattr(settings, "CALL_LOG_FILE", str(tmp_path / "calls.csv"))
    monkeypatch.setattr(settings, "OWNER_MOBILE_NUMBER", OWNER)
    return texted_numbers


def signed_call(caller):
    """Send /voice a fake call, signed the same way Twilio signs real ones."""
    form = {"From": caller, "To": OUR_NUMBER}
    validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
    signature = validator.compute_signature(settings.PUBLIC_URL + "/voice", form)
    return client.post("/voice", data=form, headers={"X-Twilio-Signature": signature})


def signed_text(sender, body):
    """Send /sms a fake text message, signed the same way Twilio signs real ones."""
    form = {"From": sender, "To": OUR_NUMBER, "Body": body}
    validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
    signature = validator.compute_signature(settings.PUBLIC_URL + "/sms", form)
    return client.post("/sms", data=form, headers={"X-Twilio-Signature": signature})


def read_log():
    with open(settings.CALL_LOG_FILE) as file:
        return list(csv.DictReader(file))


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Trade Office is running"}


def test_can_text():
    assert main.can_text("+447700900123") is True
    assert main.can_text("anonymous") is False
    assert main.can_text("+266696687") is False
    assert main.can_text("+441514960000") is False
    assert main.can_text("+12025550123") is False


def test_test_sms_only_texts_our_own_mobile(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)

    response = client.post("/test-sms")

    assert response.status_code == 200
    assert response.json() == {"sid": "SM_FAKE"}
    assert texted_numbers == [settings.MY_MOBILE_NUMBER]


def test_test_sms_rejects_get():
    assert client.get("/test-sms").status_code == 405


def test_voice_rejects_missing_signature(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)

    response = client.post("/voice", data={"From": MOBILE_CALLER, "To": OUR_NUMBER})

    assert response.status_code == 403
    assert texted_numbers == []
    assert not (tmp_path / "calls.csv").exists()


def test_voice_rejects_wrong_signature(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)

    response = client.post(
        "/voice",
        data={"From": MOBILE_CALLER, "To": OUR_NUMBER},
        headers={"X-Twilio-Signature": "fake"},
    )

    assert response.status_code == 403
    assert texted_numbers == []


def test_voice_texts_back_a_mobile_caller(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)

    response = signed_call(MOBILE_CALLER)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/xml"
    assert "We'll text you now" in response.text
    assert "<Hangup />" in response.text
    # The caller gets the text-back, then the owner gets the alert
    assert texted_numbers == [MOBILE_CALLER, OWNER]


def test_voice_does_not_text_a_withheld_caller(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)

    response = signed_call("anonymous")

    assert response.status_code == 200
    assert "Please try again later" in response.text
    # Only the owner is alerted; there's no one to text back
    assert texted_numbers == [OWNER]


def test_owner_alert():
    assert main.owner_alert(MOBILE_CALLER, True) == (
        f"Missed call from {MOBILE_CALLER}. We've texted them back."
    )
    assert main.owner_alert("+441514960000", False) == (
        "Missed call from +441514960000. We couldn't text them, so please ring back."
    )
    assert main.owner_alert("anonymous", False) == "Missed call from a withheld number."
    assert (
        main.owner_alert("+266696687", False) == "Missed call from a withheld number."
    )


def test_voice_logs_each_call(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    signed_call(MOBILE_CALLER)
    signed_call("anonymous")

    rows = read_log()
    assert len(rows) == 2
    assert rows[0]["caller"] == MOBILE_CALLER
    assert rows[0]["called"] == OUR_NUMBER
    assert rows[0]["texted"] == "True"
    assert rows[1]["caller"] == "anonymous"
    assert rows[1]["texted"] == "False"


def test_forward_message():
    assert main.forward_message(MOBILE_CALLER, "Need a boiler looked at") == (
        f"From {MOBILE_CALLER}: Need a boiler looked at"
    )
    assert "picture or empty message" in main.forward_message(MOBILE_CALLER, "  ")


def test_sms_forwards_a_customer_reply_to_the_owner(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)

    response = signed_text(MOBILE_CALLER, "Need a boiler looked at")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/xml"
    assert "<Response />" in response.text
    assert texted_numbers == [OWNER]


def test_sms_does_not_forward_the_owners_own_texts(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)

    response = signed_text(OWNER, "booked Dave Monday 9am")

    assert response.status_code == 200
    assert texted_numbers == []


def test_sms_rejects_missing_signature(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)

    response = client.post("/sms", data={"From": MOBILE_CALLER, "Body": "spam"})

    assert response.status_code == 403
    assert texted_numbers == []
