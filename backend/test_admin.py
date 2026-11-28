import sqlite3

import database
import whatsapp
from test_main import JAMES, OWNER, client, use_fakes
from test_web import log_in


def admin_cookie():
    """Ask Jack for an admin link from James's mobile, log in, and return the cookie header."""
    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{JAMES}", "admin")
    path = "/admin/login/" + text.split("/admin/login/")[1].split()[0]
    response = client.post(path, follow_redirects=False)
    assert response.status_code == 303
    return {"cookie": f"admin={response.cookies['admin']}"}


def test_only_james_gets_an_admin_link(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "ADMIN")

    assert "admin/login" not in text


def test_the_admin_cookie_is_strict(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{JAMES}", "ADMIN")
    path = "/admin/login/" + text.split("/admin/login/")[1].split()[0]

    header = client.post(path, follow_redirects=False).headers["set-cookie"].lower()

    assert "httponly" in header and "secure" in header and "samesite=strict" in header


def test_every_admin_page_needs_logging_in(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    assert client.get("/admin").status_code == 401
    assert client.get("/admin/clients/1").status_code == 401
    for path in ["/admin/clients", "/admin/clients/1", "/admin/clients/1/details",
                 "/admin/clients/1/skip", "/admin/clients/1/delete"]:
        assert client.post(path, data={"confirm": "Test Plumbing"}).status_code == 401, path
    assert database.get_client(1) is not None


def test_a_client_login_isnt_an_admin_login(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    cookie = log_in(OWNER)

    assert client.get("/admin", headers={"cookie": f"admin={cookie}"}).status_code == 401


def test_admin_lists_clients(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text = client.get("/admin", headers=admin_cookie()).text

    assert "Test Plumbing" in text


def test_admin_adds_a_client(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    cookie = admin_cookie()

    response = client.post(
        "/admin/clients", headers=cookie, follow_redirects=False,
        data={"business_name": "New Plumbing", "owner_mobile": "07700 900555", "twilio_number": "+447700900458"},
    )

    assert response.status_code == 303
    new = database.get_client_by_number("+447700900458")
    assert (new.business_name, new.owner_mobile) == ("New Plumbing", "+447700900555")


def test_admin_wont_reuse_a_twilio_number(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text = client.post(
        "/admin/clients", headers=admin_cookie(),
        data={"business_name": "Copy", "owner_mobile": "07700 900555", "twilio_number": "+447700900456"},
    ).text

    assert "already belongs to a client" in text


def test_admin_saves_business_details_and_checks_them(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    cookie = admin_cookie()
    good = {"legal_name": "Test t/a Test Plumbing", "address": "1 Test Street\r\nLiverpool", "ref_letters": "tp",
            "payment_days": "14", "bank_details": "Test Bank", "brand_colour": "#b8975a", "invoice_start": "244"}

    bad = client.post("/admin/clients/1/details", headers=cookie, data=good | {"ref_letters": "T-P"}).text
    client.post("/admin/clients/1/details", headers=cookie, data=good)

    assert "Use 2 to 4 reference letters" in bad
    details = database.get_business_details(1)
    assert (details.ref_letters, details.invoice_start, details.address) == ("TP", 244, "1 Test Street\nLiverpool")


def test_admin_pauses_a_client(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    client.post("/admin/clients/1", headers=admin_cookie(), data={
        "business_name": "Test Plumbing", "owner_mobile": OWNER, "text_back_message": "Hi",
    })

    assert database.get_client(1).active is False


def test_admin_edits_the_family_list(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    cookie = admin_cookie()

    client.post("/admin/clients/1/skip", headers=cookie, data={"number": "07700 900111"})
    assert database.skip_numbers(1) == ["+447700900111"]

    client.post("/admin/clients/1/skip/remove", headers=cookie, data={"number": "+447700900111"})
    assert database.skip_numbers(1) == []


def test_admin_delete_needs_the_exact_name(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    cookie = admin_cookie()

    client.post("/admin/clients/1/delete", headers=cookie, data={"confirm": "test plumbing"})
    assert database.get_client(1) is not None

    client.post("/admin/clients/1/delete", headers=cookie, data={"confirm": "Test Plumbing"})
    assert database.get_client(1) is None


def test_admin_saves_the_owners_first_name(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    client.post("/admin/clients/1", headers=admin_cookie(), data={
        "business_name": "Test Plumbing", "owner_mobile": OWNER, "text_back_message": "Hi",
        "active": "yes", "owner_name": " Dave ",
    })

    assert database.get_client(1).owner_name == "Dave"


def test_admin_can_download_a_backup_of_the_whole_database(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    database.save_job(1, "Mrs Jones", "Radiator")

    response = client.get("/admin/backup", headers=admin_cookie())

    assert response.headers["content-disposition"].startswith('attachment; filename="ask-jack-backup-')
    assert response.content.startswith(b"SQLite format 3")
    copy = tmp_path / "copy.db"
    copy.write_bytes(response.content)
    with sqlite3.connect(copy) as connection:
        assert connection.execute("SELECT customer_name FROM job").fetchall() == [("Mrs Jones",)]


def test_only_admin_can_download_a_backup(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    response = client.get("/admin/backup")

    assert response.status_code == 401
    assert not response.content.startswith(b"SQLite")
