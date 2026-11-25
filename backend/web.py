"""The tradesperson's own web pages, logged into from a one-time WhatsApp link."""

from pathlib import Path

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from alerts import try_send
from database import (
    accept_quote,
    get_client,
    get_quote,
    get_quote_lines,
    quotes_waiting,
    SESSION_DAYS,
    booked_jobs,
    client_for_session,
    customers_for,
    end_web_session,
    everything_for,
    get_business_details,
    now_utc,
    start_web_session,
    unpaid_invoices,
    use_login_link,
)
from diary import day_name, to_uk, uk_now, week_days
from export import XLSX, make_export
from quotes import invoice_label, link_is_valid, money, quote_label, signed_link

router = APIRouter()
# Found next to this file, whichever folder the server is started from.
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

COOKIE = "session"


def page(request, name, status_code=200, **values):
    """Return one of the HTML templates, filled in with values."""
    return templates.TemplateResponse(request, name, values, status_code=status_code)


def logged_out(request):
    """The page anyone sees who isn't logged in."""
    return page(
        request, "message.html", status_code=401,
        heading="Log in from WhatsApp",
        text="Message Jack on WhatsApp and tap My page. Jack will send you a link to log in.",
    )


def customer_quote(request, quote_id, expires, sig):
    """Return the customer's quote, its client and details, or None if the link isn't right."""
    if not link_is_valid("customer", quote_id, expires, sig, now_utc()):
        return None
    quote = get_quote(quote_id)
    client = get_client(quote.client_id) if quote else None
    # A paused client's quotes can't be accepted, and drafts were never sent.
    if client is None or not client.active or quote.status != "final":
        return None
    return quote, client


def quote_page(request, quote, client):
    """The customer's quote page."""
    details = get_business_details(client.id)
    return page(
        request, "customer_quote.html", quote=quote, business_name=client.business_name,
        label=quote_label(quote.number), lines=get_quote_lines(quote.id), total=money(quote.total_pence),
        pdf_link=signed_link("quote", quote.id, now_utc()), contact=details.contact if details else "",
    )


def link_gone(request):
    """What a customer sees when their quote link is old or wrong."""
    return page(
        request, "message.html", status_code=403, heading="This link has expired",
        text="Please ask the tradesperson who sent it for a new one.",
    )


@router.get("/q/{quote_id}", response_class=HTMLResponse)
def see_quote(request: Request, quote_id: int, expires: int = 0, sig: str = ""):
    found = customer_quote(request, quote_id, expires, sig)
    return quote_page(request, *found) if found else link_gone(request)


@router.post("/q/{quote_id}", response_class=HTMLResponse)
def accept(request: Request, quote_id: int, expires: int = 0, sig: str = "", review_ok: str = Form("")):
    found = customer_quote(request, quote_id, expires, sig)
    if not found:
        return link_gone(request)
    quote, client = found
    # Only the first press tells the tradesperson, however many times it's pressed.
    if accept_quote(quote.id, review_ok=review_ok == "yes"):
        # A text, not WhatsApp: Jack can't start a WhatsApp chat without a template.
        try_send(
            client.owner_mobile,
            f"Good news: {quote.customer_name} accepted quote {quote_label(quote.number)} for "
            f"{money(quote.total_pence)}. Message Jack to book it in.",
            client.twilio_number,
        )
    return quote_page(request, get_quote(quote.id), client)


@router.get("/login/{token}", response_class=HTMLResponse)
def login_page(request: Request, token: str):
    # Only shows a button. Opening the link mustn't log in, or a link preview could use it up.
    return page(request, "login.html")


@router.post("/login/{token}")
def log_in(request: Request, token: str):
    client_id = use_login_link(token)
    if client_id is None:
        return page(
            request, "message.html", status_code=403,
            heading="That link has expired",
            text="Login links work once, for 15 minutes. Tap My page on WhatsApp for a new one.",
        )
    # 303 tells the browser to open /me next, so a refresh won't send the form again.
    response = RedirectResponse("/me", status_code=303)
    response.set_cookie(
        COOKIE,
        start_web_session(client_id),
        max_age=SESSION_DAYS * 24 * 60 * 60,
        # httponly: page scripts can't read it. secure: only sent over https.
        # samesite: other websites can't send it with their own forms.
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


@router.get("/me", response_class=HTMLResponse)
def me(request: Request):
    """The Today page: a summary, today's jobs, the rest of the week, and quotes waiting."""
    client = logged_in_client(request)
    if client is None:
        return logged_out(request)
    now = uk_now(now_utc())
    today = now.date()
    week = week_days(booked_jobs(client.id, now_utc()), today)
    todays = week[0][1] if week and week[0][0] == today else []
    invoices = unpaid_invoices(client.id)
    days_to_pay = payment_days(client)
    late = [invoice for invoice in invoices if (now_utc() - invoice["time"]).days > days_to_pay]
    return page(
        request, "me.html", client=client, today=today, todays=todays, tab="today",
        hello="Morning" if now.hour < 12 else "Afternoon" if now.hour < 17 else "Evening",
        coming_up=[(day, jobs) for day, jobs in week if day != today],
        week_count=len({job.id for _, jobs in week for _, job in jobs}),
        owed=money(sum(invoice["total_pence"] for invoice in invoices)) if invoices else None,
        late=len(late), waiting=quotes_waiting(client.id),
        money=money, quote_label=quote_label, day_name=day_name,
        sent_day=lambda moment: day_name(to_uk(moment)),
    )


def payment_days(client):
    """Return how many days this client gives customers to pay."""
    details = get_business_details(client.id)
    return details.payment_days if details else 14


def logged_in_client(request):
    """Return the client this browser is logged in as, or None."""
    return client_for_session(request.cookies.get(COOKIE))


@router.get("/customers", response_class=HTMLResponse)
def customers(request: Request):
    client = logged_in_client(request)
    if client is None:
        return logged_out(request)
    return page(
        request, "customers.html", client=client, customers=customers_for(client.id), tab="customers",
        day_name=lambda moment: day_name(to_uk(moment)),
    )


@router.get("/money", response_class=HTMLResponse)
def money_page(request: Request):
    client = logged_in_client(request)
    if client is None:
        return logged_out(request)
    days_to_pay = payment_days(client)
    owed = [
        {
            "label": invoice_label(invoice["number"]),
            "customer_name": invoice["customer_name"],
            "amount": money(invoice["total_pence"]),
            "overdue": (now_utc() - invoice["time"]).days - days_to_pay,
        }
        for invoice in unpaid_invoices(client.id)
    ]
    invoices = everything_for(client.id)["invoices"]
    # This month in UK time, so payments on the 1st just after midnight count.
    this_month = uk_now(now_utc()).strftime("%Y-%m")
    paid = sum(
        quote.total_pence for invoice, quote in invoices
        if invoice.paid_at and to_uk(invoice.paid_at).strftime("%Y-%m") == this_month
    )
    return page(
        request, "money.html", client=client, owed=owed, tab="money", invoices=list(reversed(invoices)),
        owed_total=money(sum(invoice["total_pence"] for invoice in unpaid_invoices(client.id))),
        paid_this_month=money(paid), money=money, invoice_label=invoice_label,
        day_name=lambda moment: day_name(to_uk(moment)),
    )


@router.get("/download")
def download(request: Request):
    client = logged_in_client(request)
    if client is None:
        return logged_out(request)
    spreadsheet = make_export(everything_for(client.id), client.markup_percent)
    # attachment tells the browser to save the file rather than try to show it.
    filename = f"ask-jack-{uk_now(now_utc()):%Y-%m-%d}.xlsx"
    return Response(
        content=spreadsheet, media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/logout")
def log_out(request: Request):
    end_web_session(request.cookies.get(COOKIE))
    response = RedirectResponse("/me", status_code=303)
    response.delete_cookie(COOKIE)
    return response
