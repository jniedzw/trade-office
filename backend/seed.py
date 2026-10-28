"""Add two fake clients for local testing. Safe to run more than once."""

from sqlmodel import Session

from database import Client, create_tables, engine, get_client_by_number

# Ofcom's fictional numbers: reserved for testing, never belong to anyone
FAKE_CLIENTS = [
    {
        "business_name": "Test Plumbing",
        "owner_mobile": "+447700900101",
        "twilio_number": "+447700900001",
        "text_back_message": "Hi, it's Test Plumbing. Sorry we missed your call. What can we help with?",
    },
    {
        "business_name": "Test Electrical",
        "owner_mobile": "+447700900102",
        "twilio_number": "+447700900002",
        "text_back_message": "Hi, it's Test Electrical. Sorry we missed your call. How can we help?",
    },
]


def main():
    create_tables()

    with Session(engine) as session:
        for details in FAKE_CLIENTS:
            if get_client_by_number(details["twilio_number"]) is None:
                session.add(Client(**details))
                print("Added", details["business_name"])
            else:
                print("Already there:", details["business_name"])
        session.commit()

    for details in FAKE_CLIENTS:
        client = get_client_by_number(details["twilio_number"])
        print(f"{client.id}: {client.business_name}, owner {client.owner_mobile}")


if __name__ == "__main__":
    main()
