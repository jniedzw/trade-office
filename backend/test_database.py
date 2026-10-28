from sqlmodel import Session, create_engine

import database
from database import Client

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
