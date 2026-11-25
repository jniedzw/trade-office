"""James's admin pages: every client in one place, instead of the Render Shell.

Logged into from a one-time link Jack sends when James messages ADMIN from his alert mobile.
"""

from datetime import timedelta

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ai import uk_mobile
from ai_costs import cost_in_dollars
from database import (
    ADMIN_SESSION_HOURS,
    add_client,
    add_skip_number,
    ai_calls_since,
    all_clients,
    delete_client,
    everything_for,
    get_business_details,
    get_client,
    is_admin,
    now_utc,
    remove_skip_number,
    save_business_details,
    skip_numbers,
    start_admin_session,
    update_client,
)
from set_business_details import is_colour, is_ref_letters
from web import page

router = APIRouter(prefix="/admin")

COOKIE = "admin"


def admin_only(request):
    """Return None if this browser is logged in as admin, or the page to show instead."""
    if is_admin(request.cookies.get(COOKIE)):
        return None
    return page(
        request, "message.html", status_code=401, heading="Admin login",
        text="Send ADMIN to Jack on WhatsApp from your alert mobile for a login link.",
    )


def back_to(path):
    """Go back to a page after saving, so a refresh won't send the form again."""
    return RedirectResponse(path, status_code=303)


@router.get("/login/{token}", response_class=HTMLResponse)
def login_page(request: Request, token: str):
    # A button, so WhatsApp's link previews can't use the link up.
    return page(request, "login.html")


@router.post("/login/{token}")
def log_in(request: Request, token: str):
    session_token = start_admin_session(token)
    if session_token is None:
        return page(
            request, "message.html", status_code=403, heading="That link has expired",
            text="Send ADMIN to Jack again for a new one.",
        )
    response = back_to("/admin")
    response.set_cookie(
        COOKIE, session_token, max_age=ADMIN_SESSION_HOURS * 60 * 60,
        # strict: the cookie is never sent from another site, even by following a link.
        httponly=True, secure=True, samesite="strict",
    )
    return response


@router.get("", response_class=HTMLResponse)
def clients_page(request: Request, error: str = ""):
    if (blocked := admin_only(request)) is not None:
        return blocked
    costs = {}
    for call in ai_calls_since(now_utc() - timedelta(days=30)):
        costs[call.client_id] = costs.get(call.client_id, 0) + cost_in_dollars(call)
    rows = []
    for client in all_clients():
        data = everything_for(client.id)
        rows.append({
            "client": client, "jobs": len(data["jobs"]), "quotes": len(data["quotes"]),
            "invoices": len(data["invoices"]), "ai_cost": f"${costs.get(client.id, 0):.2f}",
        })
    return page(request, "admin.html", rows=rows, error=error)


@router.post("/clients")
def new_client(
    request: Request,
    business_name: str = Form(""),
    owner_mobile: str = Form(""),
    twilio_number: str = Form(""),
):
    if (blocked := admin_only(request)) is not None:
        return blocked
    mobile = uk_mobile(owner_mobile, owner_mobile)
    number = twilio_number.replace(" ", "")
    if not business_name.strip() or mobile is None or not (number.startswith("+44") and number[1:].isdigit()):
        return clients_page(request, error="Please give a business name, a UK mobile, and a +44 Twilio number.")
    message = (
        f"Hi, it's {business_name.strip()}. Sorry we missed your call. "
        "What can we help with? We'll call you back today."
    )
    client_id = add_client(business_name.strip(), mobile, number, message)
    if client_id is None:
        return clients_page(request, error="That Twilio number already belongs to a client.")
    return back_to(f"/admin/clients/{client_id}")


@router.get("/clients/{client_id}", response_class=HTMLResponse)
def client_page(request: Request, client_id: int, error: str = "", saved: str = ""):
    if (blocked := admin_only(request)) is not None:
        return blocked
    client = get_client(client_id)
    if client is None:
        return back_to("/admin")
    return page(
        request, "admin_client.html", client_row=client, details=get_business_details(client_id),
        skip=skip_numbers(client_id), error=error, saved=saved,
    )


@router.post("/clients/{client_id}")
def save_client(
    request: Request,
    client_id: int,
    business_name: str = Form(""),
    owner_mobile: str = Form(""),
    text_back_message: str = Form(""),
    active: str = Form(""),
):
    if (blocked := admin_only(request)) is not None:
        return blocked
    mobile = uk_mobile(owner_mobile, owner_mobile)
    if not business_name.strip() or mobile is None or not text_back_message.strip():
        return client_page(request, client_id, error="Please give a business name, a UK mobile and a message.")
    update_client(client_id, business_name.strip(), mobile, text_back_message.strip(), active == "yes")
    return back_to(f"/admin/clients/{client_id}?saved=yes")


@router.post("/clients/{client_id}/details")
def save_details(
    request: Request,
    client_id: int,
    legal_name: str = Form(""),
    address: str = Form(""),
    ref_letters: str = Form(""),
    payment_days: str = Form("14"),
    bank_details: str = Form(""),
    contact: str = Form(""),
    brand_colour: str = Form("#b8975a"),
    invoice_start: str = Form("1"),
    review_link: str = Form(""),
):
    if (blocked := admin_only(request)) is not None:
        return blocked
    letters = ref_letters.strip().upper()
    if not (legal_name.strip() and address.strip() and bank_details.strip()):
        return client_page(request, client_id, error="Legal name, address and bank details are all needed.")
    if not is_ref_letters(letters) or not is_colour(brand_colour.strip()):
        return client_page(request, client_id, error="Use 2 to 4 reference letters, and a colour like #b8975a.")
    if not (payment_days.strip().isdigit() and invoice_start.strip().isdigit()):
        return client_page(request, client_id, error="Days to pay and the next invoice number must be numbers.")
    save_business_details(
        client_id, legal_name=legal_name.strip(), address=address.strip().replace("\r\n", "\n"),
        ref_letters=letters, payment_days=int(payment_days), bank_details=bank_details.strip().replace("\r\n", "\n"),
        contact=contact.strip(), brand_colour=brand_colour.strip(), invoice_start=int(invoice_start),
        review_link=review_link.strip(),
    )
    return back_to(f"/admin/clients/{client_id}?saved=yes")


@router.post("/clients/{client_id}/skip")
def add_skip(request: Request, client_id: int, number: str = Form("")):
    if (blocked := admin_only(request)) is not None:
        return blocked
    phone = uk_mobile(number, number) or (number.replace(" ", "") if number.replace(" ", "").startswith("+44") else None)
    if phone is None:
        return client_page(request, client_id, error="Use a UK number, like 07700 900123 or +441514960000.")
    add_skip_number(client_id, phone)
    return back_to(f"/admin/clients/{client_id}?saved=yes")


@router.post("/clients/{client_id}/skip/remove")
def remove_skip(request: Request, client_id: int, number: str = Form("")):
    if (blocked := admin_only(request)) is not None:
        return blocked
    remove_skip_number(client_id, number)
    return back_to(f"/admin/clients/{client_id}?saved=yes")


@router.post("/clients/{client_id}/delete")
def delete(request: Request, client_id: int, confirm: str = Form("")):
    if (blocked := admin_only(request)) is not None:
        return blocked
    client = get_client(client_id)
    # Typing the name makes it hard to delete the wrong client by accident.
    if client is None or confirm.strip() != client.business_name:
        return client_page(request, client_id, error="To delete, type the business name exactly.")
    delete_client(client_id)
    return back_to("/admin")
