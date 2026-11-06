from datetime import timedelta

from sqlalchemy import text
from sqlmodel import Session, create_engine, select

import database
from database import Call, Client

# Ofcom's fictional numbers: reserved for testing, never belong to anyone
TWILIO_NUMBER = "+447700900001"


def use_temp_database(monkeypatch, tmp_path):
    """Point the app at a fresh, empty database file for this test only."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(database, "engine", engine)
    database.create_tables()
    return engine


def add_client(engine, twilio_number):
    with Session(engine) as session:
        session.add(
            Client(
                business_name="Test Plumbing",
                owner_mobile="+447700900101",
                twilio_number=twilio_number,
                text_back_message="Hi, it's Test Plumbing.",
            )
        )
        session.commit()


def test_get_client_by_number_finds_the_right_client(monkeypatch, tmp_path):
    engine = use_temp_database(monkeypatch, tmp_path)
    add_client(engine, TWILIO_NUMBER)

    client = database.get_client_by_number(TWILIO_NUMBER)

    assert client is not None
    assert client.business_name == "Test Plumbing"
    assert client.active is True


def test_get_client_by_number_returns_none_for_unknown_number(monkeypatch, tmp_path):
    use_temp_database(monkeypatch, tmp_path)

    assert database.get_client_by_number("+447700900999") is None


def test_skip_numbers_belong_to_one_client(monkeypatch, tmp_path):
    engine = use_temp_database(monkeypatch, tmp_path)
    add_client(engine, "+447700900001")  # client 1
    add_client(engine, "+447700900002")  # client 2
    mum = "+447700900111"

    assert database.add_skip_number(1, mum) is True

    assert database.is_skipped(1, mum) is True
    # Client 2's callers aren't affected by client 1's list
    assert database.is_skipped(2, mum) is False


def test_adding_the_same_skip_number_twice(monkeypatch, tmp_path):
    engine = use_temp_database(monkeypatch, tmp_path)
    add_client(engine, TWILIO_NUMBER)

    assert database.add_skip_number(1, "+447700900111") is True
    assert database.add_skip_number(1, "+447700900111") is False


def test_update_client_changes_details_and_can_pause(monkeypatch, tmp_path):
    engine = use_temp_database(monkeypatch, tmp_path)
    add_client(engine, TWILIO_NUMBER)

    database.update_client(1, "Dave's Plumbing", "+447700900222", "New message", False)

    client = database.get_client_by_number(TWILIO_NUMBER)
    assert client.business_name == "Dave's Plumbing"
    assert client.owner_mobile == "+447700900222"
    assert client.text_back_message == "New message"
    assert client.active is False


def add_call_days_ago(engine, days):
    with Session(engine) as session:
        time = database.now_utc() - timedelta(days=days)
        session.add(Call(client_id=1, caller="+447700900102", texted=True, time=time))
        session.commit()


def test_delete_old_calls_only_deletes_old_ones(monkeypatch, tmp_path):
    engine = use_temp_database(monkeypatch, tmp_path)
    add_client(engine, TWILIO_NUMBER)
    add_call_days_ago(engine, 366)
    add_call_days_ago(engine, 364)
    add_call_days_ago(engine, 0)

    assert database.delete_old_calls() == 1

    with Session(engine) as session:
        assert len(session.exec(select(Call)).all()) == 2


def test_delete_old_calls_keeps_opt_outs(monkeypatch, tmp_path):
    engine = use_temp_database(monkeypatch, tmp_path)
    add_client(engine, TWILIO_NUMBER)
    with Session(engine) as session:
        time = database.now_utc() - timedelta(days=1000)
        session.add(database.OptOut(client_id=1, number="+447700900102", time=time))
        session.commit()

    database.delete_old_calls()

    assert database.is_opted_out(1, "+447700900102") is True


def test_recent_calls_are_per_client(monkeypatch, tmp_path):
    engine = use_temp_database(monkeypatch, tmp_path)
    add_client(engine, TWILIO_NUMBER)
    add_client(engine, "+447700900002")
    database.log_call(1, "+447700900102", True)

    assert len(database.get_recent_calls(1, "+447700900102")) == 1
    assert database.get_recent_calls(2, "+447700900102") == []


def test_save_quote_starts_as_a_draft(monkeypatch, tmp_path):
    engine = use_temp_database(monkeypatch, tmp_path)
    add_client(engine, TWILIO_NUMBER)

    quote_id = database.save_quote(1, "Mrs Jones", "Bathroom refit", "BR", 125050)
    quote = database.get_quote(quote_id)

    assert quote.customer_name == "Mrs Jones"
    assert quote.total_pence == 125050
    assert quote.status == "draft"


def test_get_quote_returns_none_for_unknown_id(monkeypatch, tmp_path):
    use_temp_database(monkeypatch, tmp_path)

    assert database.get_quote(99) is None


def test_save_quote_keeps_the_lines_in_order(monkeypatch, tmp_path):
    engine = use_temp_database(monkeypatch, tmp_path)
    add_client(engine, TWILIO_NUMBER)
    lines = ["Remove old suite", "Fit new basin", "Tile the floor"]

    quote_id = database.save_quote(1, "Mrs Jones", "Bathroom refit", "BR", 125050, lines)

    assert database.get_quote_lines(quote_id) == lines


def test_a_quote_with_no_lines(monkeypatch, tmp_path):
    engine = use_temp_database(monkeypatch, tmp_path)
    add_client(engine, TWILIO_NUMBER)

    quote_id = database.save_quote(1, "Mr Smith", "Boiler service", "BS", 8500)

    assert database.get_quote_lines(quote_id) == []


def test_business_details_can_be_added_then_changed(monkeypatch, tmp_path):
    engine = use_temp_database(monkeypatch, tmp_path)
    add_client(engine, TWILIO_NUMBER)
    details = {
        "legal_name": "Test Person t/a Test Plumbing",
        "address": "1 Test Street\nLiverpool",
        "ref_letters": "TP",
        "bank_details": "Test Bank",
    }

    assert database.get_business_details(1) is None
    database.save_business_details(1, **details)
    database.save_business_details(1, **(details | {"payment_days": 30}))

    saved = database.get_business_details(1)
    assert saved.ref_letters == "TP"
    assert saved.payment_days == 30


def test_a_new_column_is_added_to_an_old_table(monkeypatch, tmp_path):
    engine = use_temp_database(monkeypatch, tmp_path)
    # Make the table the way it was before brand_colour existed.
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE businessdetails"))
        connection.execute(text(
            "CREATE TABLE businessdetails (id INTEGER PRIMARY KEY, client_id INTEGER, "
            "legal_name VARCHAR, address VARCHAR, ref_letters VARCHAR, "
            "payment_days INTEGER, bank_details VARCHAR)"
        ))
        connection.execute(text(
            "INSERT INTO businessdetails VALUES (1, 1, 'Test', 'Street', 'TP', 14, 'Bank')"
        ))

    database.create_tables()
    database.create_tables()  # Running it twice must be safe.

    assert database.get_business_details(1).brand_colour == "#b8975a"
    assert database.get_business_details(1).contact == ""


def test_prices_belong_to_one_client(monkeypatch, tmp_path):
    engine = use_temp_database(monkeypatch, tmp_path)
    add_client(engine, "+447700900001")  # client 1
    add_client(engine, "+447700900002")  # client 2

    database.set_price(1, "Boiler Service", 8500)

    assert database.get_prices(1) == [("boiler service", 8500)]
    assert database.get_prices(2) == []
