"""Tests for Jack's WhatsApp commands: quotes, invoices, jobs, bookings and the menu."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

import database
import diary
import export
import menu
import quotes
import settings
import whatsapp
from database import Client
from test_main import (
    JAMES,
    MOBILE_CALLER,
    OWNER,
    client,
    path_of,
    signed_whatsapp,
    use_fakes,
    use_quote_links,
)
from test_web import add_other_client


def test_alerts_on_and_off_by_whatsapp(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", " alerts on ")
    assert text.startswith("Done")
    assert link is None
    assert database.whatsapp_alerts_on(1) is True

    whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "ALERTS OFF")
    assert database.whatsapp_alerts_on(1) is False


def test_a_stranger_cant_turn_alerts_on(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    whatsapp.whatsapp_reply(f"whatsapp:{MOBILE_CALLER}", "ALERTS ON")

    assert database.whatsapp_alerts_on(1) is False


def test_owner_can_save_a_quote_by_whatsapp(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    reply = whatsapp.whatsapp_reply(
        f"whatsapp:{OWNER}", "quote Mrs Jones | Bathroom refit | BR | 1250.50"
    )

    assert reply == whatsapp.Reply(
        "Draft quote Q1 saved: Bathroom refit for Mrs Jones, £1,250.50, 0 lines of work. "
        "Check it, then send FINAL Q1 for the version to send."
    )
    assert database.get_quote(1).total_pence == 125050


def test_a_badly_written_quote_gets_help(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    reply = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "QUOTE bathroom about 1250")

    assert reply == whatsapp.Reply(whatsapp.QUOTE_HELP)
    assert database.get_quote(1) is None


def test_a_stranger_cant_make_a_quote(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    reply = whatsapp.whatsapp_reply(
        f"whatsapp:{MOBILE_CALLER}", "QUOTE Mrs Jones | Bathroom | BR | 100"
    )

    assert reply == whatsapp.Reply(whatsapp.WHATSAPP_NOT_A_CLIENT)
    assert database.get_quote(1) is None


def test_a_quote_reply_links_to_its_pdf(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "QUOTE_LINK_SECRET", "test-secret")

    text, link, buttons = whatsapp.whatsapp_reply(
        f"whatsapp:{OWNER}", "QUOTE Mrs Jones | Bathroom refit | BR | 1250"
    )

    assert text.startswith("Draft quote Q1 saved")
    assert link.startswith(f"{settings.PUBLIC_URL}/quotes/1.pdf?expires=")
    # The link in the reply really downloads the PDF.
    assert client.get(path_of(link)).content.startswith(b"%PDF-")


def test_a_quote_with_lines_by_whatsapp(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = whatsapp.whatsapp_reply(
        f"whatsapp:{OWNER}",
        "QUOTE Mrs Jones | Bathroom refit | BR | 1250\n- Remove old suite\n- Fit basin",
    )

    assert "£1,250.00, 2 lines of work." in text
    assert database.get_quote_lines(1) == ["Remove old suite", "Fit basin"]


def test_quote_can_be_on_its_own_line(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = whatsapp.whatsapp_reply(
        f"whatsapp:{OWNER}", "Quote\nMrs Jones | Bathroom refit | BR | 1250"
    )

    assert text.startswith("Draft quote Q1 saved")


def test_a_rough_quote_is_read_by_the_ai(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    quote = {
        "customer_name": "Mrs Jones",
        "title": "Bathroom refit",
        "job_code": "BR",
        "total_pence": 125000,
        "lines": ["Remove old suite"],
    }
    monkeypatch.setattr(whatsapp, "read_quote", lambda details, **kwargs: quote)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "quote bathroom mrs jones 1250")

    assert text.startswith("Draft quote Q1 saved: Bathroom refit for Mrs Jones")
    assert database.get_quote_lines(1) == ["Remove old suite"]


def test_final_makes_a_quote_final(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "final 1")

    assert text == "Quote Q1 is final. Forward this PDF to Mrs Jones."
    assert link.startswith(f"{settings.PUBLIC_URL}/quotes/1.pdf")
    assert database.get_quote(1).status == "final"


def test_final_needs_a_number_or_a_name(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "FINAL")

    assert text.startswith("To finish a quote")
    assert link is None
    assert database.get_quote(1).status == "draft"


def test_final_for_a_quote_that_doesnt_exist(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "FINAL 9")

    assert text == "There's no quote Q9 for Test Plumbing."
    assert link is None


def test_a_client_cant_finish_another_clients_quote(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    other_owner = "+447700900790"
    with Session(database.engine) as session:
        session.add(
            Client(
                business_name="Other Plumbing",
                owner_mobile=other_owner,
                twilio_number="+447700900457",
                text_back_message="Hi",
            )
        )
        session.commit()

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{other_owner}", "FINAL 1")

    assert text == "There's no quote Q1 for Other Plumbing."
    assert database.get_quote(1).status == "draft"


ADDRESS = "\n12 Test Road\nLiverpool L1 0AA"


def use_final_quote(monkeypatch, tmp_path, with_details=True):
    """Save quote 1 as final, and the test client's business details."""
    use_quote_links(monkeypatch, tmp_path)
    database.finish_quote(1, 1)
    if with_details:
        database.save_business_details(
            1, legal_name="Test", address="1 Test Street", ref_letters="TP", bank_details="Test Bank"
        )


def test_invoice_saves_an_invoice(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "invoice 1" + ADDRESS)

    assert text == "Invoice INV-0001 saved for Mrs Jones, £1,250.50. Forward this PDF to them."
    assert link.startswith(f"{settings.PUBLIC_URL}/invoices/1.pdf?expires=")
    assert client.get(path_of(link)).content.startswith(b"%PDF-")
    invoice = database.get_invoice_for_quote(1)
    assert invoice.customer_address == "12 Test Road\nLiverpool L1 0AA"


def test_invoicing_a_quote_twice_gives_the_same_invoice(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)

    whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 1" + ADDRESS)
    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 1" + ADDRESS)

    assert text.startswith("Invoice INV-0001 saved")


def test_invoice_numbers_go_up_by_one(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)
    database.save_quote(1, "Mr Smith", "Boiler service", "BS", 8500)
    database.finish_quote(1, 2)

    whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 1" + ADDRESS)
    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 2" + ADDRESS)

    assert text.startswith("Invoice INV-0002 saved for Mr Smith, £85.00.")


def test_invoice_without_an_address_asks_for_one(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 1")
    assert text.startswith("What's Mrs Jones's address?")
    assert database.get_invoice_for_quote(1) is None

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "12 Test Road\nLiverpool")
    assert text.startswith("Invoice INV-0001 saved for Mrs Jones")
    assert database.get_invoice_for_quote(1).customer_address == "12 Test Road\nLiverpool"


def test_a_command_instead_of_an_answer_forgets_the_question(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)
    whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 1")

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "QUOTE Mr Smith | Boiler | BS | 85")
    assert text.startswith("Draft quote Q2 saved")

    # The question's gone, so an address now is just a message, not an invoice.
    whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "12 Test Road")
    assert database.get_invoice_for_quote(1) is None


def test_an_old_question_is_forgotten(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)
    database.ask_question(1, "invoice_address", 1)
    with Session(database.engine) as session:
        row = session.exec(select(database.PendingQuestion)).first()
        row.time = database.now_utc() - timedelta(hours=25)
        session.add(row)
        session.commit()

    whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "12 Test Road")

    assert database.get_invoice_for_quote(1) is None


def test_make_invoice_on_an_invoiced_quote_resends_it(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)
    whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 1" + ADDRESS)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 1")

    assert text.startswith("Invoice INV-0001 saved")


def test_a_draft_cant_be_invoiced(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 1" + ADDRESS)

    assert text == "Quote Q1 is still a draft. Send FINAL Q1 first."


def test_invoice_without_business_details_alerts_james(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "QUOTE_LINK_SECRET", "test-secret")
    database.save_quote(1, "Mrs Jones", "Bathroom refit", "BR", 125050)
    database.finish_quote(1, 1)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 1" + ADDRESS)

    assert text.startswith("Your invoice details aren't set up yet")
    assert texted_numbers == [JAMES]
    assert database.get_invoice_for_quote(1) is None


def test_a_client_cant_invoice_another_clients_quote(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)
    other_owner = "+447700900790"
    with Session(database.engine) as session:
        session.add(
            Client(
                business_name="Other Plumbing",
                owner_mobile=other_owner,
                twilio_number="+447700900457",
                text_back_message="Hi",
            )
        )
        session.commit()

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{other_owner}", "INVOICE 1" + ADDRESS)

    assert text == "There's no quote Q1 for Other Plumbing."
    assert database.get_invoice_for_quote(1) is None


def test_a_quote_link_cant_open_an_invoice(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)
    whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 1" + ADDRESS)
    quote_link = quotes.signed_link("quote", 1, database.now_utc())

    response = client.get(path_of(quote_link).replace("/quotes/", "/invoices/"))

    assert response.status_code == 403


def use_buttons(monkeypatch, failing=False):
    """Turn button templates on, and return a list of WhatsApp messages "sent"."""
    sent = []
    monkeypatch.setattr(settings, "WHATSAPP_DRAFT_BUTTONS", "HX_DRAFT")
    monkeypatch.setattr(settings, "WHATSAPP_FINAL_BUTTONS", "HX_FINAL")

    def fake_send_whatsapp(to, body="", media_url=None):
        sent.append(("pdf", media_url is not None))

    def fake_send_whatsapp_buttons(to, template, variables):
        if failing:
            raise RuntimeError("Twilio is unhappy")
        sent.append((template, variables.get("2")))

    monkeypatch.setattr(whatsapp, "send_whatsapp", fake_send_whatsapp)
    monkeypatch.setattr(whatsapp, "send_whatsapp_buttons", fake_send_whatsapp_buttons)
    return sent


def test_a_draft_quote_gets_make_final_buttons(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    sent = use_buttons(monkeypatch)

    response = signed_whatsapp(OWNER, "QUOTE Mr Smith | Boiler service | BS | 85")

    # The PDF first, then the buttons for the new quote, and nothing in the response.
    assert sent == [("pdf", True), ("HX_DRAFT", "2")]
    assert "<Message>" not in response.text


def test_a_final_quote_gets_make_invoice_buttons(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    sent = use_buttons(monkeypatch)

    signed_whatsapp(OWNER, "FINAL 1")

    assert sent == [("pdf", True), ("HX_FINAL", "1")]


def test_a_plain_reply_if_buttons_fail(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    use_buttons(monkeypatch, failing=True)

    response = signed_whatsapp(OWNER, "FINAL 1")

    # The PDF already went, so the plain reply is just the words.
    assert "Quote Q1 is final" in response.text
    assert "<Media>" not in response.text


def test_quotes_stop_at_the_daily_limit(monkeypatch, tmp_path):
    texted_numbers = use_fakes(monkeypatch, tmp_path)
    monkeypatch.setattr(whatsapp, "MAX_QUOTES_PER_DAY", 2)
    for _ in range(2):
        whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "QUOTE Mr Smith | Boiler | BS | 85")

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "QUOTE Mr Smith | Boiler | BS | 85")

    assert text.startswith("That's 2 quotes today")
    assert database.get_quote(3) is None
    assert texted_numbers == [JAMES]


def use_fake_change(monkeypatch, changed):
    """Make the AI's change return changed, without calling the real AI."""
    monkeypatch.setattr(whatsapp, "change_quote", lambda quote, lines, change: changed)


CHANGED = {
    "customer_name": "Mrs Jones",
    "title": "Bathroom refit",
    "job_code": "BR",
    "total_pence": 130000,
    "lines": ["Remove old suite", "Tile the floor"],
}


def test_change_updates_a_draft(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    use_fake_change(monkeypatch, CHANGED)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "change 1 price is 1300, add tiling")

    assert text.startswith("Draft quote Q1 changed: Bathroom refit for Mrs Jones, £1,300.00, 2 lines")
    assert link.startswith(f"{settings.PUBLIC_URL}/quotes/1.pdf")
    assert database.get_quote(1).total_pence == 130000
    assert database.get_quote_lines(1) == ["Remove old suite", "Tile the floor"]


def test_tapping_change_asks_what_to_change(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    use_fake_change(monkeypatch, CHANGED)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "CHANGE 1")
    assert text == "What needs changing on quote Q1?"

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "price is 1300")
    assert text.startswith("Draft quote Q1 changed")


def test_changing_a_final_quote_makes_it_a_draft(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    use_fake_change(monkeypatch, CHANGED)
    database.finish_quote(1, 1)

    whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "CHANGE 1 price is 1300")

    assert database.get_quote(1).status == "draft"


def test_an_invoiced_quote_cant_change(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)
    use_fake_change(monkeypatch, CHANGED)
    whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE 1" + ADDRESS)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "CHANGE 1 price is 1300")

    assert "can't change" in text
    assert database.get_quote(1).total_pence == 125050


def test_a_change_the_ai_cant_make(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    use_fake_change(monkeypatch, None)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "CHANGE 1 make it cheaper")

    assert text.startswith("Sorry, I couldn't make that change")
    assert database.get_quote(1).total_pence == 125050


def test_a_client_cant_change_another_clients_quote(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    use_fake_change(monkeypatch, CHANGED)
    other_owner = "+447700900790"
    with Session(database.engine) as session:
        session.add(
            Client(
                business_name="Other Plumbing",
                owner_mobile=other_owner,
                twilio_number="+447700900457",
                text_back_message="Hi",
            )
        )
        session.commit()

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{other_owner}", "CHANGE 1 price 1300")

    assert text == "There's no quote Q1 for Other Plumbing."
    assert database.get_quote(1).total_pence == 125050


def test_saving_changing_and_listing_prices(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "price Boiler Service 85")
    assert text == "Saved: boiler service, £85.00."
    whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "PRICE boiler service £90")
    whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "PRICE gas safety cert 65")

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "PRICES")
    assert text == "Your prices:\nboiler service: £90.00\ngas safety cert: £65.00\nMaterials markup: 0%"


def test_deleting_a_price(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "PRICE boiler service 85")

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "PRICE boiler service delete")
    assert text == "Deleted the price for boiler service."
    assert database.get_prices(1) == []

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "PRICE boiler service DELETE")
    assert text.startswith("There's no price for boiler service")


def test_a_badly_written_price_gets_help(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    for message in ["PRICE", "PRICE 85", "PRICE boiler service", "PRICE boiler service abc"]:
        text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", message)
        assert text == whatsapp.PRICE_HELP, message
    assert database.get_prices(1) == []


def test_no_prices_yet(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "PRICES")

    assert text.startswith("You haven't saved any prices yet.")


def test_is_new_command():
    assert whatsapp.is_new_command("QUOTE MRS JONES | BATHROOM | BR | 1250") is True
    assert whatsapp.is_new_command("FINAL 6") is True
    assert whatsapp.is_new_command("CHANGE 6 PRICE IS 1300") is True
    assert whatsapp.is_new_command("PRICES") is True
    # Answers that happen to start with a command's word.
    assert whatsapp.is_new_command("CHANGE THE PRICE TO 1300") is False
    assert whatsapp.is_new_command("PRICE IS 1300") is False
    assert whatsapp.is_new_command("FINAL PRICE 1300") is False
    assert whatsapp.is_new_command("12 ELM ROAD") is False


def test_hi_gets_the_menu(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    sent = use_buttons(monkeypatch)
    monkeypatch.setattr(settings, "WHATSAPP_MENU", "HX_MENU")

    response = signed_whatsapp(OWNER, "Morning")

    assert sent == [("HX_MENU", None)]
    assert "<Message>" not in response.text


def test_without_the_menu_template_hi_gets_help(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "hello")

    assert text == whatsapp.HELP_TEXT


def test_new_quote_from_the_menu_asks_about_the_job(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    # The list menu's choice, by its ID or just its title.
    for tapped in ["QUOTE", "New quote"]:
        text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", tapped)
        assert text.startswith("Tell me about the job")

    text, link, buttons = whatsapp.whatsapp_reply(
        f"whatsapp:{OWNER}", "Mrs Jones | Bathroom refit | BR | 1250"
    )
    assert text.startswith("Draft quote Q1 saved")


def test_every_command_is_in_the_menu_or_marked_as_not():
    # If this fails, a new command needs adding to MENU_CHOICES or NOT_IN_MENU in menu.py.
    in_menu = {command for _, command, _ in menu.MENU_CHOICES}
    assert set(whatsapp.COMMANDS) == in_menu | set(menu.NOT_IN_MENU)


def test_log_a_typed_job(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = whatsapp.whatsapp_reply(
        f"whatsapp:{OWNER}", "LOG Mrs Jones | 12 Test Road, Liverpool | Leaking radiator"
    )

    assert text == "Job J1 logged: Mrs Jones, Leaking radiator.\nAddress: 12 Test Road, Liverpool"
    job = database.get_job(1)
    assert job.address == "12 Test Road\nLiverpool"
    assert job.status == "logged"


def test_log_a_job_from_the_menu(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    job = {
        "customer_name": "Mrs Jones",
        "work": "Leaking radiator",
        "customer_phone": "+447700900123",
        "address": None,
    }
    monkeypatch.setattr(whatsapp, "read_job", lambda details: job)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "Log a job")
    assert text.startswith("Tell me about the job")

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "mrs jones leaking rad 07700 900123")
    assert text == "Job J1 logged: Mrs Jones, Leaking radiator.\nPhone: +447700900123"


def test_a_job_jack_cant_read_gets_help(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "LOG something")

    assert text == whatsapp.LOG_HELP
    assert database.get_job(1) is None


def test_alerts_needs_on_or_off(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "ALERTS please")

    assert text.startswith("Send ALERTS ON")


def log_test_job(address="12 Test Road\nLiverpool"):
    """Save job 1 for the test client, the way LOG would."""
    return database.save_job(1, "Mrs Jones", "Leaking radiator", address=address)


def test_quote_it_turns_a_job_into_a_quote(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    log_test_job()
    seen = []

    def fake_read_quote(details, typed=None, **kwargs):
        seen.append(typed)
        return {"customer_name": "Someone else", "title": "Radiator", "job_code": "RAD",
                "total_pence": 45000, "lines": ["Fit new radiator"]}

    monkeypatch.setattr(whatsapp, "read_quote", fake_read_quote)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "QUOTE JOB 1")
    assert text.startswith("What's the price for Mrs Jones's job")

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "450, fit new radiator")
    # Quote 2, because use_quote_links already saved quote 1.
    assert text.startswith("Draft quote Q2 saved: Radiator for Mrs Jones, £450.00")
    # Only the tradesperson's answer is used for the price check.
    assert seen == ["450, fit new radiator"]
    assert database.get_quote(2).job_id == 1
    assert database.get_job(1).status == "quoted"


def test_invoicing_a_job_quote_uses_the_jobs_address(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)
    log_test_job()
    quote_id = database.save_quote(1, "Mrs Jones", "Radiator", "RAD", 45000, job_id=1)
    database.finish_quote(1, quote_id)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", f"INVOICE {quote_id}")

    assert text.startswith("Invoice INV-0001 saved for Mrs Jones")
    assert database.get_invoice_for_quote(quote_id).customer_address == "12 Test Road\nLiverpool"


def test_a_client_cant_quote_another_clients_job(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    log_test_job()
    other_owner = "+447700900790"
    with Session(database.engine) as session:
        session.add(
            Client(
                business_name="Other Plumbing",
                owner_mobile=other_owner,
                twilio_number="+447700900457",
                text_back_message="Hi",
            )
        )
        session.commit()

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{other_owner}", "QUOTE JOB 1")

    assert text == "There's no job J1 for Other Plumbing."


def test_a_logged_job_gets_a_quote_it_button(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    sent = use_buttons(monkeypatch)
    monkeypatch.setattr(settings, "WHATSAPP_JOB_BUTTONS", "HX_JOB")

    signed_whatsapp(OWNER, "LOG Mrs Jones | Leaking radiator")

    assert sent == [("HX_JOB", "1")]


def make_invoice(quote_id, days_ago=0):
    """Invoice a final quote for the test client, dated days_ago days back."""
    invoice = database.save_invoice(1, quote_id, "12 Test Road")
    with Session(database.engine) as session:
        row = session.get(database.Invoice, invoice.id)
        row.time = database.now_utc() - timedelta(days=days_ago)
        session.add(row)
        session.commit()
    return invoice.number


def test_who_owes_me_lists_unpaid_invoices_oldest_first(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)  # quote 1, Mrs Jones £1,250.50, 14 days to pay
    database.save_quote(1, "Mr Smith", "Boiler service", "BS", 8500)
    make_invoice(2, days_ago=3)
    make_invoice(1, days_ago=20)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "Who owes me")

    assert text == (
        "Who owes you:\n"
        "INV-0002: Mrs Jones, £1,250.50, 6 days overdue\n"
        "INV-0001: Mr Smith, £85.00\n"
        "Total: £1,335.50"
    )


def test_nobody_owes_anything(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "OWED")

    assert text.startswith("Nobody owes you anything")


def test_paid_marks_an_invoice_and_its_job(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)
    log_test_job()
    quote_id = database.save_quote(1, "Mrs Jones", "Radiator", "RAD", 45000, job_id=1)
    make_invoice(quote_id)
    assert database.get_job(1).status == "invoiced"

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "paid 1")
    assert text == "INV-0001 marked as paid."
    assert database.unpaid_invoices(1) == []
    assert database.get_job(1).status == "paid"

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "PAID 1")
    assert text == "INV-0001 was already marked as paid."


def test_mark_as_paid_from_the_menu_asks_which(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)
    make_invoice(1)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "Mark as paid")
    assert text.startswith("Who owes you:\nINV-0001: Mrs Jones")
    assert text.endswith("Which invoice has been paid? Send its number or the customer's name.")

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "1")
    assert text == "INV-0001 marked as paid."


def test_paid_for_an_invoice_that_isnt_theirs(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "PAID 7")

    assert text == "There's no invoice INV-0007 for Test Plumbing."


MONDAY_9AM = datetime(2026, 11, 9, 9, 0, tzinfo=ZoneInfo("Europe/London"))


def test_book_it_books_a_logged_job(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    log_test_job()
    monkeypatch.setattr(whatsapp, "read_booking", lambda message, now_uk: (MONDAY_9AM, None))

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "BOOK JOB 1")
    assert text.startswith("When's Mrs Jones's job?")

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "monday 9am")
    assert text == "Booked: Mrs Jones, Leaking radiator, Mon 9 Nov, 9am."
    assert database.get_job(1).status == "booked"


def test_an_unclear_time_asks_again(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    log_test_job()

    whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "BOOK JOB 1")
    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "sometime soon")
    assert "I couldn't tell from that" in text

    # Still waiting for the time, so the next answer books it.
    monkeypatch.setattr(whatsapp, "read_booking", lambda message, now_uk: (MONDAY_9AM, None))
    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "monday 9am")
    assert text.startswith("Booked: Mrs Jones")


def test_book_a_new_job_in_one_message(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    job = {"customer_name": "Mr Smith", "work": "Boiler service", "customer_phone": None, "address": None}
    monkeypatch.setattr(whatsapp, "read_job", lambda details: job)
    monkeypatch.setattr(whatsapp, "read_booking", lambda message, now_uk: (MONDAY_9AM, None))

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "BOOK Mr Smith boiler service monday 9am")

    assert text == "Booked: Mr Smith, Boiler service, Mon 9 Nov, 9am."
    assert database.get_job(1).status == "booked"


def test_a_client_cant_book_another_clients_job(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    log_test_job()
    other_owner = "+447700900790"
    with Session(database.engine) as session:
        session.add(
            Client(
                business_name="Other Plumbing",
                owner_mobile=other_owner,
                twilio_number="+447700900457",
                text_back_message="Hi",
            )
        )
        session.commit()

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{other_owner}", "BOOK JOB 1")

    assert text == "There's no job J1 for Other Plumbing."


def test_week_lists_booked_jobs(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    log_test_job()
    tomorrow = diary.uk_now(database.now_utc()).date() + timedelta(days=1)
    tomorrow_2pm = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 14, tzinfo=diary.UK)
    database.book_job(1, tomorrow_2pm)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "What's on this week")

    assert text.startswith("Your week:\n")
    assert "  2pm Mrs Jones: Leaking radiator (12 Test Road)" in text


def test_nothing_booked(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "WEEK")

    assert text == "Nothing booked for the next 7 days."


def test_markup(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "MARKUP 15%")
    assert text == "Saved: 15% added to materials on your quotes."
    assert database.get_client(1).markup_percent == 15

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "MARKUP lots")
    assert text.startswith("To set your materials markup")


def test_export_sends_the_spreadsheet(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "Export my data")

    assert text.startswith("Here's all your data")
    assert link.startswith(f"{settings.PUBLIC_URL}/exports/1.xlsx?expires=")
    response = client.get(path_of(link))
    assert response.status_code == 200
    assert response.headers["content-type"] == export.XLSX


def test_an_export_link_stops_working_after_an_hour(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    two_hours_ago = database.now_utc() - timedelta(hours=2)
    link = quotes.signed_link("export", 1, two_hours_ago, lasts=export.LINK_LASTS, extension="xlsx")

    assert client.get(path_of(link)).status_code == 403


def test_a_quote_link_cant_open_an_export(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)
    quote_link = quotes.signed_link("quote", 1, database.now_utc())

    response = client.get(path_of(quote_link).replace("/quotes/1.pdf", "/exports/1.xlsx"))

    assert response.status_code == 403


def add_quote(client_id, name, pence=8500, final=True):
    """Save a quote, maybe make it final, and return its database ID."""
    quote_id = database.save_quote(client_id, name, "Work", "WK", pence)
    if final:
        database.finish_quote(client_id, quote_id)
    return quote_id


def test_each_client_numbers_from_one(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    add_other_client()
    add_quote(1, "Mrs Jones")
    add_quote(1, "Mr Smith")
    database.save_job(1, "Mrs Jones", "Radiator")

    # Client 2's first quote and job are Q1 and J1, whatever client 1 has done.
    their_quote = database.get_quote(add_quote(2, "Their Customer"))
    their_job = database.get_job(database.save_job(2, "Their Customer", "Tap"))

    assert (their_quote.number, their_job.number) == (1, 1)
    assert database.get_quote_by_number(1, 2).customer_name == "Mr Smith"


def test_old_jobs_and_quotes_get_numbers(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    for name in ["First", "Second"]:
        add_quote(1, name)
    with Session(database.engine) as session:
        for quote in session.exec(select(database.Quote)).all():
            quote.number = None
            session.add(quote)
        session.commit()

    database.create_tables()

    assert [database.get_quote_by_number(1, n).customer_name for n in (1, 2)] == ["First", "Second"]


def test_invoices_carry_on_from_the_tradespersons_own_numbers(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    database.save_business_details(
        1, legal_name="Test", address="1 Test Street", ref_letters="TP", bank_details="Bank", invoice_start=244
    )

    first = database.save_invoice(1, add_quote(1, "Mrs Jones"), "12 Test Road")
    second = database.save_invoice(1, add_quote(1, "Mr Smith"), "13 Test Road")

    assert (first.number, second.number) == (244, 245)


def test_labels_can_be_typed(monkeypatch, tmp_path):
    use_final_quote(monkeypatch, tmp_path)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE q1" + ADDRESS)
    assert text.startswith("Invoice INV-0001 saved")

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "paid inv-0001")
    assert text == "INV-0001 marked as paid."


def test_final_and_invoice_by_customer_name(monkeypatch, tmp_path):
    use_quote_links(monkeypatch, tmp_path)  # Q1 is a draft for Mrs Jones
    database.save_business_details(
        1, legal_name="Test", address="1 Test Street", ref_letters="TP", bank_details="Bank"
    )

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "FINAL mrs jones")
    assert text.startswith("Quote Q1 is final")

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE jones" + ADDRESS)
    assert text.startswith("Invoice INV-0001 saved for Mrs Jones")


def test_paid_by_customer_name(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    database.save_invoice(1, add_quote(1, "Mrs Jones"), "12 Test Road")

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "PAID Mrs Jones")

    assert text == "INV-0001 marked as paid."


def test_a_name_with_more_than_one_match_asks_which(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    database.save_invoice(1, add_quote(1, "Mrs Jones", 8500), "12 Test Road")
    database.save_invoice(1, add_quote(1, "Mrs Jones", 9900), "12 Test Road")

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "PAID jones")

    assert text.startswith("jones has more than one unpaid invoice: INV-0001 (£85.00), INV-0002 (£99.00)")
    assert database.unpaid_invoices(1) != []


def test_a_name_with_no_match(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "INVOICE mr nobody")

    assert text == "There's no final quote for mr nobody."


def test_jobs_can_be_typed_as_j_numbers(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    log_test_job()

    text, link, buttons = whatsapp.whatsapp_reply(f"whatsapp:{OWNER}", "QUOTE J1")

    assert text.startswith("What's the price for Mrs Jones's job")


def test_typed_labels_count_as_new_commands():
    assert whatsapp.is_new_command("FINAL Q6") is True
    assert whatsapp.is_new_command("PAID INV-0244") is True
    # A name could be part of an answer, like "final price 1300".
    assert whatsapp.is_new_command("FINAL PRICE 1300") is False
