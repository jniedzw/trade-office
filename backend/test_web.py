from datetime import datetime, timedelta
from io import BytesIO

from openpyxl import load_workbook
from sqlmodel import Session, select

import alerts
import database
import diary
import export
import quotes
import settings
import whatsapp
from database import Client, LoginLink
from test_main import OWNER, client, use_fakes

OTHER_OWNER = "+447700900790"


def add_other_client():
    """Add client 2, Other Plumbing, alongside the test client."""
    with Session(database.engine) as session:
        session.add(
            Client(
                business_name="Other Plumbing",
                owner_mobile=OTHER_OWNER,
                twilio_number="+447700900457",
                text_back_message="Hi",
            )
        )
        session.commit()


def login_path(sender=OWNER):
    """Ask Jack for a login link on WhatsApp, and return its path."""
    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{sender}", "My page")
    return "/login/" + text.split("/login/")[1].split()[0]


def log_in(sender=OWNER):
    """Log in through the link and return the session cookie's value."""
    response = client.post(login_path(sender), follow_redirects=False)
    assert response.status_code == 303
    # The test client only sends secure cookies over https, so we pass it on by hand.
    return response.cookies["session"]


def as_cookie(cookie):
    """Return the header a browser sends with this session cookie."""
    return {"cookie": f"session={cookie}"}


def test_opening_the_link_doesnt_log_in(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    path = login_path()

    response = client.get(path)

    # Just a button, so a WhatsApp link preview can't use the link up.
    assert "<form method=\"post\">" in response.text
    assert client.post(path, follow_redirects=False).status_code == 303


def test_logging_in_shows_your_page(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    cookie = log_in()

    response = client.get("/me", headers=as_cookie(cookie))

    assert response.status_code == 200
    assert "Test Plumbing" in response.text
    assert "Nothing booked today." in response.text


def test_the_cookie_is_private_and_lasts_30_days(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    response = client.post(login_path(), follow_redirects=False)

    header = response.headers["set-cookie"].lower()
    assert "httponly" in header and "secure" in header and "samesite=lax" in header
    assert "max-age=2592000" in header


def test_a_login_link_only_works_once(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    path = login_path()
    client.post(path, follow_redirects=False)

    response = client.post(path, follow_redirects=False)

    assert response.status_code == 403
    assert "That link has expired" in response.text


def test_an_old_login_link_doesnt_work(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    path = login_path()
    with Session(database.engine) as session:
        link = session.exec(select(LoginLink)).first()
        link.expires_at = database.now_utc() - timedelta(minutes=1)
        session.add(link)
        session.commit()

    assert client.post(path, follow_redirects=False).status_code == 403


def test_only_a_hash_of_the_token_is_stored(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    token = login_path().removeprefix("/login/")

    with Session(database.engine) as session:
        link = session.exec(select(LoginLink)).first()

    assert link.token_hash != token
    assert link.token_hash == database.hash_token(token)


def test_no_page_without_logging_in(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    for cookie in [None, "made-up-cookie"]:
        response = client.get("/me", headers=as_cookie(cookie) if cookie else {})
        assert response.status_code == 401
        assert "Test Plumbing" not in response.text


def test_each_client_only_sees_their_own_page(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    add_other_client()

    mine = client.get("/me", headers=as_cookie(log_in(OWNER)))
    theirs = client.get("/me", headers=as_cookie(log_in(OTHER_OWNER)))

    assert "Test Plumbing" in mine.text and "Other Plumbing" not in mine.text
    assert "Other Plumbing" in theirs.text and "Test Plumbing" not in theirs.text


def test_a_paused_client_cant_use_their_page(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    cookie = log_in()
    database.update_client(1, "Test Plumbing", OWNER, "Hi", active=False)

    assert client.get("/me", headers=as_cookie(cookie)).status_code == 401


def test_logging_out_ends_the_session(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    cookie = log_in()

    client.post("/logout", headers=as_cookie(cookie), follow_redirects=False)

    assert client.get("/me", headers=as_cookie(cookie)).status_code == 401


def book_today(client_id, name, work, address=None, phone=None, hour=14):
    """Save and book a job for today at hour o'clock, UK time."""
    today = diary.uk_now(database.now_utc()).date()
    job_id = database.save_job(client_id, name, work, customer_phone=phone, address=address)
    database.book_job(job_id, datetime(today.year, today.month, today.day, hour, tzinfo=diary.UK))
    return job_id


def test_today_shows_todays_jobs_with_map_and_phone_links(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    book_today(1, "Mrs Jones", "Leaking radiator", address="12 Test Road\nLiverpool", phone="+447700900123")

    text = client.get("/me", headers=as_cookie(log_in())).text

    assert "Mrs Jones</strong>: Leaking radiator" in text
    assert "maps/search/?api=1&query=12%20Test%20Road%2C%20Liverpool" in text
    assert 'href="tel:+447700900123"' in text


def test_customer_details_cant_run_code_in_the_page(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    book_today(1, "<script>alert(1)</script>", "Tap")

    text = client.get("/me", headers=as_cookie(log_in())).text

    # Jinja2 escapes it, so it shows as text instead of running.
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;" in text


def test_customers_page(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    database.save_job(1, "Mrs Jones", "Radiator", address="12 Test Road")
    database.save_job(1, "mrs jones", "Boiler")
    database.save_quote(1, "Mr Smith", "Boiler service", "BS", 8500)

    text = client.get("/customers", headers=as_cookie(log_in())).text

    assert "Mrs Jones</strong>" in text and "2 jobs" in text
    assert "Mr Smith</strong>" in text and "0 jobs" in text


def test_money_page(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    for name, pence in [("Mrs Jones", 125050), ("Mr Smith", 8500)]:
        quote_id = database.save_quote(1, name, "Work", "WK", pence)
        database.save_invoice(1, quote_id, "12 Test Road")
    database.mark_paid(1, 2)

    text = client.get("/money", headers=as_cookie(log_in())).text

    assert "Owed to you: £1,250.50" in text
    assert "Paid this month: £85.00" in text
    assert "INV-0002: Mr Smith, £85.00" in text


def test_download_gives_a_spreadsheet_file(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    response = client.get("/download", headers=as_cookie(log_in()))

    assert response.headers["content-type"] == export.XLSX
    assert response.headers["content-disposition"].startswith('attachment; filename="ask-jack-')


def test_no_page_shows_another_clients_data(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    add_other_client()
    book_today(2, "Their Customer", "Their job")
    quote_id = database.save_quote(2, "Their Customer", "Their work", "TW", 99900)
    database.save_invoice(2, quote_id, "Their Road")
    cookie = log_in(OWNER)

    for path in ["/me", "/customers", "/money"]:
        text = client.get(path, headers=as_cookie(cookie)).text
        assert "Their Customer" not in text and "999" not in text, path
    rows = load_workbook(BytesIO(client.get("/download", headers=as_cookie(cookie)).content))
    assert all(sheet.max_row == 1 for sheet in rows if sheet.title != "Prices")


def test_every_page_needs_logging_in(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    for path in ["/me", "/customers", "/money", "/download"]:
        assert client.get(path).status_code == 401, path


def sent_quote(monkeypatch, tmp_path, status="final"):
    """Save quote 1 for Mrs Jones, and return the path of her quote page."""
    use_fakes(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "QUOTE_LINK_SECRET", "test-secret")
    quote_id = database.save_quote(1, "Mrs Jones", "Bathroom refit", "BR", 125050, ["Fit basin"])
    if status == "final":
        database.finish_quote(1, quote_id)
    return quotes.customer_quote_link(quote_id, database.now_utc()).removeprefix(settings.PUBLIC_URL)


def test_the_customer_sees_their_quote(monkeypatch, tmp_path):
    path = sent_quote(monkeypatch, tmp_path)

    text = client.get(path).text

    assert "Bathroom refit" in text and "£1,250.50" in text and "Fit basin" in text
    assert "Accept quote" in text
    assert "Test Plumbing" in text


def test_accepting_tells_the_tradesperson_once(monkeypatch, tmp_path):
    path = sent_quote(monkeypatch, tmp_path)
    texts = []
    monkeypatch.setattr(alerts, "send_sms", lambda to, body, from_number: texts.append((to, body)))

    first = client.post(path).text
    client.post(path)

    assert "you've accepted this quote" in first
    assert database.get_quote(1).accepted_at is not None
    assert texts == [(OWNER, "Good news: Mrs Jones accepted quote Q1 for £1,250.50. Message Jack to book it in.")]


def test_a_changed_or_old_customer_link_doesnt_work(monkeypatch, tmp_path):
    path = sent_quote(monkeypatch, tmp_path)

    assert client.get(path.replace("/q/1", "/q/2")).status_code == 403
    assert client.get(path.split("&sig=")[0] + "&sig=made-up").status_code == 403


def test_a_draft_has_no_customer_page(monkeypatch, tmp_path):
    path = sent_quote(monkeypatch, tmp_path, status="draft")

    assert client.get(path).status_code == 403


def test_ticking_the_review_box_records_consent(monkeypatch, tmp_path):
    path = sent_quote(monkeypatch, tmp_path)
    monkeypatch.setattr(alerts, "send_sms", lambda to, body, from_number: None)

    client.post(path, data={"review_ok": "yes"})

    assert database.get_quote(1).review_ok is True


def test_the_review_box_starts_unticked(monkeypatch, tmp_path):
    path = sent_quote(monkeypatch, tmp_path)
    monkeypatch.setattr(alerts, "send_sms", lambda to, body, from_number: None)

    page = client.get(path).text
    client.post(path)

    assert 'name="review_ok" value="yes"' in page and "checked" not in page
    assert database.get_quote(1).review_ok is False
