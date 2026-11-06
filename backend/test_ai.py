from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic
import httpx2

import ai
import database
import settings
from ai import (
    BookingDraft,
    JobDraft,
    PricedItem,
    QuoteDraft,
    change_quote,
    prices_in,
    read_booking,
    read_job,
    read_quote,
    uk_mobile,
)
from database import Quote
from test_database import use_temp_database

MESSAGE = "bathroom for mrs jones, rip out old suite, new basin and loo, 1,250 all in"


def use_fake_claude(monkeypatch, draft):
    """Make ask_claude return draft instead of calling the real AI."""
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai, "ask_claude", lambda message, system, shape: draft)


def good_draft(**changes):
    details = {
        "customer_name": "Mrs Jones",
        "title": "Bathroom refit",
        "job_code": "br",
        "price": "1,250",
        "items": [],
        "materials": None,
        "lines": ["Remove old suite", "Fit new basin and toilet", " "],
    }
    return QuoteDraft(**(details | changes))


def test_prices_in():
    assert prices_in("£1,250.50 or 300 for 2 rooms") == {125050, 30000, 200}


def test_read_quote_tidies_claudes_answer(monkeypatch):
    use_fake_claude(monkeypatch, good_draft())

    assert read_quote(MESSAGE) == {
        "customer_name": "Mrs Jones",
        "title": "Bathroom refit",
        "job_code": "BR",
        "total_pence": 125000,
        "lines": ["Remove old suite", "Fit new basin and toilet"],
    }


def test_read_quote_rejects_a_price_that_isnt_in_the_message(monkeypatch):
    use_fake_claude(monkeypatch, good_draft(price="1300"))

    assert read_quote(MESSAGE) is None


def test_read_quote_needs_a_price_and_a_customer(monkeypatch):
    use_fake_claude(monkeypatch, good_draft(price=None))
    assert read_quote(MESSAGE) is None

    use_fake_claude(monkeypatch, good_draft(customer_name=" "))
    assert read_quote(MESSAGE) is None


def test_read_quote_replaces_a_bad_job_code(monkeypatch):
    use_fake_claude(monkeypatch, good_draft(job_code="B-R"))

    assert read_quote(MESSAGE)["job_code"] == "JOB"


def test_read_quote_when_claude_refuses(monkeypatch):
    use_fake_claude(monkeypatch, None)

    assert read_quote(MESSAGE) is None


def test_read_quote_when_the_ai_is_down(monkeypatch, capsys):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key")

    def broken(message, system, shape):
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        raise anthropic.APIConnectionError(request=request)

    monkeypatch.setattr(ai, "ask_claude", broken)

    assert read_quote(MESSAGE) is None
    # The log names the error but never the customer.
    assert "Jones" not in capsys.readouterr().out


def test_read_quote_without_a_key_never_calls_claude(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)

    def must_not_run(message, system, shape):
        raise AssertionError("called Claude")

    monkeypatch.setattr(ai, "ask_claude", must_not_run)

    assert read_quote(MESSAGE) is None


def saved_quote():
    return Quote(
        id=1,
        client_id=1,
        customer_name="Mrs Jones",
        title="Bathroom refit",
        job_code="BR",
        total_pence=125000,
    )


def test_change_quote_can_take_a_new_price_from_the_change(monkeypatch):
    use_fake_claude(monkeypatch, good_draft(price="1300"))

    changed = change_quote(saved_quote(), ["Remove old suite"], "price is 1,300 now")

    assert changed["total_pence"] == 130000


def test_change_quote_can_keep_the_old_price(monkeypatch):
    use_fake_claude(monkeypatch, good_draft(price="1250.00"))

    changed = change_quote(saved_quote(), ["Remove old suite"], "add tiling the floor")

    assert changed["total_pence"] == 125000


def test_change_quote_rejects_an_invented_price(monkeypatch):
    use_fake_claude(monkeypatch, good_draft(price="1400"))

    assert change_quote(saved_quote(), ["Remove old suite"], "add tiling the floor") is None


def test_uk_mobile_must_be_in_the_message():
    message = "mrs jones 07700 900123 leaking rad"
    assert uk_mobile("07700 900123", message) == "+447700900123"
    assert uk_mobile("+44 7700 900123", "call +44 7700 900123") == "+447700900123"
    # Made up by the AI, or not a mobile.
    assert uk_mobile("07700 900999", message) is None
    assert uk_mobile("0151 496 0000", "0151 496 0000") is None


def test_read_job_drops_a_made_up_address(monkeypatch):
    draft = JobDraft(
        customer_name="Mrs Jones",
        customer_phone=None,
        address="99 Made Up Street",
        work="Leaking radiator",
    )
    use_fake_claude(monkeypatch, draft)

    job = read_job("mrs jones leaking rad")

    assert job == {
        "customer_name": "Mrs Jones",
        "work": "Leaking radiator",
        "customer_phone": None,
        "address": None,
    }


def test_read_quote_checks_the_price_against_only_what_was_typed(monkeypatch):
    use_fake_claude(monkeypatch, good_draft(price="3"))

    # "3" is in the job's description, but not in what the tradesperson typed.
    assert read_quote("For Mrs Jones: 3 radiators. 1,250 all in", typed="1,250 all in") is None


THURSDAY = datetime(2026, 11, 5, 18, 0, tzinfo=ZoneInfo("Europe/London"))


def test_read_booking(monkeypatch):
    use_fake_claude(monkeypatch, BookingDraft(date="2026-11-09", time="09:00", end_date=None))

    starts_at, ends_on = read_booking("monday 9am", THURSDAY)

    assert starts_at == datetime(2026, 11, 9, 9, 0, tzinfo=ZoneInfo("Europe/London"))
    assert ends_on is None


def test_read_booking_rejects_a_bad_date(monkeypatch):
    use_fake_claude(monkeypatch, BookingDraft(date="2026-11-02", time=None, end_date=None))
    assert read_booking("last monday", THURSDAY) is None

    use_fake_claude(monkeypatch, BookingDraft(date="soon", time=None, end_date=None))
    assert read_booking("soon", THURSDAY) is None


class FakeResponse:
    """Just the parts of Claude's response that record() reads."""

    model = "claude-sonnet-5-5"

    class usage:
        input_tokens = 900
        output_tokens = 120


def test_record_logs_the_cost_against_the_client(monkeypatch, tmp_path):
    use_temp_database(monkeypatch, tmp_path)
    ai.current_client_id.set(1)

    ai.record(ai.JOB_INSTRUCTIONS, JobDraft, FakeResponse(), 1.234)

    [logged] = database.ai_calls_since(database.now_utc().replace(year=2000))
    assert (logged.client_id, logged.purpose, logged.input_tokens, logged.seconds) == (1, "JobDraft", 900, 1.23)


def test_a_logging_failure_doesnt_lose_the_answer(monkeypatch, capsys):
    def broken(*args):
        raise RuntimeError("database is down")

    monkeypatch.setattr(database, "record_ai_call", broken)

    ai.record(ai.INSTRUCTIONS, None, FakeResponse(), 1.0)

    assert "AI log failed: RuntimeError" in capsys.readouterr().out


def test_read_job_without_the_work_still_saves_a_job(monkeypatch):
    draft = JobDraft(customer_name="Mr Jones", customer_phone=None, address=None, work="")
    use_fake_claude(monkeypatch, draft)

    assert read_job("book mr jones friday 3pm")["work"] == "Job"


PRICE_LIST = [("day rate", 25000), ("boiler service", 8500), ("call-out", 6000)]


def priced_draft(items, materials=None):
    return good_draft(price=None, items=[PricedItem(name=n, quantity=q) for n, q in items], materials=materials)


def test_read_quote_adds_up_items_and_materials_with_markup(monkeypatch):
    use_fake_claude(monkeypatch, priced_draft([("Day rate", "3")], materials="650"))

    quote = read_quote("mrs jones bathroom, 3 days, 650 materials", prices=PRICE_LIST, markup_percent=15)

    # 3 days at £250, plus £650 materials with 15% on top: £750 + £747.50.
    assert quote["total_pence"] == 149750


def test_read_quote_with_one_item_and_no_quantity_typed(monkeypatch):
    use_fake_claude(monkeypatch, priced_draft([("boiler service", "1")]))

    assert read_quote("boiler service for dave", prices=PRICE_LIST)["total_pence"] == 8500


def test_read_quote_rejects_an_item_not_on_the_list(monkeypatch):
    use_fake_claude(monkeypatch, priced_draft([("powerflush", "1")]))

    assert read_quote("powerflush for dave", prices=PRICE_LIST) is None


def test_read_quote_rejects_a_quantity_that_wasnt_typed(monkeypatch):
    use_fake_claude(monkeypatch, priced_draft([("day rate", "4")]))

    assert read_quote("bathroom for mrs jones, 3 days", prices=PRICE_LIST) is None


def test_read_quote_rejects_materials_that_werent_typed(monkeypatch):
    use_fake_claude(monkeypatch, priced_draft([("day rate", "1")], materials="900"))

    assert read_quote("mrs jones, 1 day plus materials", prices=PRICE_LIST) is None


def test_a_typed_total_beats_the_price_list(monkeypatch):
    draft = good_draft(price="1,250", items=[PricedItem(name="day rate", quantity="3")])
    use_fake_claude(monkeypatch, draft)

    assert read_quote(MESSAGE, prices=PRICE_LIST)["total_pence"] == 125000
