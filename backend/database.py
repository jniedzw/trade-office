import os
from datetime import datetime, timedelta, timezone

from sqlmodel import Field, Session, SQLModel, UniqueConstraint, create_engine, select

# A local SQLite file unless .env says otherwise (on Render it's on the persistent disk).
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///trade_office.db")
engine = create_engine(DATABASE_URL)

# The privacy policy promises call records are deleted after 12 months.
RETENTION_DAYS = 365
# A WhatsApp alert that isn't delivered within this many minutes goes by SMS instead.
ALERT_WAIT_MINUTES = 2
# A caller only gets one text-back in this many hours, however often they ring.
REPEAT_HOURS = 24


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


class SkipNumber(SQLModel, table=True):
    """A number (family, friends, suppliers) that never gets this client's text-back."""

    # The same number can't be on one client's list twice.
    __table_args__ = (UniqueConstraint("client_id", "number"),)

    id: int | None = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id", index=True)
    number: str


class OptOut(SQLModel, table=True):
    """A customer who replied STOP, so this client must never text them again."""

    __table_args__ = (UniqueConstraint("client_id", "number"),)

    id: int | None = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id", index=True)
    number: str
    time: datetime = Field(default_factory=now_utc)


class WhatsAppAlerts(SQLModel, table=True):
    """A client who opted in to missed-call alerts on WhatsApp."""

    id: int | None = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id", unique=True)
    time: datetime = Field(default_factory=now_utc)


class PendingAlert(SQLModel, table=True):
    """A WhatsApp alert we haven't seen delivered yet, kept so SMS can take over."""

    id: int | None = Field(default=None, primary_key=True)
    message_sid: str = Field(unique=True, index=True)
    client_id: int = Field(foreign_key="client.id")
    body: str
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


def get_client_by_owner(owner_mobile):
    """Return the client with this mobile, or None if there isn't one."""
    with Session(engine) as session:
        statement = select(Client).where(Client.owner_mobile == owner_mobile)
        return session.exec(statement).first()


def whatsapp_alerts_on(client_id):
    """Return True if this client opted in to WhatsApp alerts."""
    with Session(engine) as session:
        statement = select(WhatsAppAlerts).where(WhatsAppAlerts.client_id == client_id)
        return session.exec(statement).first() is not None


def set_whatsapp_alerts(client_id, on):
    """Turn this client's WhatsApp alerts on or off."""
    with Session(engine) as session:
        statement = select(WhatsAppAlerts).where(WhatsAppAlerts.client_id == client_id)
        row = session.exec(statement).first()
        if on and row is None:
            session.add(WhatsAppAlerts(client_id=client_id))
        elif not on and row is not None:
            session.delete(row)
        session.commit()


def save_pending_alert(message_sid, client_id, body):
    """Remember a WhatsApp alert until it's delivered."""
    with Session(engine) as session:
        session.add(PendingAlert(message_sid=message_sid, client_id=client_id, body=body))
        session.commit()


def pop_pending_alert(message_sid):
    """Remove this alert and return its client ID and body, or None if it's gone."""
    with Session(engine) as session:
        statement = select(PendingAlert).where(PendingAlert.message_sid == message_sid)
        row = session.exec(statement).first()
        if row is None:
            return None
        result = (row.client_id, row.body)
        session.delete(row)
        session.commit()
        return result


def overdue_alert_sids():
    """Return the IDs of WhatsApp alerts that have waited too long."""
    cutoff = now_utc() - timedelta(minutes=ALERT_WAIT_MINUTES)
    with Session(engine) as session:
        statement = select(PendingAlert).where(PendingAlert.time < cutoff)
        return [row.message_sid for row in session.exec(statement).all()]


def get_client(client_id):
    """Return the client with this ID, or None."""
    with Session(engine) as session:
        return session.get(Client, client_id)


def get_recent_calls(client_id, caller):
    """Return this caller's calls to this client in the last REPEAT_HOURS hours."""
    since = now_utc() - timedelta(hours=REPEAT_HOURS)
    with Session(engine) as session:
        statement = select(Call).where(
            Call.client_id == client_id, Call.caller == caller, Call.time > since
        )
        return session.exec(statement).all()


def retention_cutoff():
    """Return the moment before which records are too old to keep."""
    return now_utc() - timedelta(days=RETENTION_DAYS)


def delete_old_calls():
    """Delete calls older than RETENTION_DAYS and return how many went."""
    # OptOut rows stay: forgetting a STOP would mean texting that person again.
    cutoff = retention_cutoff()
    with Session(engine) as session:
        old_calls = session.exec(select(Call).where(Call.time < cutoff)).all()
        for call in old_calls:
            session.delete(call)
        session.commit()
    return len(old_calls)


def is_skipped(client_id, number):
    """Return True if this number is on the client's family and friends list."""
    with Session(engine) as session:
        statement = select(SkipNumber).where(
            SkipNumber.client_id == client_id, SkipNumber.number == number
        )
        return session.exec(statement).first() is not None


def add_skip_number(client_id, number):
    """Add a number to the client's list. Return False if it was already there."""
    if is_skipped(client_id, number):
        return False
    with Session(engine) as session:
        session.add(SkipNumber(client_id=client_id, number=number))
        session.commit()
    return True


def update_client(client_id, business_name, owner_mobile, text_back_message, active):
    """Save new details for an existing client."""
    with Session(engine) as session:
        client = session.get(Client, client_id)
        client.business_name = business_name
        client.owner_mobile = owner_mobile
        client.text_back_message = text_back_message
        client.active = active
        session.add(client)
        session.commit()


def is_opted_out(client_id, number):
    """Return True if this number has replied STOP to this client."""
    with Session(engine) as session:
        statement = select(OptOut).where(
            OptOut.client_id == client_id, OptOut.number == number
        )
        return session.exec(statement).first() is not None


def opt_out(client_id, number):
    """Record that this number replied STOP."""
    if not is_opted_out(client_id, number):
        with Session(engine) as session:
            session.add(OptOut(client_id=client_id, number=number))
            session.commit()


def opt_in(client_id, number):
    """Remove this number's STOP, because they replied START."""
    with Session(engine) as session:
        statement = select(OptOut).where(
            OptOut.client_id == client_id, OptOut.number == number
        )
        row = session.exec(statement).first()
        if row is not None:
            session.delete(row)
            session.commit()
