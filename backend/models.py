"""The database tables, one class each. database.py has the functions that use them.

Each class is a table: its fields are the columns. SQLModel builds the tables from these.
"""

from datetime import date, datetime, timezone

from sqlmodel import Field, SQLModel, UniqueConstraint


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
    # "starter" (missed calls only) or "pro" (everything, including Jack on WhatsApp).
    plan: str = "pro"


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
