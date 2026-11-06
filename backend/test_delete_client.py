from sqlmodel import Session, SQLModel, select

import database
from database import AiCall
from test_main import OWNER, use_fakes
from test_web import add_other_client, log_in


def fill_up(client_id):
    """Give a client at least one row in every table that can hold their data."""
    database.log_call(client_id, "+447700900123", True)
    database.add_skip_number(client_id, "+447700900111")
    database.opt_out(client_id, "+447700900222")
    database.set_whatsapp_alerts(client_id, True)
    database.save_pending_alert(f"SM_{client_id}", client_id, "alert")
    job_id = database.save_job(client_id, "Mrs Jones", "Radiator")
    quote_id = database.save_quote(client_id, "Mrs Jones", "Radiator", "RAD", 45000, ["Fit radiator"], job_id)
    database.finish_quote(client_id, quote_id)
    database.save_business_details(
        client_id, legal_name="Test", address="1 Test Street", ref_letters="TP", bank_details="Bank"
    )
    database.save_invoice(client_id, quote_id, "12 Test Road")
    database.set_price(client_id, "day rate", 25000)
    database.ask_question(client_id, "change", quote_id)
    database.create_login_link(client_id)
    database.start_web_session(client_id)
    database.record_ai_call(client_id, "QuoteDraft", "claude-sonnet-5-5", 900, 120, 1.5)


def rows_for(client_id):
    """Count every row still linked to a client, in every table."""
    with Session(database.engine) as session:
        total = 0
        for table in SQLModel.metadata.tables.values():
            if "client_id" in table.columns:
                total += len(session.exec(select(table).where(table.c.client_id == client_id)).all())
        quote_ids = select(database.Quote.id).where(database.Quote.client_id == client_id)
        total += len(session.exec(select(database.QuoteLine).where(database.QuoteLine.quote_id.in_(quote_ids))).all())
        return total


def test_deleting_a_client_leaves_nothing_behind(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    fill_up(1)
    log_in(OWNER)
    assert rows_for(1) > 15

    database.delete_client(1)

    assert rows_for(1) == 0
    assert database.get_client(1) is None


def test_every_table_with_client_data_is_filled_by_the_test(monkeypatch, tmp_path):
    # If a new table holds client data, fill_up must give it a row, or the test above proves nothing.
    use_fakes(monkeypatch, tmp_path)
    fill_up(1)
    with Session(database.engine) as session:
        empty = [
            name for name, table in SQLModel.metadata.tables.items()
            if "client_id" in table.columns
            and not session.exec(select(table).where(table.c.client_id == 1)).all()
        ]
    assert empty == []


def test_other_clients_are_untouched(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    add_other_client()
    fill_up(1)
    fill_up(2)
    before = rows_for(2)

    database.delete_client(1)

    assert rows_for(2) == before
    assert database.get_client(2).business_name == "Other Plumbing"


def test_ai_costs_are_kept_without_the_client(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path)
    fill_up(1)

    database.delete_client(1)

    with Session(database.engine) as session:
        [call] = session.exec(select(AiCall)).all()
    assert call.client_id is None
