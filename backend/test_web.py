from datetime import timedelta

from sqlmodel import Session, select

import database
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

    assert "logged in as Test Plumbing" in response.text


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
