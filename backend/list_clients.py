"""Show every client in the database.

Run it where the database lives (on Render, in the Shell):
    python list_clients.py
It shows real phone numbers, so don't paste the output anywhere public.
"""

from sqlmodel import Session, select

from database import Client, create_tables, engine


def describe(client):
    """Return one line describing a client."""
    status = "active" if client.active else "PAUSED"
    return (
        f"{client.id}: {client.business_name} ({status}) | "
        f"Twilio {client.twilio_number} | owner {client.owner_mobile}"
    )


def main():
    create_tables()
    with Session(engine) as session:
        clients = session.exec(select(Client).order_by(Client.id)).all()

    if not clients:
        print("No clients yet. Add one with: python add_client.py")
        return

    for client in clients:
        print(describe(client))
        print(f"    Text-back: {client.text_back_message}")


if __name__ == "__main__":
    main()
