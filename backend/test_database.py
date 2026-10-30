from datetime import timedelta

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

    database.update_client(1, "Andy's Plumbing", "+447700900222", "New message", False)

    client = database.get_client_by_number(TWILIO_NUMBER)
    assert client.business_name == "Andy's Plumbing"
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
