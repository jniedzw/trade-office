import time
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select
from twilio.request_validator import RequestValidator

import database
import main
import menu
import quotes
import settings
from database import Call, Client

client = TestClient(main.app)

# Ofcom's fictional numbers: reserved for testing, never belong to anyone
MOBILE_CALLER = "+447700900123"
OUR_NUMBER = "+447700900456"
OWNER = "+447700900789"
JAMES = "+447700900321"


def use_fakes(monkeypatch, tmp_path, active=True, failing=()):
    """Stop real texts, use a temporary database with one test client,
    and keep the call log in a temporary folder.

    Texts to any number in failing raise an error, like Twilio does when it can't
    send. Returns a list that collects every number we "texted".
    """
    texted_numbers = []

    def fake_send_sms(to, body, from_number):
        # Every text must come from the client's own Twilio number
        assert from_number == OUR_NUMBER
        if to in failing:
            raise RuntimeError("Twilio is unhappy")
        texted_numbers.append(to)
        return "SM_FAKE"

    monkeypatch.setattr(main, "send_sms", fake_send_sms)
    monkeypatch.setattr(settings, "ALERT_MOBILE", JAMES)
    monkeypatch.setattr(settings, "WHATSAPP_NUMBER", OUR_NUMBER)
    monkeypatch.setattr(settings, "WHATSAPP_ALERT_TEMPLATE", "HX_FAKE")
    # Off unless a test turns it on, whatever the real .env says.
    monkeypatch.setattr(settings, "QUOTE_LINK_SECRET", None)
    # Tests must never reach the real AI, which costs money.
    monkeypatch.setattr(main, "read_quote", lambda details, typed=None: None)
    monkeypatch.setattr(main, "read_job", lambda details: None)
    monkeypatch.setattr(main, "change_quote", lambda quote, lines, change: None)
    monkeypatch.setattr(settings, "WHATSAPP_DRAFT_BUTTONS", None)
    monkeypatch.setattr(settings, "WHATSAPP_FINAL_BUTTONS", None)
    monkeypatch.setattr(settings, "WHATSAPP_MENU", None)
    monkeypatch.setattr(settings, "WHATSAPP_JOB_BUTTONS", None)
    # The cleanup job must never reach the real Twilio account from a test.
    monkeypatch.setattr(main, "count_old_twilio_records", lambda cutoff: (0, 0))

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
    """Return every call saved in the test database, oldest first."""
    with Session(database.engine) as session:
        return session.exec(select(Call).order_by(Call.id)).all()


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
    assert read_log() == []


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
    assert rows[0].caller == MOBILE_CALLER
    assert rows[0].client_id == 1  # linked to the test client
    assert rows[0].texted is True
    assert rows[0].time is not None
    assert rows[1].caller == "anonymous"
    assert rows[1].texted is False


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


def test_voice_does_not_text_family_and_friends(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)
    database.add_skip_number(1, MOBILE_CALLER)  # client 1 is the test client

    response = signed_call(MOBILE_CALLER)

    assert response.status_code == 200
    assert "Please try again later" in response.text
    # The caller is skipped, but the owner still hears about the missed call
    assert texted_numbers == [OWNER]
    assert read_log()[0].texted is False


def test_owner_alert_for_family_and_friends():
    assert main.owner_alert(MOBILE_CALLER, False, skipped=True) == (
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


def test_calls_to_an_unknown_number_are_not_logged(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    form = {"From": MOBILE_CALLER, "To": "+447700900999"}
    validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
    signature = validator.compute_signature(settings.PUBLIC_URL + "/voice", form)

    client.post("/voice", data=form, headers={"X-Twilio-Signature": signature})

    assert read_log() == []


def test_stop_opts_out_and_confirms(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)

    response = signed_text(MOBILE_CALLER, " stop ")

    assert response.status_code == 200
    # One confirmation to the customer, and the owner sees the message
    assert texted_numbers == [MOBILE_CALLER, OWNER]
    assert database.is_opted_out(1, MOBILE_CALLER) is True


def test_voice_does_not_text_someone_who_opted_out(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)
    database.opt_out(1, MOBILE_CALLER)

    response = signed_call(MOBILE_CALLER)

    assert "Please try again later" in response.text
    assert texted_numbers == [OWNER]
    assert read_log()[0].texted is False


def test_start_opts_back_in(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    database.opt_out(1, MOBILE_CALLER)

    signed_text(MOBILE_CALLER, "START")

    assert database.is_opted_out(1, MOBILE_CALLER) is False


def test_a_normal_reply_does_not_opt_out(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    signed_text(MOBILE_CALLER, "Please stop by on Monday")

    assert database.is_opted_out(1, MOBILE_CALLER) is False


def test_cleanup_runs_when_the_app_starts(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    old = database.now_utc() - timedelta(days=366)
    with Session(database.engine) as session:
        session.add(Call(client_id=1, caller=MOBILE_CALLER, texted=True, time=old))
        session.commit()

    # Using "with" runs the startup and shutdown code, which is what starts the job.
    with TestClient(main.app):
        for _ in range(50):  # It runs in the background, so give it up to 5 seconds
            if read_log() == []:
                break
            time.sleep(0.1)

    assert read_log() == []


def test_voice_when_the_customer_text_fails(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path, failing=[MOBILE_CALLER])

    response = signed_call(MOBILE_CALLER)

    assert response.status_code == 200
    # The caller isn't promised a text that never came
    assert "Please try again later" in response.text
    assert texted_numbers == [JAMES, OWNER]
    assert read_log()[0].texted is False


def test_owner_is_told_to_ring_back_when_the_customer_text_fails(monkeypatch, tmp_path):
    sent = []
    use_fakes(monkeypatch, tmp_path)

    def fail_for_caller(to, body, from_number):
        if to == MOBILE_CALLER:
            raise RuntimeError("Twilio is unhappy")
        sent.append(body)

    monkeypatch.setattr(main, "send_sms", fail_for_caller)

    signed_call(MOBILE_CALLER)

    # sent[0] is my alert; sent[1] is what the owner gets
    assert "please ring back" in sent[1]


def test_voice_when_the_owner_alert_fails(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path, failing=[OWNER])

    response = signed_call(MOBILE_CALLER)

    assert response.status_code == 200
    assert "We'll text you now" in response.text
    assert texted_numbers == [MOBILE_CALLER, JAMES]
    assert len(read_log()) == 1


def test_voice_when_every_text_fails(monkeypatch, tmp_path):
    texted_numbers = use_fakes(
        monkeypatch, tmp_path, failing=[MOBILE_CALLER, OWNER, JAMES]
    )

    response = signed_call(MOBILE_CALLER)

    assert response.status_code == 200
    assert texted_numbers == []
    assert read_log()[0].texted is False


def test_voice_when_the_call_cant_be_logged(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)

    def broken_log(client_id, caller, texted):
        raise RuntimeError("database is unhappy")

    monkeypatch.setattr(main, "log_call", broken_log)

    response = signed_call(MOBILE_CALLER)

    assert response.status_code == 200
    assert texted_numbers == [MOBILE_CALLER, OWNER, JAMES]


def test_sms_when_the_forward_fails(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path, failing=[OWNER])

    response = signed_text(MOBILE_CALLER, "Need a boiler looked at")

    assert response.status_code == 200
    assert texted_numbers == [JAMES]


def test_a_failed_stop_confirmation_still_opts_out(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path, failing=[MOBILE_CALLER])

    response = signed_text(MOBILE_CALLER, "STOP")

    assert response.status_code == 200
    assert database.is_opted_out(1, MOBILE_CALLER) is True
    assert texted_numbers == [JAMES, OWNER]


def test_no_alert_text_when_alert_mobile_is_not_set(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path, failing=[OWNER])
    monkeypatch.setattr(settings, "ALERT_MOBILE", None)

    response = signed_call(MOBILE_CALLER)

    assert response.status_code == 200
    assert texted_numbers == [MOBILE_CALLER]


def test_logs_never_include_the_callers_number(monkeypatch, tmp_path, capsys):
    use_fakes(monkeypatch, tmp_path, failing=[MOBILE_CALLER, OWNER, JAMES])

    signed_call(MOBILE_CALLER)

    assert MOBILE_CALLER not in capsys.readouterr().out


def add_call(caller, hours_ago, texted=True):
    """Save a past call, for tests that care about when it happened."""
    time = database.now_utc() - timedelta(hours=hours_ago)
    with Session(database.engine) as session:
        session.add(Call(client_id=1, caller=caller, texted=texted, time=time))
        session.commit()


def test_a_repeat_caller_only_gets_one_text(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)

    signed_call(MOBILE_CALLER)
    response = signed_call(MOBILE_CALLER)

    assert "Please try again later" in response.text
    # The caller once, and the owner hears about both calls
    assert texted_numbers == [MOBILE_CALLER, OWNER, OWNER]
    assert [row.texted for row in read_log()] == [True, False]


def test_a_caller_can_be_texted_again_after_24_hours(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)
    add_call(MOBILE_CALLER, hours_ago=25)

    signed_call(MOBILE_CALLER)

    assert texted_numbers == [MOBILE_CALLER, OWNER]


def test_a_failed_text_is_retried_on_the_next_call(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)
    add_call(MOBILE_CALLER, hours_ago=1, texted=False)

    signed_call(MOBILE_CALLER)

    assert texted_numbers == [MOBILE_CALLER, OWNER]


def test_owner_alerts_stop_after_three_calls(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)

    for _ in range(6):
        response = signed_call(MOBILE_CALLER)
        assert response.status_code == 200

    # One text-back, and the owner hears about the first three calls only
    assert texted_numbers == [MOBILE_CALLER, OWNER, OWNER, OWNER]
    assert len(read_log()) == 6  # every call is still logged


def test_family_and_friends_alerts_never_stop(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)
    database.add_skip_number(1, MOBILE_CALLER)

    for _ in range(6):
        signed_call(MOBILE_CALLER)

    assert texted_numbers == [OWNER] * 6


def test_withheld_alerts_never_stop(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)

    for _ in range(6):
        signed_call("anonymous")

    assert texted_numbers == [OWNER] * 6


def test_owner_alert_for_a_repeat_caller():
    assert main.owner_alert(MOBILE_CALLER, False, repeat=True) == (
        f"Missed call from {MOBILE_CALLER} again. We texted them in the last 24 hours."
    )


def signed_whatsapp(sender, body="Hi", button=None, list_id=None):
    """Send /whatsapp a fake message, signed the same way Twilio signs real ones."""
    form = {"From": f"whatsapp:{sender}", "To": f"whatsapp:{OUR_NUMBER}", "Body": body}
    if button:
        form["ButtonPayload"] = button
    if list_id:
        form["ListId"] = list_id
    validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
    signature = validator.compute_signature(settings.PUBLIC_URL + "/whatsapp", form)
    return client.post(
        "/whatsapp", data=form, headers={"X-Twilio-Signature": signature}
    )


def test_whatsapp_recognises_a_client(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)

    response = signed_whatsapp(OWNER)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/xml"
    assert "Things you can send me" in response.text
    assert texted_numbers == []  # the reply goes back in the response, not by SMS


def test_whatsapp_for_someone_who_is_not_a_client(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    response = signed_whatsapp(MOBILE_CALLER)

    assert "askjack.uk" in response.text
    assert "Test Plumbing" not in response.text


def test_whatsapp_for_a_paused_client(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path, active=False)

    response = signed_whatsapp(OWNER)

    assert "Test Plumbing" not in response.text


def test_whatsapp_rejects_missing_signature(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    response = client.post("/whatsapp", data={"From": f"whatsapp:{OWNER}"})

    assert response.status_code == 403


def use_whatsapp_alerts(monkeypatch, failing=False):
    """Opt the test client in, and return a list of WhatsApp alerts "sent"."""
    sent = []

    def fake_send_whatsapp_alert(to, who, status):
        if failing:
            raise RuntimeError("Twilio is unhappy")
        sent.append((to, who, status))
        return f"SM_WHATSAPP_{len(sent)}"

    monkeypatch.setattr(main, "send_whatsapp_alert", fake_send_whatsapp_alert)
    database.set_whatsapp_alerts(1, True)
    return sent


def signed_status(message_sid, status):
    """Send /whatsapp-status a delivery update, signed like Twilio's."""
    form = {"MessageSid": message_sid, "MessageStatus": status}
    validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
    signature = validator.compute_signature(
        settings.PUBLIC_URL + "/whatsapp-status", form
    )
    return client.post(
        "/whatsapp-status", data=form, headers={"X-Twilio-Signature": signature}
    )


def test_alerts_stay_on_sms_until_the_owner_opts_in(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)
    sent = use_whatsapp_alerts(monkeypatch)
    database.set_whatsapp_alerts(1, False)

    signed_call(MOBILE_CALLER)

    assert sent == []
    assert texted_numbers == [MOBILE_CALLER, OWNER]


def test_alerts_stay_on_sms_until_the_template_is_set(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)
    sent = use_whatsapp_alerts(monkeypatch)
    monkeypatch.setattr(settings, "WHATSAPP_ALERT_TEMPLATE", None)

    signed_call(MOBILE_CALLER)

    assert sent == []
    assert texted_numbers == [MOBILE_CALLER, OWNER]


def test_opted_in_owner_gets_a_whatsapp_alert(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)
    sent = use_whatsapp_alerts(monkeypatch)

    signed_call(MOBILE_CALLER)

    assert sent == [(OWNER, MOBILE_CALLER, "we texted them back")]
    assert texted_numbers == [MOBILE_CALLER]  # no SMS alert as well


def test_a_rejected_whatsapp_alert_goes_by_sms_straight_away(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)
    use_whatsapp_alerts(monkeypatch, failing=True)

    signed_call(MOBILE_CALLER)

    assert texted_numbers == [MOBILE_CALLER, OWNER]


def test_a_delivered_alert_needs_no_sms(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)
    use_whatsapp_alerts(monkeypatch)
    signed_call(MOBILE_CALLER)

    response = signed_status("SM_WHATSAPP_1", "delivered")
    signed_status("SM_WHATSAPP_1", "failed")  # a late update mustn't send an SMS

    assert response.status_code == 204
    assert texted_numbers == [MOBILE_CALLER]
    assert database.pop_pending_alert("SM_WHATSAPP_1") is None


def test_a_failed_alert_goes_by_sms_once(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)
    use_whatsapp_alerts(monkeypatch)
    signed_call(MOBILE_CALLER)

    signed_status("SM_WHATSAPP_1", "failed")
    signed_status("SM_WHATSAPP_1", "undelivered")

    assert texted_numbers == [MOBILE_CALLER, OWNER]


def test_sent_is_not_treated_as_delivered(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    use_whatsapp_alerts(monkeypatch)
    signed_call(MOBILE_CALLER)

    signed_status("SM_WHATSAPP_1", "sent")

    assert database.overdue_alert_sids() == []  # still waiting, not forgotten
    assert database.pop_pending_alert("SM_WHATSAPP_1") is not None


def test_an_overdue_alert_goes_by_sms(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)
    use_whatsapp_alerts(monkeypatch)
    signed_call(MOBILE_CALLER)
    with Session(database.engine) as session:
        row = session.exec(select(database.PendingAlert)).first()
        row.time = database.now_utc() - timedelta(minutes=3)
        session.add(row)
        session.commit()

    main.send_overdue_alerts()
    main.send_overdue_alerts()

    assert texted_numbers == [MOBILE_CALLER, OWNER]


def test_a_recent_alert_is_left_alone(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)
    use_whatsapp_alerts(monkeypatch)
    signed_call(MOBILE_CALLER)

    main.send_overdue_alerts()

    assert texted_numbers == [MOBILE_CALLER]


def test_whatsapp_status_rejects_missing_signature(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    response = client.post(
        "/whatsapp-status", data={"MessageSid": "SM1", "MessageStatus": "failed"}
    )

    assert response.status_code == 403


def test_alerts_on_and_off_by_whatsapp(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", " alerts on ")
    assert text.startswith("Done")
    assert link is None
    assert database.whatsapp_alerts_on(1) is True

    main.whatsapp_reply(f"whatsapp:{OWNER}", "ALERTS OFF")
    assert database.whatsapp_alerts_on(1) is False


def test_a_stranger_cant_turn_alerts_on(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    main.whatsapp_reply(f"whatsapp:{MOBILE_CALLER}", "ALERTS ON")

    assert database.whatsapp_alerts_on(1) is False


def test_whatsapp_alert_details():
    assert main.whatsapp_alert_details("anonymous", False, False, False) == (
        "a withheld number",
        "no number to text",
    )
    assert main.whatsapp_alert_details("+266696687", False, False, False)[0] == (
        "a withheld number"
    )
    assert main.whatsapp_alert_details(MOBILE_CALLER, False, False, True) == (
        MOBILE_CALLER,
        "we texted them in the last 24 hours",
    )
    assert "ring back" in main.whatsapp_alert_details(
        "+441514960000", False, False, False
    )[1]


def test_owner_can_save_a_quote_by_whatsapp(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    reply = main.whatsapp_reply(
        f"whatsapp:{OWNER}", "quote Mrs Jones | Bathroom refit | BR | 1250.50"
    )

    assert reply == main.Reply(
        "Draft quote 1 saved: Bathroom refit for Mrs Jones, £1,250.50, 0 lines of work. "
        "Check it, then send FINAL 1 for the version to send."
    )
    assert database.get_quote(1).total_pence == 125050


def test_a_badly_written_quote_gets_help(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    reply = main.whatsapp_reply(f"whatsapp:{OWNER}", "QUOTE bathroom about 1250")

    assert reply == main.Reply(main.QUOTE_HELP)
    assert database.get_quote(1) is None


def test_a_stranger_cant_make_a_quote(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    reply = main.whatsapp_reply(
        f"whatsapp:{MOBILE_CALLER}", "QUOTE Mrs Jones | Bathroom | BR | 100"
    )

    assert reply == main.Reply(main.WHATSAPP_NOT_A_CLIENT)
    assert database.get_quote(1) is None


def use_quote_links(monkeypatch, tmp_path):
    """Set a test secret and save one draft quote, ID 1."""
    use_fakes(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "QUOTE_LINK_SECRET", "test-secret")
    database.save_quote(1, "Mrs Jones", "Bathroom refit", "BR", 125050)


def path_of(link):
    """Return the link without PUBLIC_URL, which is how TestClient wants it."""
    return link.removeprefix(settings.PUBLIC_URL)


def test_a_signed_link_downloads_the_quote(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    link = quotes.signed_link("quote", 1, database.now_utc())

    response = client.get(path_of(link))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")


def test_a_link_cant_be_changed_to_another_quote(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    database.save_quote(1, "Mr Smith", "Boiler service", "BS", 8500)
    link = quotes.signed_link("quote", 1, database.now_utc())

    response = client.get(path_of(link).replace("/quotes/1.pdf", "/quotes/2.pdf"))

    assert response.status_code == 403


def test_an_old_link_stops_working(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    eight_days_ago = database.now_utc() - timedelta(days=8)
    link = quotes.signed_link("quote", 1, eight_days_ago)

    assert client.get(path_of(link)).status_code == 403


def test_a_link_without_a_signature_is_refused(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)

    assert client.get("/quotes/1.pdf").status_code == 403


def test_no_links_work_without_a_secret(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    link = quotes.signed_link("quote", 1, database.now_utc())
    monkeypatch.setattr(settings, "QUOTE_LINK_SECRET", None)

    assert client.get(path_of(link)).status_code == 403


def test_a_quote_reply_links_to_its_pdf(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "QUOTE_LINK_SECRET", "test-secret")

    text, link, buttons = main.whatsapp_reply(
        f"whatsapp:{OWNER}", "QUOTE Mrs Jones | Bathroom refit | BR | 1250"
    )

    assert text.startswith("Draft quote 1 saved")
    assert link.startswith(f"{settings.PUBLIC_URL}/quotes/1.pdf?expires=")
    # The link in the reply really downloads the PDF.
    assert client.get(path_of(link)).content.startswith(b"%PDF-")


def test_whatsapp_reply_attaches_the_pdf(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "QUOTE_LINK_SECRET", "test-secret")

    response = signed_whatsapp(OWNER, "QUOTE Mrs Jones | Bathroom refit | BR | 1250")

    assert "<Media>" in response.text


def test_a_quote_with_lines_by_whatsapp(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = main.whatsapp_reply(
        f"whatsapp:{OWNER}",
        "QUOTE Mrs Jones | Bathroom refit | BR | 1250\n- Remove old suite\n- Fit basin",
    )

    assert "£1,250.00, 2 lines of work." in text
    assert database.get_quote_lines(1) == ["Remove old suite", "Fit basin"]


def test_quote_can_be_on_its_own_line(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = main.whatsapp_reply(
        f"whatsapp:{OWNER}", "Quote\nMrs Jones | Bathroom refit | BR | 1250"
    )

    assert text.startswith("Draft quote 1 saved")


def test_a_rough_quote_is_read_by_the_ai(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    quote = {
        "customer_name": "Mrs Jones",
        "title": "Bathroom refit",
        "job_code": "BR",
        "total_pence": 125000,
        "lines": ["Remove old suite"],
    }
    monkeypatch.setattr(main, "read_quote", lambda details: quote)

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "quote bathroom mrs jones 1250")

    assert text.startswith("Draft quote 1 saved: Bathroom refit for Mrs Jones")
    assert database.get_quote_lines(1) == ["Remove old suite"]


def test_final_makes_a_quote_final(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "final 1")

    assert text == "Quote 1 is final. Forward this PDF to Mrs Jones."
    assert link.startswith(f"{settings.PUBLIC_URL}/quotes/1.pdf")
    assert database.get_quote(1).status == "final"


def test_final_needs_a_number(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "FINAL one")

    assert text.startswith("To finish a quote")
    assert link is None
    assert database.get_quote(1).status == "draft"


def test_final_for_a_quote_that_doesnt_exist(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "FINAL 9")

    assert text == "There's no quote 9 for Test Plumbing."
    assert link is None


def test_a_client_cant_finish_another_clients_quote(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    other_owner = "+447700900790"
    with Session(database.engine) as session:
        session.add(
            Client(
                business_name="Other Plumbing",
                owner_mobile=other_owner,
                twilio_number="+447700900457",
                text_back_message="Hi",
            )
        )
        session.commit()

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{other_owner}", "FINAL 1")

    assert text == "There's no quote 1 for Other Plumbing."
    assert database.get_quote(1).status == "draft"


ADDRESS = "\n12 Test Road\nLiverpool L1 0AA"


def use_final_quote(monkeypatch, tmp_path, with_details=True):
    """Save quote 1 as final, and the test client's business details."""
    use_quote_links(monkeypatch, tmp_path)
    database.finish_quote(1, 1)
    if with_details:
        database.save_business_details(
            1, legal_name="Test", address="1 Test Street", ref_letters="TP", bank_details="Test Bank"
        )


def test_invoice_saves_an_invoice(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "invoice 1" + ADDRESS)

    assert text == "Invoice 1 saved for Mrs Jones, £1,250.50. Forward this PDF to them."
    assert link.startswith(f"{settings.PUBLIC_URL}/invoices/1.pdf?expires=")
    assert client.get(path_of(link)).content.startswith(b"%PDF-")
    invoice = database.get_invoice_for_quote(1)
    assert invoice.customer_address == "12 Test Road\nLiverpool L1 0AA"


def test_invoicing_a_quote_twice_gives_the_same_invoice(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)

    main.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 1" + ADDRESS)
    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 1" + ADDRESS)

    assert text.startswith("Invoice 1 saved")


def test_invoice_numbers_go_up_by_one(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)
    database.save_quote(1, "Mr Smith", "Boiler service", "BS", 8500)
    database.finish_quote(1, 2)

    main.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 1" + ADDRESS)
    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 2" + ADDRESS)

    assert text.startswith("Invoice 2 saved for Mr Smith, £85.00.")


def test_invoice_without_an_address_asks_for_one(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 1")
    assert text.startswith("What's Mrs Jones's address?")
    assert database.get_invoice_for_quote(1) is None

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "12 Test Road\nLiverpool")
    assert text.startswith("Invoice 1 saved for Mrs Jones")
    assert database.get_invoice_for_quote(1).customer_address == "12 Test Road\nLiverpool"


def test_a_command_instead_of_an_answer_forgets_the_question(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)
    main.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 1")

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "QUOTE Mr Smith | Boiler | BS | 85")
    assert text.startswith("Draft quote 2 saved")

    # The question's gone, so an address now is just a message, not an invoice.
    main.whatsapp_reply(f"whatsapp:{OWNER}", "12 Test Road")
    assert database.get_invoice_for_quote(1) is None


def test_an_old_question_is_forgotten(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)
    database.ask_question(1, "invoice_address", 1)
    with Session(database.engine) as session:
        row = session.exec(select(database.PendingQuestion)).first()
        row.time = database.now_utc() - timedelta(hours=25)
        session.add(row)
        session.commit()

    main.whatsapp_reply(f"whatsapp:{OWNER}", "12 Test Road")

    assert database.get_invoice_for_quote(1) is None


def test_make_invoice_on_an_invoiced_quote_resends_it(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)
    main.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 1" + ADDRESS)

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 1")

    assert text.startswith("Invoice 1 saved")


def test_a_draft_cant_be_invoiced(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 1" + ADDRESS)

    assert text == "Quote 1 is still a draft. Send FINAL 1 first."


def test_invoice_without_business_details_alerts_james(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "QUOTE_LINK_SECRET", "test-secret")
    database.save_quote(1, "Mrs Jones", "Bathroom refit", "BR", 125050)
    database.finish_quote(1, 1)

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 1" + ADDRESS)

    assert text.startswith("Your invoice details aren't set up yet")
    assert texted_numbers == [JAMES]
    assert database.get_invoice_for_quote(1) is None


def test_a_client_cant_invoice_another_clients_quote(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)
    other_owner = "+447700900790"
    with Session(database.engine) as session:
        session.add(
            Client(
                business_name="Other Plumbing",
                owner_mobile=other_owner,
                twilio_number="+447700900457",
                text_back_message="Hi",
            )
        )
        session.commit()

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{other_owner}", "INVOICE 1" + ADDRESS)

    assert text == "There's no quote 1 for Other Plumbing."
    assert database.get_invoice_for_quote(1) is None


def test_a_quote_link_cant_open_an_invoice(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)
    main.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 1" + ADDRESS)
    quote_link = quotes.signed_link("quote", 1, database.now_utc())

    response = client.get(path_of(quote_link).replace("/quotes/", "/invoices/"))

    assert response.status_code == 403


def test_a_tapped_button_runs_its_command(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)

    # The button's title is "Make final", but its hidden ID is the command.
    response = signed_whatsapp(OWNER, body="Make final", button="FINAL 1")

    assert "Quote 1 is final" in response.text
    assert database.get_quote(1).status == "final"


def use_buttons(monkeypatch, failing=False):
    """Turn button templates on, and return a list of WhatsApp messages "sent"."""
    sent = []
    monkeypatch.setattr(settings, "WHATSAPP_DRAFT_BUTTONS", "HX_DRAFT")
    monkeypatch.setattr(settings, "WHATSAPP_FINAL_BUTTONS", "HX_FINAL")

    def fake_send_whatsapp(to, body="", media_url=None):
        sent.append(("pdf", media_url is not None))

    def fake_send_whatsapp_buttons(to, template, variables):
        if failing:
            raise RuntimeError("Twilio is unhappy")
        sent.append((template, variables.get("2")))

    monkeypatch.setattr(main, "send_whatsapp", fake_send_whatsapp)
    monkeypatch.setattr(main, "send_whatsapp_buttons", fake_send_whatsapp_buttons)
    return sent


def test_a_draft_quote_gets_make_final_buttons(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    sent = use_buttons(monkeypatch)

    response = signed_whatsapp(OWNER, "QUOTE Mr Smith | Boiler service | BS | 85")

    # The PDF first, then the buttons for the new quote, and nothing in the response.
    assert sent == [("pdf", True), ("HX_DRAFT", "2")]
    assert "<Message>" not in response.text


def test_a_final_quote_gets_make_invoice_buttons(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    sent = use_buttons(monkeypatch)

    signed_whatsapp(OWNER, "FINAL 1")

    assert sent == [("pdf", True), ("HX_FINAL", "1")]


def test_a_plain_reply_if_buttons_fail(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    use_buttons(monkeypatch, failing=True)

    response = signed_whatsapp(OWNER, "FINAL 1")

    # The PDF already went, so the plain reply is just the words.
    assert "Quote 1 is final" in response.text
    assert "<Media>" not in response.text


def test_quotes_stop_at_the_daily_limit(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "MAX_QUOTES_PER_DAY", 2)
    for _ in range(2):
        main.whatsapp_reply(f"whatsapp:{OWNER}", "QUOTE Mr Smith | Boiler | BS | 85")

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "QUOTE Mr Smith | Boiler | BS | 85")

    assert text.startswith("That's 2 quotes today")
    assert database.get_quote(3) is None
    assert texted_numbers == [JAMES]


def use_fake_change(monkeypatch, changed):
    """Make the AI's change return changed, without calling the real AI."""
    monkeypatch.setattr(main, "change_quote", lambda quote, lines, change: changed)


CHANGED = {
    "customer_name": "Mrs Jones",
    "title": "Bathroom refit",
    "job_code": "BR",
    "total_pence": 130000,
    "lines": ["Remove old suite", "Tile the floor"],
}


def test_change_updates_a_draft(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    use_fake_change(monkeypatch, CHANGED)

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "change 1 price is 1300, add tiling")

    assert text.startswith("Draft quote 1 changed: Bathroom refit for Mrs Jones, £1,300.00, 2 lines")
    assert link.startswith(f"{settings.PUBLIC_URL}/quotes/1.pdf")
    assert database.get_quote(1).total_pence == 130000
    assert database.get_quote_lines(1) == ["Remove old suite", "Tile the floor"]


def test_tapping_change_asks_what_to_change(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    use_fake_change(monkeypatch, CHANGED)

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "CHANGE 1")
    assert text == "What needs changing on quote 1?"

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "price is 1300")
    assert text.startswith("Draft quote 1 changed")


def test_changing_a_final_quote_makes_it_a_draft(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    use_fake_change(monkeypatch, CHANGED)
    database.finish_quote(1, 1)

    main.whatsapp_reply(f"whatsapp:{OWNER}", "CHANGE 1 price is 1300")

    assert database.get_quote(1).status == "draft"


def test_an_invoiced_quote_cant_change(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)
    use_fake_change(monkeypatch, CHANGED)
    main.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 1" + ADDRESS)

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "CHANGE 1 price is 1300")

    assert "can't change" in text
    assert database.get_quote(1).total_pence == 125050


def test_a_change_the_ai_cant_make(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    use_fake_change(monkeypatch, None)

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "CHANGE 1 make it cheaper")

    assert text.startswith("Sorry, I couldn't make that change")
    assert database.get_quote(1).total_pence == 125050


def test_a_client_cant_change_another_clients_quote(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    use_fake_change(monkeypatch, CHANGED)
    other_owner = "+447700900790"
    with Session(database.engine) as session:
        session.add(
            Client(
                business_name="Other Plumbing",
                owner_mobile=other_owner,
                twilio_number="+447700900457",
                text_back_message="Hi",
            )
        )
        session.commit()

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{other_owner}", "CHANGE 1 price 1300")

    assert text == "There's no quote 1 for Other Plumbing."
    assert database.get_quote(1).total_pence == 125050


def test_saving_changing_and_listing_prices(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "price Boiler Service 85")
    assert text == "Saved: boiler service, £85.00."
    main.whatsapp_reply(f"whatsapp:{OWNER}", "PRICE boiler service £90")
    main.whatsapp_reply(f"whatsapp:{OWNER}", "PRICE gas safety cert 65")

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "PRICES")
    assert text == "Your prices:\nboiler service: £90.00\ngas safety cert: £65.00"


def test_deleting_a_price(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    main.whatsapp_reply(f"whatsapp:{OWNER}", "PRICE boiler service 85")

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "PRICE boiler service delete")
    assert text == "Deleted the price for boiler service."
    assert database.get_prices(1) == []

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "PRICE boiler service DELETE")
    assert text.startswith("There's no price for boiler service")


def test_a_badly_written_price_gets_help(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    for message in ["PRICE", "PRICE 85", "PRICE boiler service", "PRICE boiler service abc"]:
        text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", message)
        assert text == main.PRICE_HELP, message
    assert database.get_prices(1) == []


def test_no_prices_yet(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "PRICES")

    assert text.startswith("You haven't saved any prices yet.")


def test_is_new_command():
    assert main.is_new_command("QUOTE MRS JONES | BATHROOM | BR | 1250") is True
    assert main.is_new_command("FINAL 6") is True
    assert main.is_new_command("CHANGE 6 PRICE IS 1300") is True
    assert main.is_new_command("PRICES") is True
    # Answers that happen to start with a command's word.
    assert main.is_new_command("CHANGE THE PRICE TO 1300") is False
    assert main.is_new_command("PRICE IS 1300") is False
    assert main.is_new_command("FINAL PRICE 1300") is False
    assert main.is_new_command("12 ELM ROAD") is False


def test_hi_gets_the_menu(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    sent = use_buttons(monkeypatch)
    monkeypatch.setattr(settings, "WHATSAPP_MENU", "HX_MENU")

    response = signed_whatsapp(OWNER, "Morning")

    assert sent == [("HX_MENU", None)]
    assert "<Message>" not in response.text


def test_without_the_menu_template_hi_gets_help(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "hello")

    assert text == main.HELP_TEXT


def test_new_quote_from_the_menu_asks_about_the_job(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    # The list menu's choice, by its ID or just its title.
    for tapped in ["QUOTE", "New quote"]:
        text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", tapped)
        assert text.startswith("Tell me about the job")

    text, link, buttons = main.whatsapp_reply(
        f"whatsapp:{OWNER}", "Mrs Jones | Bathroom refit | BR | 1250"
    )
    assert text.startswith("Draft quote 1 saved")


def test_a_list_choice_arrives_as_list_id(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    # The ID is the command, whatever the title says.
    response = signed_whatsapp(OWNER, body="Something else", list_id="PRICES")
    assert "haven't saved any prices" in response.text


def test_every_command_is_in_the_menu_or_marked_as_not():
    # If this fails, a new command needs adding to MENU_CHOICES or NOT_IN_MENU in menu.py.
    in_menu = {command for _, command, _ in menu.MENU_CHOICES}
    assert set(main.COMMANDS) == in_menu | set(menu.NOT_IN_MENU)


def test_log_a_typed_job(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = main.whatsapp_reply(
        f"whatsapp:{OWNER}", "LOG Mrs Jones | 12 Test Road, Liverpool | Leaking radiator"
    )

    assert text == "Job 1 logged: Mrs Jones, Leaking radiator.\nAddress: 12 Test Road, Liverpool"
    job = database.get_job(1)
    assert job.address == "12 Test Road\nLiverpool"
    assert job.status == "logged"


def test_log_a_job_from_the_menu(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    job = {
        "customer_name": "Mrs Jones",
        "work": "Leaking radiator",
        "customer_phone": "+447700900123",
        "address": None,
    }
    monkeypatch.setattr(main, "read_job", lambda details: job)

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "Log a job")
    assert text.startswith("Tell me about the job")

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "mrs jones leaking rad 07700 900123")
    assert text == "Job 1 logged: Mrs Jones, Leaking radiator.\nPhone: +447700900123"


def test_a_job_jack_cant_read_gets_help(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "LOG something")

    assert text == main.LOG_HELP
    assert database.get_job(1) is None


def test_alerts_needs_on_or_off(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "ALERTS please")

    assert text.startswith("Send ALERTS ON")


def log_test_job(address="12 Test Road\nLiverpool"):
    """Save job 1 for the test client, the way LOG would."""
    return database.save_job(1, "Mrs Jones", "Leaking radiator", address=address)


def test_quote_it_turns_a_job_into_a_quote(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    log_test_job()
    seen = []

    def fake_read_quote(details, typed=None):
        seen.append(typed)
        return {"customer_name": "Someone else", "title": "Radiator", "job_code": "RAD",
                "total_pence": 45000, "lines": ["Fit new radiator"]}

    monkeypatch.setattr(main, "read_quote", fake_read_quote)

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "QUOTE JOB 1")
    assert text.startswith("What's the price for Mrs Jones's job")

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", "450, fit new radiator")
    # Quote 2, because use_quote_links already saved quote 1.
    assert text.startswith("Draft quote 2 saved: Radiator for Mrs Jones, £450.00")
    # Only the tradesperson's answer is used for the price check.
    assert seen == ["450, fit new radiator"]
    assert database.get_quote(2).job_id == 1
    assert database.get_job(1).status == "quoted"


def test_invoicing_a_job_quote_uses_the_jobs_address(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)
    log_test_job()
    quote_id = database.save_quote(1, "Mrs Jones", "Radiator", "RAD", 45000, job_id=1)
    database.finish_quote(1, quote_id)

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{OWNER}", f"INVOICE {quote_id}")

    assert text.startswith("Invoice 1 saved for Mrs Jones")
    assert database.get_invoice_for_quote(quote_id).customer_address == "12 Test Road\nLiverpool"


def test_a_client_cant_quote_another_clients_job(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    log_test_job()
    other_owner = "+447700900790"
    with Session(database.engine) as session:
        session.add(
            Client(
                business_name="Other Plumbing",
                owner_mobile=other_owner,
                twilio_number="+447700900457",
                text_back_message="Hi",
            )
        )
        session.commit()

    text, link, buttons = main.whatsapp_reply(f"whatsapp:{other_owner}", "QUOTE JOB 1")

    assert text == "There's no job 1 for Other Plumbing."


def test_a_logged_job_gets_a_quote_it_button(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    sent = use_buttons(monkeypatch)
    monkeypatch.setattr(settings, "WHATSAPP_JOB_BUTTONS", "HX_JOB")

    signed_whatsapp(OWNER, "LOG Mrs Jones | Leaking radiator")

    assert sent == [("HX_JOB", "1")]
