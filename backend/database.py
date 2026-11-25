import hashlib
import json
import os
import secrets
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, inspect, text, update
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
# Jack forgets a question that hasn't been answered in this many hours.
QUESTION_HOURS = 24
# UNDO only works on a change made in the last this many hours.
UNDO_HOURS = 24
# A login link works for this many minutes, and a login lasts this many days.
LOGIN_LINK_MINUTES = 15
SESSION_DAYS = 30
# Admin can change any client, so its logins are kept short.
ADMIN_SESSION_HOURS = 12


class Client(SQLModel, table=True):
    """One tradesperson using Trade Office."""

    id: int | None = Field(default=None, primary_key=True)
    business_name: str
    owner_mobile: str
    twilio_number: str = Field(unique=True, index=True)
    text_back_message: str
    active: bool = True
    # Added to materials on quotes, as a percentage.
    markup_percent: int = 0
    # The owner's first name, so Jack can say "Morning Andy". Empty if not given.
    owner_name: str = ""


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


class Quote(SQLModel, table=True):
    """One quote a client has made for a customer."""

    id: int | None = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id", index=True)
    customer_name: str
    title: str
    job_code: str
    # Whole pence, because floats can't hold amounts like 0.1 exactly.
    total_pence: int
    # draft, then final.
    status: str = "draft"
    time: datetime = Field(default_factory=now_utc)
    # The job it was quoted from, or None for a quote made straight away.
    job_id: int | None = None
    # Each client's quotes are numbered 1, 2, 3..., shown as Q1, Q2, Q3.
    number: int | None = None
    # The mobile it was texted to, and when it was sent and accepted, if it has been.
    customer_phone: str | None = None
    sent_at: datetime | None = None
    accepted_at: datetime | None = None
    # True only if the customer ticked the box saying they're happy to be asked for a review.
    review_ok: bool = False


class QuoteLine(SQLModel, table=True):
    """One line of a quote's scope of works, like "Fit new basin"."""

    id: int | None = Field(default=None, primary_key=True)
    quote_id: int = Field(foreign_key="quote.id", index=True)
    # Keeps the lines in the order they were written.
    position: int
    text: str


class BusinessDetails(SQLModel, table=True):
    """What a client's quotes and invoices need: who they are and how to pay them."""

    id: int | None = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id", unique=True)
    legal_name: str
    # Lines separated by "\n", so it prints the way it's written.
    address: str
    # Letters at the start of their references, like DPL.
    ref_letters: str
    payment_days: int = 14
    bank_details: str
    # The accent colour on their PDFs, as a web colour like #b8975a.
    brand_colour: str = "#b8975a"
    # A phone number or email for customers, shown on quotes and invoices.
    contact: str = ""
    # The number of their first Ask Jack invoice, so it carries on from their own.
    invoice_start: int = 1
    # Their Google review link, or "" if they don't want review requests sent.
    review_link: str = ""


class Invoice(SQLModel, table=True):
    """An invoice made from a client's final quote."""

    # Each client numbers their invoices 1, 2, 3..., and a quote gets one invoice.
    __table_args__ = (UniqueConstraint("client_id", "number"),)

    id: int | None = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id", index=True)
    quote_id: int = Field(foreign_key="quote.id", unique=True)
    number: int
    # Invoices must show the customer's address; quotes don't need it.
    customer_address: str
    time: datetime = Field(default_factory=now_utc)
    # When it was paid, or None while it's still owed.
    paid_at: datetime | None = None
    # When the customer was last texted a reminder, so they aren't chased too often.
    reminded_at: datetime | None = None


class Job(SQLModel, table=True):
    """A piece of work for a customer, logged before it's quoted or booked."""

    id: int | None = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id", index=True)
    customer_name: str
    # Either can be missing when the job's logged, and filled in later.
    customer_phone: str | None = None
    address: str | None = None
    work: str
    # logged, then quoted, booked, done, invoiced and paid.
    status: str = "logged"
    time: datetime = Field(default_factory=now_utc)
    # When it's booked for, saved in UTC, and the last day of a job that runs over days.
    starts_at: datetime | None = None
    ends_on: date | None = None
    # Each client's jobs are numbered 1, 2, 3..., shown as J1, J2, J3.
    number: int | None = None
    # When the tradesperson said the work was finished, or None.
    done_at: datetime | None = None
    # When the customer was asked for a review, so it only happens once.
    review_asked_at: datetime | None = None


class PriceItem(SQLModel, table=True):
    """One of a client's standard prices, like a boiler service for £85."""

    __table_args__ = (UniqueConstraint("client_id", "name"),)

    id: int | None = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id", index=True)
    # Saved in lower case, so "Boiler Service" and "boiler service" are one price.
    name: str
    pence: int


class AiCall(SQLModel, table=True):
    """One call to the AI, for working out costs. Never the message itself."""

    id: int | None = Field(default=None, primary_key=True)
    # None when there's no client, as when the test set runs.
    client_id: int | None = Field(default=None, index=True)
    purpose: str
    model: str
    input_tokens: int
    output_tokens: int
    seconds: float
    time: datetime = Field(default_factory=now_utc)


class LoginLink(SQLModel, table=True):
    """A one-time link that logs a client in to their page."""

    id: int | None = Field(default=None, primary_key=True)
    # Only a hash of the link's secret part is kept, so a leaked database can't log anyone in.
    token_hash: str = Field(unique=True, index=True)
    client_id: int = Field(foreign_key="client.id")
    expires_at: datetime
    used_at: datetime | None = None


class WebSession(SQLModel, table=True):
    """A logged-in browser, kept as a hash of the cookie it holds."""

    id: int | None = Field(default=None, primary_key=True)
    token_hash: str = Field(unique=True, index=True)
    client_id: int = Field(foreign_key="client.id")
    expires_at: datetime


class Customer(SQLModel, table=True):
    """One of a client's customers, made the first time their name turns up on a job or quote.

    Jobs and quotes still hold the customer's name; this row adds notes, and any
    phone or address the tradesperson corrected on their web page.
    """

    __table_args__ = (UniqueConstraint("client_id", "name_key"),)
    id: int | None = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id", index=True)
    name: str
    # The name in lower case without spaces at the ends, so "mrs jones" and "Mrs Jones" are one customer.
    name_key: str
    # None until the tradesperson sets them; until then the newest job's are used.
    phone: str | None = None
    address: str | None = None
    notes: str = ""
    time: datetime = Field(default_factory=now_utc)


class UndoStep(SQLModel, table=True):
    """The last change Jack made for a client, kept so UNDO can put it back."""

    id: int | None = Field(default=None, primary_key=True)
    # Only the latest change can be undone, so there's one row per client.
    client_id: int = Field(foreign_key="client.id", unique=True)
    # What changed, for Jack's reply, like "INV-0244 marked as paid".
    description: str
    # The rows as they were before, as JSON: [{"table": "job", "id": 4, "fields": {...}}].
    before: str
    time: datetime = Field(default_factory=now_utc)


class AdminLogin(SQLModel, table=True):
    """James's admin access: a one-time link, or a logged-in browser. Only hashes are kept."""

    id: int | None = Field(default=None, primary_key=True)
    token_hash: str = Field(unique=True, index=True)
    # "link" until it's used to log in, then a separate "session" row for the browser.
    kind: str
    expires_at: datetime
    used_at: datetime | None = None


class PendingQuestion(SQLModel, table=True):
    """Something Jack asked a client and is waiting to hear back about."""

    id: int | None = Field(default=None, primary_key=True)
    # One question at a time per client: a new one replaces the old.
    client_id: int = Field(foreign_key="client.id", unique=True)
    # What was asked, like "invoice_address", and which quote it's about.
    question: str
    quote_id: int
    time: datetime = Field(default_factory=now_utc)


# Columns added after their table was first made. create_all only makes missing
# tables, never missing columns, so these are added by hand: a small migration.
NEW_COLUMNS = [
    ("businessdetails", "brand_colour", "VARCHAR NOT NULL DEFAULT '#b8975a'"),
    ("businessdetails", "contact", "VARCHAR NOT NULL DEFAULT ''"),
    ("quote", "job_id", "INTEGER"),
    ("invoice", "paid_at", "DATETIME"),
    ("job", "starts_at", "DATETIME"),
    ("job", "ends_on", "DATE"),
    ("client", "markup_percent", "INTEGER NOT NULL DEFAULT 0"),
    ("job", "number", "INTEGER"),
    ("quote", "number", "INTEGER"),
    ("businessdetails", "invoice_start", "INTEGER NOT NULL DEFAULT 1"),
    ("job", "done_at", "DATETIME"),
    ("quote", "customer_phone", "VARCHAR"),
    ("quote", "sent_at", "DATETIME"),
    ("quote", "accepted_at", "DATETIME"),
    ("invoice", "reminded_at", "DATETIME"),
    ("quote", "review_ok", "BOOLEAN NOT NULL DEFAULT 0"),
    ("job", "review_asked_at", "DATETIME"),
    ("businessdetails", "review_link", "VARCHAR NOT NULL DEFAULT ''"),
    ("client", "owner_name", "VARCHAR NOT NULL DEFAULT ''"),
]


def add_missing_columns():
    """Add any column in NEW_COLUMNS that an existing table doesn't have yet."""
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, column, definition in NEW_COLUMNS:
            existing = [col["name"] for col in inspector.get_columns(table)]
            if column not in existing:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))


def create_tables():
    """Create any tables that don't exist yet, then add any new columns."""
    SQLModel.metadata.create_all(engine)
    add_missing_columns()
    number_old_rows()


def next_number(session, model, client_id):
    """Return the client's next number for a job or quote: their highest so far, plus 1."""
    numbers = session.exec(select(model.number).where(model.client_id == client_id)).all()
    return max((n for n in numbers if n is not None), default=0) + 1


def number_old_rows():
    """Give a number to any job or quote saved before numbers existed, in the order they were made."""
    with Session(engine) as session:
        for model in (Job, Quote):
            rows = session.exec(select(model).where(model.number == None).order_by(model.id)).all()  # noqa: E711
            for row in rows:
                row.number = next_number(session, model, row.client_id)
                session.add(row)
                # flush, so the next row's next_number sees this one.
                session.flush()
        session.commit()


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


def active_clients():
    """Return every client who isn't paused."""
    with Session(engine) as session:
        return session.exec(select(Client).where(Client.active == True)).all()  # noqa: E712


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


def save_quote(client_id, customer_name, title, job_code, total_pence, lines=(), job_id=None):
    """Save a new draft quote and its scope of works, and return its ID.

    With a job_id, the job is marked as quoted in the same commit.
    """
    quote = Quote(
        client_id=client_id,
        customer_name=customer_name,
        title=title,
        job_code=job_code,
        total_pence=total_pence,
        job_id=job_id,
    )
    with Session(engine) as session:
        quote.number = next_number(session, Quote, client_id)
        session.add(quote)
        # flush sends the quote to the database without committing, so it gets an ID
        # the lines can point to. One commit then saves all of it, or none of it.
        session.flush()
        for position, text in enumerate(lines):
            session.add(QuoteLine(quote_id=quote.id, position=position, text=text))
        set_job_status(session, job_id, "quoted")
        session.commit()
        return quote.id


def get_business_details(client_id):
    """Return this client's business details, or None if they haven't been added."""
    with Session(engine) as session:
        statement = select(BusinessDetails).where(BusinessDetails.client_id == client_id)
        return session.exec(statement).first()


def save_business_details(client_id, **details):
    """Add this client's business details, or replace the ones already saved."""
    with Session(engine) as session:
        statement = select(BusinessDetails).where(BusinessDetails.client_id == client_id)
        row = session.exec(statement).first() or BusinessDetails(client_id=client_id)
        for name, value in details.items():
            setattr(row, name, value)
        session.add(row)
        session.commit()


def update_quote(quote_id, customer_name, title, job_code, total_pence, lines):
    """Replace a quote's details and lines, and make it a draft again."""
    with Session(engine) as session:
        quote = session.get(Quote, quote_id)
        quote.customer_name = customer_name
        quote.title = title
        quote.job_code = job_code
        quote.total_pence = total_pence
        # A changed quote needs checking again before it's sent.
        quote.status = "draft"
        session.add(quote)
        old_lines = session.exec(select(QuoteLine).where(QuoteLine.quote_id == quote_id))
        for line in old_lines:
            session.delete(line)
        for position, text in enumerate(lines):
            session.add(QuoteLine(quote_id=quote_id, position=position, text=text))
        # One commit, so the quote never ends up with half its new lines.
        session.commit()


def finish_quote(client_id, quote_id):
    """Mark this client's quote as final and return it, or None if it isn't theirs."""
    with Session(engine) as session:
        quote = session.get(Quote, quote_id)
        # Checking the owner stops one tradesperson finishing another's quote.
        if quote is None or quote.client_id != client_id:
            return None
        quote.status = "final"
        session.add(quote)
        session.commit()
        session.refresh(quote)
        return quote


def get_invoice(invoice_id):
    """Return the invoice with this ID, or None."""
    with Session(engine) as session:
        return session.get(Invoice, invoice_id)


def get_invoice_for_quote(quote_id):
    """Return the invoice already made from this quote, or None."""
    with Session(engine) as session:
        statement = select(Invoice).where(Invoice.quote_id == quote_id)
        return session.exec(statement).first()


def save_invoice(client_id, quote_id, customer_address):
    """Save an invoice with the client's next invoice number, and return it."""
    with Session(engine) as session:
        numbers = session.exec(select(Invoice.number).where(Invoice.client_id == client_id))
        details = session.exec(select(BusinessDetails).where(BusinessDetails.client_id == client_id)).first()
        # Invoice numbers must run in order with no gaps, so take the next one up,
        # starting from the client's own next number if they already sent invoices.
        number = max(max(numbers, default=0) + 1, details.invoice_start if details else 1)
        invoice = Invoice(
            client_id=client_id,
            quote_id=quote_id,
            number=number,
            customer_address=customer_address,
        )
        session.add(invoice)
        set_job_status(session, session.get(Quote, quote_id).job_id, "invoiced")
        session.commit()
        session.refresh(invoice)
        return invoice


def set_job_status(session, job_id, status):
    """Move a job on to status, inside an open session. Does nothing without a job."""
    if job_id is not None:
        job = session.get(Job, job_id)
        job.status = status
        session.add(job)


def as_utc(moment):
    """Return a time from SQLite with its UTC timezone put back."""
    # SQLite stores times without a timezone, so they come back without one.
    return moment.replace(tzinfo=timezone.utc)


def unpaid_invoices(client_id):
    """Return this client's unpaid invoices, oldest first.

    Each is a dict with the invoice number, customer, amount and date.
    """
    with Session(engine) as session:
        statement = (
            select(Invoice, Quote)
            .where(Invoice.quote_id == Quote.id)
            .where(Invoice.client_id == client_id, Invoice.paid_at == None)  # noqa: E711
            .order_by(Invoice.time)
        )
        return [
            {
                "id": invoice.id,
                "number": invoice.number,
                "quote_id": quote.id,
                "customer_name": quote.customer_name,
                "total_pence": quote.total_pence,
                "time": as_utc(invoice.time),
                "reminded_at": as_utc(invoice.reminded_at) if invoice.reminded_at else None,
            }
            for invoice, quote in session.exec(statement).all()
        ]


def mark_reminded(invoice_id):
    """Record that the customer was just sent a reminder about this invoice."""
    with Session(engine) as session:
        invoice = session.get(Invoice, invoice_id)
        invoice.reminded_at = now_utc()
        session.add(invoice)
        session.commit()


def mark_paid(client_id, number):
    """Mark this client's invoice as paid.

    Return "paid", "already paid", or None if they have no invoice with that number.
    """
    with Session(engine) as session:
        statement = select(Invoice).where(Invoice.client_id == client_id, Invoice.number == number)
        invoice = session.exec(statement).first()
        if invoice is None:
            return None
        if invoice.paid_at is not None:
            return "already paid"
        invoice.paid_at = now_utc()
        session.add(invoice)
        set_job_status(session, session.get(Quote, invoice.quote_id).job_id, "paid")
        session.commit()
        return "paid"


def ask_question(client_id, question, quote_id):
    """Remember that Jack asked this client something, replacing any older question."""
    with Session(engine) as session:
        statement = select(PendingQuestion).where(PendingQuestion.client_id == client_id)
        old = session.exec(statement).first()
        if old is not None:
            session.delete(old)
            # Delete first, so the new row doesn't clash with the old one's client_id.
            session.flush()
        session.add(PendingQuestion(client_id=client_id, question=question, quote_id=quote_id))
        session.commit()


def pop_question(client_id):
    """Forget this client's question and return (question, quote_id), or None.

    A question older than QUESTION_HOURS is forgotten without being returned.
    """
    with Session(engine) as session:
        statement = select(PendingQuestion).where(PendingQuestion.client_id == client_id)
        row = session.exec(statement).first()
        if row is None:
            return None
        # Read everything before deleting: after commit the row can't be read.
        result = (row.question, row.quote_id)
        # SQLite gives back times without a timezone, so add UTC before comparing.
        asked = row.time.replace(tzinfo=timezone.utc)
        session.delete(row)
        session.commit()
    if asked < now_utc() - timedelta(hours=QUESTION_HOURS):
        return None
    return result


def count_quotes_today(client_id):
    """Return how many quotes this client has made since midnight (UTC)."""
    midnight = now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
    with Session(engine) as session:
        statement = select(Quote.id).where(Quote.client_id == client_id, Quote.time >= midnight)
        return len(session.exec(statement).all())


def save_job(client_id, customer_name, work, customer_phone=None, address=None):
    """Save a new job and return its ID."""
    job = Job(
        client_id=client_id,
        customer_name=customer_name,
        work=work,
        customer_phone=customer_phone,
        address=address,
    )
    with Session(engine) as session:
        job.number = next_number(session, Job, client_id)
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.id


def book_job(job_id, starts_at, ends_on=None):
    """Book a job in for starts_at (any timezone; saved as UTC), maybe running to ends_on."""
    with Session(engine) as session:
        job = session.get(Job, job_id)
        job.starts_at = starts_at.astimezone(timezone.utc)
        job.ends_on = ends_on
        # A job that's already been invoiced or paid keeps that status.
        if job.status in ("logged", "quoted"):
            job.status = "booked"
        session.add(job)
        session.commit()


def booked_jobs(client_id, since):
    """Return this client's jobs booked to start in the 60 days before since, or later.

    A long job that started earlier can still be running, so the diary picks the days.
    """
    with Session(engine) as session:
        statement = select(Job).where(
            Job.client_id == client_id,
            Job.starts_at >= since - timedelta(days=60),
            Job.status != "cancelled",
        )
        return session.exec(statement).all()


def record_ai_call(client_id, purpose, model, input_tokens, output_tokens, seconds):
    """Save the details of one AI call."""
    with Session(engine) as session:
        session.add(
            AiCall(
                client_id=client_id,
                purpose=purpose,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                seconds=seconds,
            )
        )
        session.commit()


def ai_calls_since(since):
    """Return every AI call since a time, oldest first."""
    with Session(engine) as session:
        statement = select(AiCall).where(AiCall.time >= since).order_by(AiCall.time)
        return session.exec(statement).all()


def everything_for(client_id):
    """Return all of a client's jobs, quotes, invoices and prices, oldest first."""
    with Session(engine) as session:
        jobs = session.exec(select(Job).where(Job.client_id == client_id).order_by(Job.time)).all()
        quotes = session.exec(select(Quote).where(Quote.client_id == client_id).order_by(Quote.time)).all()
        invoices = session.exec(
            select(Invoice, Quote)
            .where(Invoice.quote_id == Quote.id, Invoice.client_id == client_id)
            .order_by(Invoice.number)
        ).all()
        customers = session.exec(select(Customer).where(Customer.client_id == client_id).order_by(Customer.name)).all()
    return {
        "jobs": jobs, "quotes": quotes, "invoices": invoices, "prices": get_prices(client_id),
        "customers": customers,
    }


def hash_token(token):
    """Return the hash we store in place of a secret token."""
    return hashlib.sha256(token.encode()).hexdigest()


def create_login_link(client_id):
    """Save a new one-time login link for this client, and return its secret token."""
    token = secrets.token_urlsafe(32)
    with Session(engine) as session:
        session.add(
            LoginLink(
                token_hash=hash_token(token),
                client_id=client_id,
                expires_at=now_utc() + timedelta(minutes=LOGIN_LINK_MINUTES),
            )
        )
        session.commit()
    return token


def use_login_link(token):
    """Use up a login link. Return its client ID, or None if it's unknown, used or expired."""
    with Session(engine) as session:
        statement = select(LoginLink).where(LoginLink.token_hash == hash_token(token))
        link = session.exec(statement).first()
        if link is None or link.used_at is not None or as_utc(link.expires_at) < now_utc():
            return None
        link.used_at = now_utc()
        session.add(link)
        session.commit()
        return link.client_id


def start_web_session(client_id):
    """Log a browser in for SESSION_DAYS. Return the secret token for its cookie."""
    token = secrets.token_urlsafe(32)
    with Session(engine) as session:
        session.add(
            WebSession(
                token_hash=hash_token(token),
                client_id=client_id,
                expires_at=now_utc() + timedelta(days=SESSION_DAYS),
            )
        )
        session.commit()
    return token


def client_for_session(token):
    """Return the active client logged in with this cookie, or None."""
    if not token:
        return None
    with Session(engine) as session:
        statement = select(WebSession).where(WebSession.token_hash == hash_token(token))
        web_session = session.exec(statement).first()
        if web_session is None or as_utc(web_session.expires_at) < now_utc():
            return None
        client = session.get(Client, web_session.client_id)
        # A paused client can't use their page either.
        return client if client is not None and client.active else None


def end_web_session(token):
    """Log this cookie out, if it's logged in."""
    with Session(engine) as session:
        statement = select(WebSession).where(WebSession.token_hash == hash_token(token or ""))
        web_session = session.exec(statement).first()
        if web_session is not None:
            session.delete(web_session)
            session.commit()


def name_key(name):
    """Return the key that says two names are the same customer, like "mrs jones"."""
    return name.strip().lower()


def customers_for(client_id):
    """Return this client's customers, from their jobs and quotes, most recent first.

    Each is a dict: id, name, phone, address, notes, how many jobs, and when we last
    saw them. A customer seen for the first time gets a Customer row, so they have an id.
    """
    data = everything_for(client_id)
    customers = {}
    # Oldest first, so the newest phone number and address win.
    records = sorted(data["jobs"] + data["quotes"], key=lambda record: as_utc(record.time))
    for record in records:
        customer = customers.setdefault(
            name_key(record.customer_name),
            {"name": record.customer_name, "phone": None, "address": None, "jobs": 0},
        )
        if isinstance(record, Job):
            customer["jobs"] += 1
            customer["phone"] = record.customer_phone or customer["phone"]
            customer["address"] = record.address or customer["address"]
        customer["last_seen"] = as_utc(record.time)

    with Session(engine) as session:
        saved = {row.name_key: row for row in session.exec(select(Customer).where(Customer.client_id == client_id))}
        for key, customer in customers.items():
            if key not in saved:
                saved[key] = Customer(client_id=client_id, name=customer["name"], name_key=key)
                session.add(saved[key])
        session.commit()
        for key, customer in customers.items():
            row = saved[key]
            # What the tradesperson typed on their page beats what was on the jobs.
            customer.update(
                id=row.id, notes=row.notes,
                phone=row.phone or customer["phone"], address=row.address or customer["address"],
            )
    return sorted(customers.values(), key=lambda customer: customer["last_seen"], reverse=True)


def get_customer(client_id, customer_id):
    """Return this client's customer with this id, with their jobs, quotes and invoices, or None.

    The client_id check means one client can never open another's customer.
    """
    for customer in customers_for(client_id):
        if customer["id"] == customer_id:
            break
    else:
        return None
    key = name_key(customer["name"])
    data = everything_for(client_id)
    customer["job_list"] = [job for job in reversed(data["jobs"]) if name_key(job.customer_name) == key]
    customer["quote_list"] = [quote for quote in reversed(data["quotes"]) if name_key(quote.customer_name) == key]
    customer["invoice_list"] = [
        (invoice, quote) for invoice, quote in reversed(data["invoices"]) if name_key(quote.customer_name) == key
    ]
    return customer


def save_customer_notes(client_id, customer_id, notes):
    """Save the notes for this client's customer."""
    with Session(engine) as session:
        row = session.get(Customer, customer_id)
        if row and row.client_id == client_id:
            row.notes = notes
            session.add(row)
            session.commit()


def save_customer_details(client_id, customer_id, phone, address):
    """Save a customer's phone and address, and put them on their open jobs too.

    Open jobs get them so Today's Call and Directions buttons use the new ones.
    Finished jobs and invoices keep what they had: they're a record of what happened.
    """
    with Session(engine) as session:
        row = session.get(Customer, customer_id)
        if row is None or row.client_id != client_id:
            return
        row.phone, row.address = phone, address
        session.add(row)
        jobs = session.exec(select(Job).where(Job.client_id == client_id, Job.status.in_(OPEN_JOB))).all()
        for job in jobs:
            if name_key(job.customer_name) == row.name_key:
                job.customer_phone, job.address = phone, address
                session.add(job)
        session.commit()


def get_job_by_number(client_id, number):
    """Return this client's job with this number (J4 is 4), or None."""
    with Session(engine) as session:
        statement = select(Job).where(Job.client_id == client_id, Job.number == number)
        return session.exec(statement).first()


def get_quote_by_number(client_id, number):
    """Return this client's quote with this number (Q12 is 12), or None."""
    with Session(engine) as session:
        statement = select(Quote).where(Quote.client_id == client_id, Quote.number == number)
        return session.exec(statement).first()


def quotes_for_customer(client_id, name):
    """Return this client's quotes for a customer, newest first. Ignores capitals."""
    with Session(engine) as session:
        quotes = session.exec(
            select(Quote).where(Quote.client_id == client_id).order_by(Quote.id.desc())
        ).all()
    return [quote for quote in quotes if same_customer(quote.customer_name, name)]


def same_customer(saved_name, typed_name):
    """Return True if a typed name matches a saved one.

    "jones" matches "Mrs Jones", and so does "mrs jones her quote", where the name
    is followed by other words.
    """
    saved, typed = saved_name.strip().lower(), typed_name.strip().lower()
    return typed != "" and saved != "" and (typed in saved or f"{saved} " in f"{typed} ")


# Jobs still to finish: done, invoiced, paid and cancelled jobs are past this.
OPEN_JOB = ("logged", "quoted", "booked")


def cancel_job(job_id):
    """Mark a job as cancelled. It's kept, but leaves the diary."""
    with Session(engine) as session:
        job = session.get(Job, job_id)
        job.status = "cancelled"
        session.add(job)
        session.commit()


def get_invoice_by_number(client_id, number):
    """Return this client's invoice with this number (INV-0244 is 244), or None."""
    with Session(engine) as session:
        statement = select(Invoice).where(Invoice.client_id == client_id, Invoice.number == number)
        return session.exec(statement).first()


# The tables UNDO can put back, by name.
UNDOABLE = {"job": Job, "quote": Quote, "invoice": Invoice}


def to_json_value(value):
    """Return a value that JSON can hold, keeping dates and times as tagged text."""
    if isinstance(value, datetime):
        return {"datetime": value.isoformat()}
    if isinstance(value, date):
        return {"date": value.isoformat()}
    return value


def from_json_value(value):
    """Undo to_json_value."""
    if isinstance(value, dict) and "datetime" in value:
        return datetime.fromisoformat(value["datetime"])
    if isinstance(value, dict) and "date" in value:
        return date.fromisoformat(value["date"])
    return value


def snapshot(table, row_id, fields):
    """Return how a row looks now, for the fields a change will touch."""
    with Session(engine) as session:
        row = session.get(UNDOABLE[table], row_id)
        state = {"table": table, "id": row_id, "fields": {f: to_json_value(getattr(row, f)) for f in fields}}
        if table == "quote":
            # A change rewrites a quote's lines too, so keep those.
            lines = session.exec(select(QuoteLine).where(QuoteLine.quote_id == row_id).order_by(QuoteLine.position))
            state["lines"] = [line.text for line in lines]
        return state


def remember_change(client_id, description, snapshots):
    """Keep the rows as they were before a change, so UNDO can put them back.

    Replaces whatever was remembered before: only the latest change can be undone.
    """
    with Session(engine) as session:
        old = session.exec(select(UndoStep).where(UndoStep.client_id == client_id)).first()
        if old is not None:
            session.delete(old)
            session.flush()
        session.add(UndoStep(client_id=client_id, description=description, before=json.dumps(snapshots)))
        session.commit()


def undo_last_change(client_id):
    """Put back the client's last remembered change. Return its description, or None."""
    with Session(engine) as session:
        step = session.exec(select(UndoStep).where(UndoStep.client_id == client_id)).first()
        if step is None:
            return None
        session.delete(step)
        if as_utc(step.time) < now_utc() - timedelta(hours=UNDO_HOURS):
            session.commit()
            return None
        for state in json.loads(step.before):
            row = session.get(UNDOABLE[state["table"]], state["id"])
            if row is None:
                # Deleted since, so there's nothing to put back.
                continue
            for field, value in state["fields"].items():
                setattr(row, field, from_json_value(value))
            session.add(row)
            if "lines" in state:
                old_lines = session.exec(select(QuoteLine).where(QuoteLine.quote_id == row.id))
                for line in old_lines:
                    session.delete(line)
                for position, text in enumerate(state["lines"]):
                    session.add(QuoteLine(quote_id=row.id, position=position, text=text))
        # One commit: the whole change comes back, or none of it.
        session.commit()
        return step.description


def jobs_for_customer(client_id, name, statuses=OPEN_JOB):
    """Return this client's jobs for a customer with one of these statuses, newest first."""
    with Session(engine) as session:
        jobs = session.exec(select(Job).where(Job.client_id == client_id).order_by(Job.id.desc())).all()
    return [job for job in jobs if job.status in statuses and same_customer(job.customer_name, name)]


def mark_job_done(job_id):
    """Mark a job as finished now. An invoiced or paid job keeps that status."""
    with Session(engine) as session:
        job = session.get(Job, job_id)
        job.done_at = now_utc()
        if job.status in OPEN_JOB:
            job.status = "done"
        session.add(job)
        session.commit()


def mark_quote_sent(quote_id, phone):
    """Record that a quote was texted to the customer at this mobile."""
    with Session(engine) as session:
        quote = session.get(Quote, quote_id)
        quote.customer_phone = phone
        quote.sent_at = now_utc()
        session.add(quote)
        session.commit()


def accept_quote(quote_id, review_ok=False):
    """Record that the customer accepted a quote, and whether they'll be asked for a review.

    Return False if they'd already accepted it.
    """
    with Session(engine) as session:
        quote = session.get(Quote, quote_id)
        if quote.accepted_at is not None:
            return False
        quote.accepted_at = now_utc()
        quote.review_ok = review_ok
        session.add(quote)
        session.commit()
        return True


# A review request goes this many hours after the job's marked done...
REVIEW_AFTER_HOURS = 20
# ...and not at all once this many days have passed.
REVIEW_WITHIN_DAYS = 7


def review_requests_due(now):
    """Return (job, quote) pairs whose customer said yes to a review and hasn't been asked yet."""
    with Session(engine) as session:
        statement = (
            select(Job, Quote)
            .where(Quote.job_id == Job.id, Quote.review_ok == True)  # noqa: E712
            .where(Job.done_at != None, Job.review_asked_at == None)  # noqa: E711
            .where(Job.done_at <= now - timedelta(hours=REVIEW_AFTER_HOURS))
            .where(Job.done_at >= now - timedelta(days=REVIEW_WITHIN_DAYS))
        )
        return session.exec(statement).all()


def mark_review_asked(job_id):
    """Record that the customer was asked for a review, so it isn't sent twice."""
    with Session(engine) as session:
        job = session.get(Job, job_id)
        job.review_asked_at = now_utc()
        session.add(job)
        session.commit()


def update_job(job_id, work, customer_phone, address, starts_at, ends_on):
    """Save a job's details as edited on the web page.

    starts_at None takes it out of the diary: it goes back to logged, or quoted if it has a quote.
    """
    with Session(engine) as session:
        job = session.get(Job, job_id)
        job.work, job.customer_phone, job.address = work, customer_phone, address
        if starts_at is not None:
            job.starts_at, job.ends_on = starts_at.astimezone(timezone.utc), ends_on
            if job.status in ("logged", "quoted"):
                job.status = "booked"
        elif job.starts_at is not None:
            job.starts_at = job.ends_on = None
            if job.status == "booked":
                has_quote = session.exec(select(Quote).where(Quote.job_id == job_id)).first()
                job.status = "quoted" if has_quote else "logged"
        session.add(job)
        session.commit()


def can_delete_job(job_id):
    """Return True if a job has no quotes, so deleting it loses no business records."""
    return not quotes_for_job(job_id)


def delete_job(client_id, job_id):
    """Delete this client's job for good, if it has no quotes. Return True if it went."""
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job is None or job.client_id != client_id or not can_delete_job(job_id):
            return False
        session.delete(job)
        session.commit()
        return True


def quotes_for_job(job_id):
    """Return every quote made for a job."""
    with Session(engine) as session:
        return session.exec(select(Quote).where(Quote.job_id == job_id)).all()


def quotes_waiting(client_id):
    """Return this client's quotes sent to a customer but not yet accepted, oldest first."""
    with Session(engine) as session:
        statement = (
            select(Quote)
            .where(Quote.client_id == client_id, Quote.sent_at != None)  # noqa: E711
            .where(Quote.accepted_at == None)  # noqa: E711
            .order_by(Quote.sent_at)
        )
        return session.exec(statement).all()


def link_quote_to_job(quote_id, job_id):
    """Say this quote is for this job, so paying its invoice marks the job paid too."""
    with Session(engine) as session:
        quote = session.get(Quote, quote_id)
        quote.job_id = job_id
        session.add(quote)
        session.commit()


def final_quote_for_job(job_id):
    """Return the newest final quote for a job, or None."""
    with Session(engine) as session:
        statement = (
            select(Quote)
            .where(Quote.job_id == job_id, Quote.status == "final")
            .order_by(Quote.id.desc())
        )
        return session.exec(statement).first()


def get_job(job_id):
    """Return the job with this ID, or None."""
    with Session(engine) as session:
        return session.get(Job, job_id)


def count_jobs_today(client_id):
    """Return how many jobs this client has logged since midnight (UTC)."""
    midnight = now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
    with Session(engine) as session:
        statement = select(Job.id).where(Job.client_id == client_id, Job.time >= midnight)
        return len(session.exec(statement).all())


def set_price(client_id, name, pence):
    """Save one of this client's prices, replacing the old one with that name."""
    name = name.strip().lower()
    with Session(engine) as session:
        statement = select(PriceItem).where(PriceItem.client_id == client_id, PriceItem.name == name)
        item = session.exec(statement).first() or PriceItem(client_id=client_id, name=name)
        item.pence = pence
        session.add(item)
        session.commit()


def set_markup(client_id, percent):
    """Save the percentage this client adds to materials on quotes."""
    with Session(engine) as session:
        client = session.get(Client, client_id)
        client.markup_percent = percent
        session.add(client)
        session.commit()


def delete_price(client_id, name):
    """Delete one of this client's prices. Return False if there wasn't one."""
    with Session(engine) as session:
        statement = select(PriceItem).where(
            PriceItem.client_id == client_id, PriceItem.name == name.strip().lower()
        )
        item = session.exec(statement).first()
        if item is None:
            return False
        session.delete(item)
        session.commit()
        return True


def get_prices(client_id):
    """Return this client's prices as (name, pence) pairs, in A to Z order."""
    with Session(engine) as session:
        statement = select(PriceItem).where(PriceItem.client_id == client_id).order_by(PriceItem.name)
        return [(item.name, item.pence) for item in session.exec(statement).all()]


def get_quote(quote_id):
    """Return the quote with this ID, or None."""
    with Session(engine) as session:
        return session.get(Quote, quote_id)


def get_quote_lines(quote_id):
    """Return the text of a quote's scope of works, in order."""
    with Session(engine) as session:
        statement = (
            select(QuoteLine)
            .where(QuoteLine.quote_id == quote_id)
            .order_by(QuoteLine.position)
        )
        return [line.text for line in session.exec(statement).all()]


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


def skip_numbers(client_id):
    """Return the numbers on this client's family and friends list."""
    with Session(engine) as session:
        statement = select(SkipNumber.number).where(SkipNumber.client_id == client_id).order_by(SkipNumber.number)
        return list(session.exec(statement).all())


def remove_skip_number(client_id, number):
    """Take a number off the client's family and friends list."""
    with Session(engine) as session:
        statement = select(SkipNumber).where(SkipNumber.client_id == client_id, SkipNumber.number == number)
        row = session.exec(statement).first()
        if row is not None:
            session.delete(row)
            session.commit()


def all_clients():
    """Return every client, newest first."""
    with Session(engine) as session:
        return session.exec(select(Client).order_by(Client.id.desc())).all()


def add_client(business_name, owner_mobile, twilio_number, text_back_message):
    """Add a client and return their ID, or None if that Twilio number is already used."""
    if get_client_by_number(twilio_number) is not None:
        return None
    client = Client(
        business_name=business_name,
        owner_mobile=owner_mobile,
        twilio_number=twilio_number,
        text_back_message=text_back_message,
    )
    with Session(engine) as session:
        session.add(client)
        session.commit()
        session.refresh(client)
        return client.id


def create_admin_link():
    """Save a one-time admin login link, and return its secret token."""
    token = secrets.token_urlsafe(32)
    with Session(engine) as session:
        session.add(AdminLogin(
            token_hash=hash_token(token), kind="link",
            expires_at=now_utc() + timedelta(minutes=LOGIN_LINK_MINUTES),
        ))
        session.commit()
    return token


def start_admin_session(link_token):
    """Use up an admin link and return a token for the admin cookie, or None if the link's no good."""
    with Session(engine) as session:
        statement = select(AdminLogin).where(
            AdminLogin.token_hash == hash_token(link_token), AdminLogin.kind == "link"
        )
        link = session.exec(statement).first()
        if link is None or link.used_at is not None or as_utc(link.expires_at) < now_utc():
            return None
        link.used_at = now_utc()
        session.add(link)
        token = secrets.token_urlsafe(32)
        session.add(AdminLogin(
            token_hash=hash_token(token), kind="session",
            expires_at=now_utc() + timedelta(hours=ADMIN_SESSION_HOURS),
        ))
        session.commit()
        return token


def is_admin(cookie):
    """Return True if this cookie is a live admin login."""
    if not cookie:
        return False
    with Session(engine) as session:
        statement = select(AdminLogin).where(
            AdminLogin.token_hash == hash_token(cookie), AdminLogin.kind == "session"
        )
        login = session.exec(statement).first()
        return login is not None and as_utc(login.expires_at) > now_utc()


def set_owner_name(client_id, name):
    """Save the owner's first name, for Jack's greetings."""
    with Session(engine) as session:
        client = session.get(Client, client_id)
        client.owner_name = name.strip()
        session.add(client)
        session.commit()


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


def delete_client(client_id):
    """Delete a client and everything they own, in one go. Return the rows deleted per table.

    Every table with a client_id is found automatically, so a table added later
    can't be forgotten. AI cost logs hold no personal data, so they're kept but
    unlinked from the client.
    """
    tables = SQLModel.metadata.tables
    counts = {}
    with Session(engine) as session:
        quote_ids = select(Quote.id).where(Quote.client_id == client_id)
        # Quote lines belong to quotes, not clients, so they go first.
        lines = session.exec(delete(QuoteLine).where(QuoteLine.quote_id.in_(quote_ids)))
        counts["quoteline"] = lines.rowcount
        session.exec(update(AiCall).where(AiCall.client_id == client_id).values(client_id=None))
        # Invoices point at quotes, so they go before them.
        owned = [name for name, table in tables.items() if "client_id" in table.columns and name != "aicall"]
        for name in sorted(owned, key=lambda name: name != "invoice"):
            table = tables[name]
            counts[name] = session.exec(delete(table).where(table.c.client_id == client_id)).rowcount
        counts["client"] = session.exec(delete(Client).where(Client.id == client_id)).rowcount
        # One commit: either everything goes, or nothing does.
        session.commit()
    return counts
