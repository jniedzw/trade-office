import os

from sqlmodel import Field, Session, SQLModel, create_engine, select

# A local SQLite file unless .env says otherwise (e.g. Postgres on Render).
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


def create_tables():
    """Create any tables that don't exist yet."""
    SQLModel.metadata.create_all(engine)


def get_client_by_number(twilio_number):
    """Return the client who owns this Twilio number, or None if nobody does."""
    with Session(engine) as session:
        statement = select(Client).where(Client.twilio_number == twilio_number)
        return session.exec(statement).first()
