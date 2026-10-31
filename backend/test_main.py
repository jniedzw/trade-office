import time
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select
from twilio.request_validator import RequestValidator

import database
import main
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


def signed_whatsapp(sender):
    """Send /whatsapp a fake message, signed the same way Twilio signs real ones."""
    form = {"From": f"whatsapp:{sender}", "To": f"whatsapp:{OUR_NUMBER}", "Body": "Hi"}
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
    assert "Test Plumbing is connected" in response.text
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

    assert main.whatsapp_reply(f"whatsapp:{OWNER}", " alerts on ").startswith("Done")
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
