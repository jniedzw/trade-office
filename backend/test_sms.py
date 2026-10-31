import json
from datetime import datetime, timezone
from types import SimpleNamespace

import settings
import sms

CUTOFF = datetime(2025, 10, 30, tzinfo=timezone.utc)


def test_count_old_twilio_records_only_counts(monkeypatch):
    filters = {}

    def fake_messages(**kwargs):
        filters.update(kwargs)
        return ["SM1", "SM2"]

    def fake_calls(**kwargs):
        filters.update(kwargs)
        return ["CA1"]

    # This fake has no delete, so the test fails if the dry run ever tries one.
    fake_client = SimpleNamespace(
        messages=SimpleNamespace(list=fake_messages),
        calls=SimpleNamespace(list=fake_calls),
    )
    monkeypatch.setattr(sms, "client", fake_client)

    assert sms.count_old_twilio_records(CUTOFF) == (2, 1)
    assert filters == {"date_sent_before": CUTOFF, "start_time_before": CUTOFF}


def test_send_whatsapp_alert_uses_the_template(monkeypatch):
    sent = {}

    def fake_create(**kwargs):
        sent.update(kwargs)
        return SimpleNamespace(sid="SM1")

    fake_client = SimpleNamespace(messages=SimpleNamespace(create=fake_create))
    monkeypatch.setattr(sms, "client", fake_client)
    monkeypatch.setattr(settings, "WHATSAPP_NUMBER", "+447700900001")
    monkeypatch.setattr(settings, "WHATSAPP_ALERT_TEMPLATE", "HX_FAKE")

    assert sms.send_whatsapp_alert("+447700900002", "+447700900003", "ok") == "SM1"
    assert sent["to"] == "whatsapp:+447700900002"
    assert sent["from_"] == "whatsapp:+447700900001"
    assert sent["content_sid"] == "HX_FAKE"
    assert json.loads(sent["content_variables"]) == {"1": "+447700900003", "2": "ok"}
    assert sent["status_callback"].endswith("/whatsapp-status")
