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
import web
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


def test_today_shows_a_summary_and_quotes_waiting(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    database.set_owner_name(1, "Dave")
    book_today(1, "Mrs Jones", "Leaking radiator")
    sent = database.save_quote(1, "Mr Smith", "Boiler service", "BS", 8500)
    database.mark_quote_sent(sent, "+447700900123")
    accepted = database.save_quote(1, "Ms Brown", "New tap", "NT", 9000)
    database.mark_quote_sent(accepted, "+447700900124")
    database.accept_quote(accepted)

    text = client.get("/me", headers=as_cookie(log_in())).text

    assert " Dave</strong>" in text
    assert '<span class="big">1</span><span class="small">job today' in text
    assert "Quotes waiting for an answer" in text
    assert f'Mr Smith</strong>: <a class="name" href="/my/quotes/{sent}.pdf">Boiler service' in text
    # Accepted quotes aren't waiting any more.
    assert "Ms Brown" not in text
    assert '<a href="/me" class="here">Today</a>' in text


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


def customer_id(name="Mrs Jones", client_id=1):
    """Return the id of this client's customer with this name."""
    return next(c["id"] for c in database.customers_for(client_id) if c["name"] == name)


def test_customer_page_shows_everything_for_that_customer(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    database.save_job(1, "Mrs Jones", "Leaking radiator", customer_phone="+447700900123")
    quote_id = database.save_quote(1, "mrs jones", "New radiator", "NR", 42000)
    database.save_invoice(1, quote_id, "12 Test Road")
    database.save_job(1, "Mr Smith", "Boiler")

    text = client.get(f"/customers/{customer_id()}", headers=as_cookie(log_in())).text

    assert "Leaking radiator" in text and "New radiator" in text and "£420.00" in text
    assert "INV-0001" in text and 'href="tel:+447700900123"' in text
    assert "Boiler" not in text


def test_customer_list_links_to_each_customer(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    database.save_job(1, "Mrs Jones", "Radiator")

    text = client.get("/customers", headers=as_cookie(log_in())).text

    assert f'href="/customers/{customer_id()}"' in text


def test_notes_are_saved_and_shown(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    database.save_job(1, "Mrs Jones", "Radiator")
    cookie = as_cookie(log_in())
    path = f"/customers/{customer_id()}"

    response = client.post(f"{path}/notes", data={"notes": " Side gate, dog in garden "}, headers=cookie,
                           follow_redirects=False)

    assert response.status_code == 303
    assert "Side gate, dog in garden</textarea>" in client.get(path, headers=cookie).text
    assert "Side gate, dog in garden" in client.get("/customers", headers=cookie).text


def test_changing_details_updates_open_jobs_but_not_finished_ones(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    old = database.save_job(1, "Mrs Jones", "Old job", customer_phone="+447700900111")
    database.mark_job_done(old)
    open_job = database.save_job(1, "Mrs Jones", "New job", customer_phone="+447700900111")
    cookie = as_cookie(log_in())

    client.post(f"/customers/{customer_id()}/details", headers=cookie,
                data={"phone": "07700 900999", "address": "12 Test Road\n\nLiverpool "})

    assert database.get_job(open_job).customer_phone == "+447700900999"
    assert database.get_job(open_job).address == "12 Test Road\nLiverpool"
    assert database.get_job(old).customer_phone == "+447700900111"
    assert database.customers_for(1)[0]["phone"] == "+447700900999"


def test_a_bad_phone_number_changes_nothing(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    job_id = database.save_job(1, "Mrs Jones", "Radiator", customer_phone="+447700900111")
    cookie = as_cookie(log_in())
    path = f"/customers/{customer_id()}"

    response = client.post(f"{path}/details", headers=cookie, data={"phone": "12345", "address": ""},
                           follow_redirects=False)

    assert "doesn't look like a UK phone number" in client.get(response.headers["location"], headers=cookie).text
    assert database.get_job(job_id).customer_phone == "+447700900111"


def test_uk_phone():
    assert web.uk_phone("07700 900123") == "+447700900123"
    assert web.uk_phone("+44 151 496 0000") == "+441514960000"
    assert web.uk_phone("0151 496 0000") == "+441514960000"
    assert web.uk_phone("  ") == ""
    for bad in ["12345", "0800 123 4567", "+1 212 555 0100"]:
        assert web.uk_phone(bad) is None, bad


def test_nobody_can_open_or_change_another_clients_customer(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    add_other_client()
    database.save_job(2, "Their Customer", "Their job", customer_phone="+447700900222")
    theirs = customer_id("Their Customer", client_id=2)
    cookie = as_cookie(log_in(OWNER))

    response = client.get(f"/customers/{theirs}", headers=cookie)
    for kind, data in [("notes", {"notes": "hacked"}), ("details", {"phone": "07700900999", "address": ""})]:
        client.post(f"/customers/{theirs}/{kind}", data=data, headers=cookie, follow_redirects=False)

    assert response.status_code == 404 and "Their Customer" not in response.text
    assert database.customers_for(2)[0]["notes"] == ""
    assert database.customers_for(2)[0]["phone"] == "+447700900222"


def test_a_logged_in_owner_can_open_their_quote_and_invoice_pdfs(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    database.save_business_details(1, legal_name="Test", address="1 Test Street", ref_letters="TP", bank_details="Bank")
    quote_id = database.save_quote(1, "Mrs Jones", "Radiator", "RAD", 45000)
    invoice_id = database.save_invoice(1, quote_id, "12 Test Road").id
    cookie = as_cookie(log_in())

    for path in [f"/my/quotes/{quote_id}.pdf", f"/my/invoices/{invoice_id}.pdf"]:
        response = client.get(path, headers=cookie)
        assert response.status_code == 200, path
        assert response.content.startswith(b"%PDF"), path


def test_nobody_can_open_another_clients_pdfs(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    add_other_client()
    database.save_business_details(2, legal_name="Them", address="2 Test Street", ref_letters="TH", bank_details="Bank")
    quote_id = database.save_quote(2, "Their Customer", "Their work", "TW", 31337)
    invoice_id = database.save_invoice(2, quote_id, "Their Road").id
    cookie = as_cookie(log_in(OWNER))

    for path in [f"/my/quotes/{quote_id}.pdf", f"/my/invoices/{invoice_id}.pdf"]:
        response = client.get(path, headers=cookie)
        assert response.status_code == 404 and not response.content.startswith(b"%PDF"), path
        assert client.get(path).status_code == 401, path


def test_job_done_button_marks_the_job_done_and_undo_puts_it_back(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    job_id = book_today(1, "Mrs Jones", "Leaking radiator")
    cookie = as_cookie(log_in())
    assert 'action="/jobs/1/done"' in client.get("/me", headers=cookie).text

    response = client.post("/jobs/1/done", data={"back": "/me"}, headers=cookie, follow_redirects=False)

    assert response.headers["location"] == "/me?did=done&n=1"
    assert database.get_job(job_id).status == "done"
    page = client.get(response.headers["location"], headers=cookie).text
    assert "J1 marked done." in page and 'action="/undo"' in page
    assert 'action="/jobs/1/done"' not in page

    client.post("/undo", data={"back": "/me"}, headers=cookie, follow_redirects=False)
    assert database.get_job(job_id).status == "booked"


def test_mark_as_paid_button(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    quote_id = database.save_quote(1, "Mrs Jones", "Radiator", "RAD", 45000)
    database.save_invoice(1, quote_id, "12 Test Road")
    cookie = as_cookie(log_in())
    assert 'action="/invoices/1/paid"' in client.get("/money", headers=cookie).text

    response = client.post("/invoices/1/paid", data={"back": "/money"}, headers=cookie, follow_redirects=False)

    assert database.unpaid_invoices(1) == []
    assert "INV-0001 marked as paid." in client.get(response.headers["location"], headers=cookie).text


def test_buttons_only_go_back_to_our_own_pages(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    book_today(1, "Mrs Jones", "Leaking radiator")
    cookie = as_cookie(log_in())

    for where in ["https://example.com", "//example.com", "/customers/1/../../evil"]:
        response = client.post("/jobs/1/done", data={"back": where}, headers=cookie, follow_redirects=False)
        assert response.headers["location"].startswith("/me?"), where


def test_nobody_can_finish_or_mark_paid_another_clients_work(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    add_other_client()
    job_id = book_today(2, "Their Customer", "Their job")
    quote_id = database.save_quote(2, "Their Customer", "Their work", "TW", 31337)
    database.save_invoice(2, quote_id, "Their Road")
    cookie = as_cookie(log_in(OWNER))

    client.post("/jobs/1/done", headers=cookie, follow_redirects=False)
    client.post("/invoices/1/paid", headers=cookie, follow_redirects=False)

    assert database.get_job(job_id).status == "booked"
    assert len(database.unpaid_invoices(2)) == 1


def test_money_page(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    for name, pence in [("Mrs Jones", 125050), ("Mr Smith", 8500)]:
        quote_id = database.save_quote(1, name, "Work", "WK", pence)
        database.save_invoice(1, quote_id, "12 Test Road")
    database.mark_paid(1, 2)

    text = client.get("/money", headers=as_cookie(log_in())).text

    assert '<span class="big">£1,250.50</span><span class="small">owed to you' in text
    assert '<span class="big">£85.00</span><span class="small">paid this month' in text
    assert 'INV-0002</a>: Mr Smith, £85.00' in text


def test_download_gives_a_spreadsheet_file(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    response = client.get("/download", headers=as_cookie(log_in()))

    assert response.headers["content-type"] == export.XLSX
    assert response.headers["content-disposition"].startswith('attachment; filename="ask-jack-')


def test_no_page_shows_another_clients_data(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    add_other_client()
    book_today(2, "Their Customer", "Their job")
    # An amount that can't turn up anywhere else on the page by chance.
    quote_id = database.save_quote(2, "Their Customer", "Their work", "TW", 31337)
    database.save_invoice(2, quote_id, "Their Road")
    cookie = log_in(OWNER)

    for path in ["/me", "/customers", "/money"]:
        text = client.get(path, headers=as_cookie(cookie)).text
        assert "Their Customer" not in text and "313.37" not in text, path
    rows = load_workbook(BytesIO(client.get("/download", headers=as_cookie(cookie)).content))
    assert all(sheet.max_row == 1 for sheet in rows if sheet.title != "Prices")


def test_every_page_needs_logging_in(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    for path in ["/me", "/customers", "/customers/1", "/money", "/download"]:
        assert client.get(path).status_code == 401, path
    for path in ["/customers/1/notes", "/customers/1/details", "/jobs/1/done", "/invoices/1/paid", "/undo"]:
        assert client.post(path).status_code == 401, path


def sent_quote(monkeypatch, tmp_path, status="final", review_link="https://g.page/r/test/review"):
    """Save quote 1 for Mrs Jones, and return the path of her quote page."""
    use_fakes(monkeypatch, tmp_path)
    database.save_business_details(
        1, legal_name="Test", address="1 Test Street", ref_letters="TP", bank_details="Bank", review_link=review_link
    )
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


def test_no_review_box_if_the_business_has_no_review_link(monkeypatch, tmp_path):
    path = sent_quote(monkeypatch, tmp_path, review_link="")

    assert 'name="review_ok"' not in client.get(path).text


def test_the_review_box_starts_unticked(monkeypatch, tmp_path):
    path = sent_quote(monkeypatch, tmp_path)
    monkeypatch.setattr(alerts, "send_sms", lambda to, body, from_number: None)

    page = client.get(path).text
    client.post(path)

    assert 'name="review_ok" value="yes"' in page and "checked" not in page
    assert database.get_quote(1).review_ok is False
