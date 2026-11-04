import anthropic
import httpx2

import ai
import settings
from ai import QuoteDraft, prices_in, read_quote

MESSAGE = "bathroom for mrs jones, rip out old suite, new basin and loo, 1,250 all in"


def use_fake_claude(monkeypatch, draft):
    """Make ask_claude return draft instead of calling the real AI."""
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai, "ask_claude", lambda message: draft)


def good_draft(**changes):
    details = {
        "customer_name": "Mrs Jones",
        "title": "Bathroom refit",
        "job_code": "br",
        "price": "1,250",
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

    def broken(message):
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        raise anthropic.APIConnectionError(request=request)

    monkeypatch.setattr(ai, "ask_claude", broken)

    assert read_quote(MESSAGE) is None
    # The log names the error but never the customer.
    assert "Jones" not in capsys.readouterr().out


def test_read_quote_without_a_key_never_calls_claude(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)

    def must_not_run(message):
        raise AssertionError("called Claude")

    monkeypatch.setattr(ai, "ask_claude", must_not_run)

    assert read_quote(MESSAGE) is None
