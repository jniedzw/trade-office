"""The tradesperson's own web pages, logged into from a one-time WhatsApp link."""

from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import (
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
from quotes import invoice_label, money

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
    """The Today page: today's jobs, the rest of the week, and what's owed."""
    client = logged_in_client(request)
    if client is None:
        return logged_out(request)
    today = uk_now(now_utc()).date()
    week = week_days(booked_jobs(client.id, now_utc()), today)
    todays = week[0][1] if week and week[0][0] == today else []
    owed = sum(invoice["total_pence"] for invoice in unpaid_invoices(client.id))
    return page(
        request, "me.html", client=client, today=today, todays=todays,
        coming_up=[(day, jobs) for day, jobs in week if day != today],
        owed=money(owed) if owed else None, day_name=day_name,
    )


def logged_in_client(request):
    """Return the client this browser is logged in as, or None."""
    return client_for_session(request.cookies.get(COOKIE))


@router.get("/customers", response_class=HTMLResponse)
def customers(request: Request):
    client = logged_in_client(request)
    if client is None:
        return logged_out(request)
    return page(
        request, "customers.html", client=client, customers=customers_for(client.id),
        day_name=lambda moment: day_name(to_uk(moment)),
    )


@router.get("/money", response_class=HTMLResponse)
def money_page(request: Request):
    client = logged_in_client(request)
    if client is None:
        return logged_out(request)
    details = get_business_details(client.id)
    days_to_pay = details.payment_days if details else 14
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
        request, "money.html", client=client, owed=owed, invoices=list(reversed(invoices)),
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
