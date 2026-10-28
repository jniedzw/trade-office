"""Add a real client to the database by answering a few questions.

Run it where the database lives (on Render, in the Shell):
    python add_client.py
Real phone numbers are typed in here, so they never go in the code.
"""

from sqlmodel import Session

from database import Client, create_tables, engine, get_client_by_number


def is_uk_mobile(number):
    """Return True if number looks like +447 followed by 9 digits."""
    return number.startswith("+447") and len(number) == 13 and number[1:].isdigit()


def ask_for_number(prompt):
    """Keep asking until we get a UK mobile in +447... format."""
    while True:
        number = input(prompt).strip().replace(" ", "")
        if is_uk_mobile(number):
            return number
        print("Please use the +447... format, e.g. +447700900123")


def main():
    create_tables()

    business_name = input("Business name: ").strip()
    owner_mobile = ask_for_number("Owner's mobile: ")
    twilio_number = ask_for_number("Their Twilio number: ")

    if get_client_by_number(twilio_number) is not None:
        print("That Twilio number already belongs to a client. Nothing added.")
        return

    default_message = (
        f"Hi, it's {business_name}. Sorry we missed your call. "
        "What can we help with? We'll call you back today."
    )
    print(f"Text-back message (press Enter to use this):\n  {default_message}")
    text_back_message = input("> ").strip() or default_message

    with Session(engine) as session:
        session.add(
            Client(
                business_name=business_name,
                owner_mobile=owner_mobile,
                twilio_number=twilio_number,
                text_back_message=text_back_message,
            )
        )
        session.commit()
    print(f"Added {business_name}.")


if __name__ == "__main__":
    main()
