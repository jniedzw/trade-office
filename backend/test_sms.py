from datetime import datetime, timezone
from types import SimpleNamespace

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
