import csv

from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine
from twilio.request_validator import RequestValidator

import database
import main
import settings
from database import Client

client = TestClient(main.app)

# Ofcom's fictional numbers: reserved for testing, never belong to anyone
MOBILE_CALLER = "+447700900123"
OUR_NUMBER = "+447700900456"
OWNER = "+447700900789"


def use_fakes(monkeypatch, tmp_path, active=True):
    """Stop real texts, use a temporary database with one test client,
    and keep the call log in a temporary folder.

    Returns a list that collects every number we "texted".
    """
    texted_numbers = []

    def fake_send_sms(to, body, from_number):
        # Every text must come from the client's own Twilio number
        assert from_number == OUR_NUMBER
        texted_numbers.append(to)
        return "SM_FAKE"

    monkeypatch.setattr(main, "send_sms", fake_send_sms)
    monkeypatch.setattr(settings, "CALL_LOG_FILE", str(tmp_path / "calls.csv"))

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(database, "engine", engine)
    database.create_tables()
    with Session(engine) as session:
        session.add(
            Client(
                business_name="Test Plumbing",
                owner_mobile=OWNER,
                twilio_number=OUR_NUMBER,
                text_back_message="Hi, it's Test Plumbing. Sorry we missed your call.",
                active=active,
            )
        )
        session.commit()

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


def test_test_sms_is_gone():
    assert client.post("/test-sms").status_code == 404


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

    response = client.post(
        "/sms", data={"From": MOBILE_CALLER, "To": OUR_NUMBER, "Body": "spam"}
    )

    assert response.status_code == 403
    assert texted_numbers == []


def test_read_skip_numbers(monkeypatch):
    monkeypatch.setenv("SKIP_NUMBERS", " +447700900111, +447700900222 ,,")
    assert settings.read_skip_numbers() == ["+447700900111", "+447700900222"]

    monkeypatch.setenv("SKIP_NUMBERS", "")
    assert settings.read_skip_numbers() == []


def test_voice_does_not_text_family_and_friends(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "SKIP_NUMBERS", [MOBILE_CALLER])

    response = signed_call(MOBILE_CALLER)

    assert response.status_code == 200
    assert "Please try again later" in response.text
    # The caller is skipped, but the owner still hears about the missed call
    assert texted_numbers == [OWNER]
    assert read_log()[0]["texted"] == "False"


def test_owner_alert_for_family_and_friends(monkeypatch):
    monkeypatch.setattr(settings, "SKIP_NUMBERS", [MOBILE_CALLER])
    assert main.owner_alert(MOBILE_CALLER, False) == (
        f"Missed call from {MOBILE_CALLER} (family and friends, so no text was sent)."
    )


def test_voice_uses_the_clients_business_name(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    response = signed_call(MOBILE_CALLER)

    assert "Sorry, Test Plumbing can't take your call" in response.text


def test_voice_for_a_number_with_no_client(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)
    form = {"From": MOBILE_CALLER, "To": "+447700900999"}
    validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
    signature = validator.compute_signature(settings.PUBLIC_URL + "/voice", form)

    response = client.post(
        "/voice", data=form, headers={"X-Twilio-Signature": signature}
    )

    assert response.status_code == 200
    assert "isn't available right now" in response.text
    assert texted_numbers == []


def test_voice_for_a_paused_client(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path, active=False)

    response = signed_call(MOBILE_CALLER)

    assert "isn't available right now" in response.text
    assert texted_numbers == []


def test_sms_to_a_paused_client_is_not_forwarded(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path, active=False)

    response = signed_text(MOBILE_CALLER, "Hello?")

    assert response.status_code == 200
    assert texted_numbers == []
