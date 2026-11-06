"""Delete a client who's leaving, and all their records.

Run it where the database lives (on Render, in the Shell):
    python delete_client.py
Before running it: have the client send EXPORT on WhatsApp, so they keep a copy.
Our privacy policy promises this is done within 30 days of them leaving.
"""

from database import create_tables, delete_client, get_client


def main():
    create_tables()
    client = get_client(int(input("Client ID to delete (from list_clients.py): ")))
    if client is None:
        print("No client has that ID. Nothing deleted.")
        return

    print(f"This deletes {client.business_name} and all their jobs, quotes, invoices,")
    print("calls, prices and logins. It can't be undone.")
    print("Have they had their EXPORT spreadsheet?")
    # Typing the name, not just "y", makes it hard to delete the wrong client by accident.
    if input(f"Type {client.business_name} to confirm: ").strip() != client.business_name:
        print("That didn't match. Nothing deleted.")
        return

    counts = delete_client(client.id)
    for table, count in counts.items():
        if count:
            print(f"  {table}: {count} deleted")
    print(f"Deleted {client.business_name}.")
    print("Still to do by hand: release their Twilio number, and delete their calls and")
    print("messages in Twilio's logs.")


if __name__ == "__main__":
    main()
