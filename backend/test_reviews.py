from datetime import datetime, timedelta

import alerts
import database
import reviews
from diary import UK
from test_main import OUR_NUMBER, use_fakes

CUSTOMER = "+447700900123"


def done_job(review_ok=True, hours_ago=24, link="https://g.page/r/test-review"):
    """Save a job done hours_ago hours back, whose accepted quote did or didn't say yes to reviews."""
    database.save_business_details(
        1, legal_name="Test", address="1 Test Street", ref_letters="TP", bank_details="Bank", review_link=link
    )
    job_id = database.save_job(1, "Mrs Jones", "Radiator", customer_phone=CUSTOMER)
    quote_id = database.save_quote(1, "Mrs Jones", "Radiator", "RAD", 45000, job_id=job_id)
    database.finish_quote(1, quote_id)
    database.accept_quote(quote_id, review_ok=review_ok)
    database.mark_job_done(job_id)
    with database.Session(database.engine) as session:
        job = session.get(database.Job, job_id)
        job.done_at = database.now_utc() - timedelta(hours=hours_ago)
        session.add(job)
        session.commit()
    return job_id


def run_at(monkeypatch, hour):
    """Run the hourly check as if it were this hour in the UK, and return the texts sent."""
    texts = []

    def fake_send_sms(to, body, from_number):
        assert from_number == OUR_NUMBER
        texts.append((to, body))

    monkeypatch.setattr(alerts, "send_sms", fake_send_sms)
    monkeypatch.setattr(reviews, "uk_now", lambda now: datetime(2026, 11, 24, hour, tzinfo=UK))
    reviews.send_review_requests()
    return texts


def test_a_customer_who_said_yes_gets_one_request(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    done_job()

    first = run_at(monkeypatch, 11)
    second = run_at(monkeypatch, 12)

    [(to, body)] = first
    assert to == CUSTOMER
    assert "https://g.page/r/test-review" in body and "Reply STOP to opt out." in body
    assert second == []


def test_no_request_without_consent(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    done_job(review_ok=False)

    assert run_at(monkeypatch, 11) == []


def test_not_too_soon_after_the_job(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    done_job(hours_ago=5)

    assert run_at(monkeypatch, 11) == []


def test_not_at_night(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    done_job()

    assert run_at(monkeypatch, 22) == []


def test_no_request_without_a_review_link(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    done_job(link="")

    assert run_at(monkeypatch, 11) == []


def test_no_request_after_stop(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    done_job()
    database.opt_out(1, CUSTOMER)

    assert run_at(monkeypatch, 11) == []
