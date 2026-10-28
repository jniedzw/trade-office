import os
from datetime import datetime, timezone

from sqlmodel import Field, Session, SQLModel, create_engine, select

# A local SQLite file unless .env says otherwise (on Render it's on the persistent disk).
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///trade_office.db")
engine = create_engine(DATABASE_URL)


class Client(SQLModel, table=True):
    """One tradesperson using Trade Office."""

    id: int | None = Field(default=None, primary_key=True)
    business_name: str
    owner_mobile: str
    twilio_number: str = Field(unique=True, index=True)
    text_back_message: str
    active: bool = True


def now_utc():
    """Return the current time in UTC, so times don't jump when the clocks change."""
    return datetime.now(timezone.utc)


class Call(SQLModel, table=True):
    """One missed call to a client's Twilio number."""

    id: int | None = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id", index=True)
    caller: str
    texted: bool
    time: datetime = Field(default_factory=now_utc)


def create_tables():
    """Create any tables that don't exist yet."""
    SQLModel.metadata.create_all(engine)


def get_client_by_number(twilio_number):
    """Return the client who owns this Twilio number, or None if nobody does."""
    with Session(engine) as session:
        statement = select(Client).where(Client.twilio_number == twilio_number)
        return session.exec(statement).first()


def log_call(client_id, caller, texted):
    """Save one missed call for this client."""
    with Session(engine) as session:
        session.add(Call(client_id=client_id, caller=caller, texted=texted))
        session.commit()
