"""The tradesperson's own web pages, logged into from a one-time WhatsApp link."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import (
    SESSION_DAYS,
    client_for_session,
    end_web_session,
    start_web_session,
    use_login_link,
)

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
    client = client_for_session(request.cookies.get(COOKIE))
    if client is None:
        return logged_out(request)
    return page(request, "me.html", client=client)


@router.post("/logout")
def log_out(request: Request):
    end_web_session(request.cookies.get(COOKIE))
    response = RedirectResponse("/me", status_code=303)
    response.delete_cookie(COOKIE)
    return response
