"""Change a client's details, or pause and unpause them.

Run it where the database lives (on Render, in the Shell):
    python update_client.py
For each detail, press Enter to keep the current value.
"""

from add_client import ask_for_number, is_uk_mobile
from database import create_tables, get_client_by_number, update_client


def ask_with_default(prompt, current):
    """Ask for a new value, keeping the current one if Enter is pressed."""
    answer = input(f"{prompt} [{current}]: ").strip()
    return answer or current


def ask_yes_no(prompt, current):
    """Ask a yes/no question, keeping the current answer if Enter is pressed."""
    default = "y" if current else "n"
    while True:
        answer = input(f"{prompt} (y/n) [{default}]: ").strip().lower() or default
        if answer in ("y", "n"):
            return answer == "y"
        print("Please type y or n.")


def main():
    create_tables()

    twilio_number = ask_for_number("Client's Twilio number: ")
    client = get_client_by_number(twilio_number)
    if client is None:
        print("No client has that Twilio number. Check it with: python list_clients.py")
        return

    business_name = ask_with_default("Business name", client.business_name)

    owner_mobile = ask_with_default("Owner's mobile", client.owner_mobile)
    while not is_uk_mobile(owner_mobile):
        print("Please use the +447... format, e.g. +447700900123")
        owner_mobile = ask_with_default("Owner's mobile", client.owner_mobile)

    text_back_message = ask_with_default("Text-back message", client.text_back_message)
    active = ask_yes_no("Active (answering calls)", client.active)

    update_client(client.id, business_name, owner_mobile, text_back_message, active)
    print(f"Saved. {business_name} is {'active' if active else 'PAUSED'}.")


if __name__ == "__main__":
    main()
