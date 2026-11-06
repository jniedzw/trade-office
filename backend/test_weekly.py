from datetime import datetime, timedelta

import database
import settings
import weekly
from diary import UK
from test_main import OWNER, use_fakes

# A Sunday evening in the UK, when the update goes out.
SUNDAY_6PM = datetime(2026, 11, 8, 18, 0, tzinfo=UK)


def book(work, starts_at, ends_on=None):
    job_id = database.save_job(1, "Mrs Jones", work)
    database.book_job(job_id, starts_at, ends_on)


def test_week_summary_counts_next_weeks_jobs_and_whats_owed(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    book("Tonight, so not next week", SUNDAY_6PM + timedelta(hours=1))
    book("Monday", SUNDAY_6PM + timedelta(days=1))
    book("Next Sunday", SUNDAY_6PM + timedelta(days=7))
    book("Too far off", SUNDAY_6PM + timedelta(days=8))
    quote_id = database.save_quote(1, "Mrs Jones", "Bathroom", "BR", 125050)
    database.save_invoice(1, quote_id, "12 Test Road")

    assert weekly.week_summary(1, SUNDAY_6PM) == ("2 jobs", "£1,250.50")


def test_a_long_job_that_runs_into_next_week_counts(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    book("Bathroom", SUNDAY_6PM - timedelta(days=4), ends_on=SUNDAY_6PM.date() + timedelta(days=2))

    assert weekly.week_summary(1, SUNDAY_6PM) == ("1 job", "£0.00")


def use_weekly(monkeypatch, failing=False):
    """Turn the weekly template on, and return a list of updates "sent"."""
    sent = []
    monkeypatch.setattr(settings, "WHATSAPP_WEEKLY_TEMPLATE", "HX_WEEKLY")

    def fake_send_weekly_update(to, jobs, owed):
        if failing:
            raise RuntimeError("Twilio is unhappy")
        sent.append((to, jobs, owed))
        return f"SM_WEEKLY_{len(sent)}"

    monkeypatch.setattr(weekly, "send_weekly_update", fake_send_weekly_update)
    return sent


def test_updates_go_to_clients_with_whatsapp_alerts_on(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    sent = use_weekly(monkeypatch)

    weekly.send_weekly_updates()
    assert sent == []

    database.set_whatsapp_alerts(1, True)
    weekly.send_weekly_updates()
    assert sent == [(OWNER, "0 jobs", "£0.00")]
    # Kept until WhatsApp says it's delivered, so SMS can take over if it isn't.
    assert database.pop_pending_alert("SM_WEEKLY_1") is not None


def test_no_updates_until_the_template_is_set(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    sent = use_weekly(monkeypatch)
    monkeypatch.setattr(settings, "WHATSAPP_WEEKLY_TEMPLATE", None)
    database.set_whatsapp_alerts(1, True)

    weekly.send_weekly_updates()

    assert sent == []


def test_a_failed_update_goes_by_sms(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)
    use_weekly(monkeypatch, failing=True)
    database.set_whatsapp_alerts(1, True)

    weekly.send_weekly_updates()

    assert texted_numbers == [OWNER]
