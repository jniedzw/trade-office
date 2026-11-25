"""The tradesperson's own web pages, logged into from a one-time WhatsApp link."""

import re
from pathlib import Path

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import settings
from alerts import try_send
from database import (
    accept_quote,
    get_client,
    get_quote,
    get_quote_lines,
    save_pending_alert,
    whatsapp_alerts_on,
    quotes_waiting,
    SESSION_DAYS,
    booked_jobs,
    client_for_session,
    customers_for,
    get_customer,
    get_job_by_number,
    get_invoice,
    save_customer_details,
    save_customer_notes,
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
from pdfs import make_invoice_pdf, make_quote_pdf
from sms import send_template
import whatsapp
from quotes import invoice_label, job_label, link_is_valid, money, quote_label, signed_link

router = APIRouter()
# Found next to this file, whichever folder the server is started from.
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

COOKIE = "session"


def page(request, name, status_code=200, **values):
    """Return one of the HTML templates, filled in with values."""
    values.setdefault("flash", flash_for(request))
    return templates.TemplateResponse(request, name, values, status_code=status_code)


def flash_for(request):
    """Return the green bar after a button was tapped, like "J4 marked done.", or None.

    Only a word and a number come in the address, never a name, so nothing personal
    ends up in web addresses or logs.
    """
    did, number = request.query_params.get("did"), request.query_params.get("n", "")
    number = int(number) if number.isdigit() else 0
    return {
        "done": f"{job_label(number)} marked done.",
        "paid": f"{invoice_label(number)} marked as paid.",
        "undone": "Undone.",
        "nothing": "There was nothing to undo.",
    }.get(did)


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
        # Only ask about a review if the business has somewhere to leave one.
        asks_for_reviews=bool(details and details.review_link),
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
        tell_owner_accepted(client, quote)
    return quote_page(request, get_quote(quote.id), client)


def tell_owner_accepted(client, quote):
    """Tell the owner a customer accepted, on WhatsApp if we can, otherwise by text."""
    label, total = quote_label(quote.number), money(quote.total_pence)
    text = f"Good news: {quote.customer_name} accepted quote {label} for {total}. Message Jack to book it in."
    if settings.WHATSAPP_NUMBER and settings.WHATSAPP_ACCEPTED_TEMPLATE and whatsapp_alerts_on(client.id):
        try:
            message_sid = send_template(
                client.owner_mobile, settings.WHATSAPP_ACCEPTED_TEMPLATE,
                {"1": quote.customer_name, "2": label, "3": total},
            )
            # Like missed-call alerts: if WhatsApp doesn't deliver it, the text goes instead.
            save_pending_alert(message_sid, client.id, text)
            return
        except Exception as error:
            print(f"WhatsApp accepted alert failed: {type(error).__name__}", flush=True)
    try_send(client.owner_mobile, text, client.twilio_number)


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


# Long enough for gate codes and parking, short enough to stay a note.
NOTES_MAX = 2000


def uk_phone(text):
    """Return a UK number as +44..., "" if text is blank, or None if it isn't a UK number."""
    digits = "".join(c for c in text if c.isdigit())
    if digits == "":
        return ""
    if digits.startswith("44"):
        digits = "0" + digits[2:]
    # UK numbers are 0 then 10 digits; 01, 02, 03 and 07 cover landlines and mobiles.
    if len(digits) == 11 and digits[:2] in ("01", "02", "03", "07"):
        return "+44" + digits[1:]
    return None


@router.get("/customers/{customer_id}", response_class=HTMLResponse)
def customer_page(request: Request, customer_id: int, saved: str = "", problem: str = ""):
    """One customer: their details, notes, and every job, quote and invoice."""
    client = logged_in_client(request)
    if client is None:
        return logged_out(request)
    customer = get_customer(client.id, customer_id)
    if customer is None:
        return page(request, "message.html", status_code=404, client=client,
                    heading="Customer not found", text="They may have been removed.")
    return page(
        request, "customer.html", client=client, customer=customer, tab="customers",
        saved=saved, problem=problem, money=money, quote_label=quote_label,
        invoice_label=invoice_label, day_name=lambda moment: day_name(to_uk(moment)),
    )


@router.post("/customers/{customer_id}/notes")
def customer_notes(request: Request, customer_id: int, notes: str = Form("")):
    client = logged_in_client(request)
    if client is None:
        return logged_out(request)
    save_customer_notes(client.id, customer_id, notes.strip()[:NOTES_MAX])
    return RedirectResponse(f"/customers/{customer_id}?saved=notes", status_code=303)


@router.post("/customers/{customer_id}/details")
def customer_details(request: Request, customer_id: int, phone: str = Form(""), address: str = Form("")):
    client = logged_in_client(request)
    if client is None:
        return logged_out(request)
    number = uk_phone(phone)
    if number is None:
        return RedirectResponse(f"/customers/{customer_id}?problem=phone", status_code=303)
    # Blank lines dropped, so the address is the same shape as one sent on WhatsApp.
    lines = [line.strip() for line in address.splitlines() if line.strip()]
    save_customer_details(client.id, customer_id, number or None, "\n".join(lines) or None)
    return RedirectResponse(f"/customers/{customer_id}?saved=details", status_code=303)


@router.get("/money", response_class=HTMLResponse)
def money_page(request: Request):
    client = logged_in_client(request)
    if client is None:
        return logged_out(request)
    days_to_pay = payment_days(client)
    owed = [
        {
            "number": invoice["number"],
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


def quote_pdf_for(quote):
    """Return a quote's PDF, made the same way wherever it's opened from."""
    business_name = get_client(quote.client_id).business_name
    details = get_business_details(quote.client_id)
    return make_quote_pdf(quote, business_name, get_quote_lines(quote.id), details)


def invoice_pdf_for(invoice):
    """Return an invoice's PDF, made the same way wherever it's opened from."""
    quote = get_quote(invoice.quote_id)
    business_name = get_client(invoice.client_id).business_name
    details = get_business_details(invoice.client_id)
    return make_invoice_pdf(invoice, quote, business_name, get_quote_lines(quote.id), details)


def pdf_response(pdf, filename):
    """Return a PDF that opens in the browser, with a sensible name if it's saved."""
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{filename}"'})


@router.get("/my/quotes/{quote_id}.pdf")
def my_quote_pdf(request: Request, quote_id: int):
    """A quote's PDF for the logged-in tradesperson. No signed link needed: the login is the check."""
    client = logged_in_client(request)
    if client is None:
        return logged_out(request)
    quote = get_quote(quote_id)
    # Someone else's quote gets the same answer as one that doesn't exist.
    if quote is None or quote.client_id != client.id:
        return page(request, "message.html", status_code=404, client=client,
                    heading="Quote not found", text="It may have been removed.")
    return pdf_response(quote_pdf_for(quote), f"{quote_label(quote.number)}.pdf")


@router.get("/my/invoices/{invoice_id}.pdf")
def my_invoice_pdf(request: Request, invoice_id: int):
    """An invoice's PDF for the logged-in tradesperson."""
    client = logged_in_client(request)
    if client is None:
        return logged_out(request)
    invoice = get_invoice(invoice_id)
    if invoice is None or invoice.client_id != client.id:
        return page(request, "message.html", status_code=404, client=client,
                    heading="Invoice not found", text="It may have been removed.")
    return pdf_response(invoice_pdf_for(invoice), f"{invoice_label(invoice.number)}.pdf")


def back_to(where, did, number=0):
    """Return a redirect to the page the button was on, saying what happened.

    where comes from the form, so only our own pages are allowed: anything else goes
    to Today, so a link elsewhere can't use this to send someone to another site.
    """
    if not (where in ("/me", "/money") or re.fullmatch(r"/customers/\d+", where)):
        where = "/me"
    return RedirectResponse(f"{where}?did={did}&n={number}", status_code=303)


@router.post("/jobs/{number}/done")
def job_done(request: Request, number: int, back: str = Form("/me")):
    """The Job done button: the same as sending DONE J4 on WhatsApp, so UNDO works too."""
    client = logged_in_client(request)
    if client is None:
        return logged_out(request)
    job = get_job_by_number(client.id, number)
    # Looked up by this client's own number, so nobody can finish someone else's job.
    if job is not None and job.done_at is None:
        whatsapp.done_reply(client, job_label(number))
    return back_to(back, "done", number)


@router.post("/invoices/{number}/paid")
def invoice_paid(request: Request, number: int, back: str = Form("/money")):
    """The Mark as paid button: the same as sending PAID INV-0244 on WhatsApp."""
    client = logged_in_client(request)
    if client is None:
        return logged_out(request)
    whatsapp.paid_reply(client, invoice_label(number))
    return back_to(back, "paid", number)


@router.post("/undo")
def undo(request: Request, back: str = Form("/me")):
    """Put back the last change, the same as sending UNDO on WhatsApp."""
    client = logged_in_client(request)
    if client is None:
        return logged_out(request)
    reply = whatsapp.undo_reply(client)
    return back_to(back, "undone" if reply.text.startswith("Undone") else "nothing")


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
