"""Add family and friends numbers that never get a client's text-back.

Run it where the database lives (on Render, in the Shell):
    python add_skip_numbers.py
Real phone numbers are typed in here, so they never go in the code.
"""

from add_client import ask_for_number, is_uk_mobile
from database import add_skip_number, create_tables, get_client_by_number


def main():
    create_tables()

    twilio_number = ask_for_number("Client's Twilio number: ")
    client = get_client_by_number(twilio_number)
    if client is None:
        print("No client has that Twilio number. Check it with: python list_clients.py")
        return

    print(
        f"Adding numbers for {client.business_name}. Press Enter on an empty line to finish."
    )
    while True:
        number = input("Number to skip: ").strip().replace(" ", "")
        if number == "":
            break
        if not is_uk_mobile(number):
            print("Please use the +447... format, e.g. +447700900123")
        elif add_skip_number(client.id, number):
            print("  Added.")
        else:
            print("  Already on the list.")
    print("Done.")


if __name__ == "__main__":
    main()
