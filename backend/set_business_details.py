"""Add or change a client's details for quotes and invoices.

Run it where the database lives (on Render, in the Shell):
    python set_business_details.py
Bank details are typed in here, so they never go in the code or through WhatsApp.
"""

from database import create_tables, get_client, save_business_details


def is_ref_letters(text):
    """Return True if text is 2 to 4 capital letters, like DPL."""
    return 2 <= len(text) <= 4 and text.isalpha() and text.isascii() and text.isupper()


def is_colour(text):
    """Return True if text is a web colour like #b8975a."""
    digits = text.removeprefix("#")
    return text.startswith("#") and len(digits) == 6 and all(c in "0123456789abcdefABCDEF" for c in digits)


def ask_lines(prompt):
    """Ask for several lines, ending with an empty one, and join them."""
    print(f"{prompt} (one line at a time, then press Enter on an empty line):")
    lines = []
    while line := input("  ").strip():
        lines.append(line)
    return "\n".join(lines)


def main():
    create_tables()

    client = get_client(int(input("Client ID (from list_clients.py): ")))
    if client is None:
        print("No client has that ID. Nothing saved.")
        return
    print(f"Setting details for {client.business_name}.")

    legal_name = input("Legal name, like Dave Smith t/a Dave's Plumbing: ").strip()
    address = ask_lines("Business address")
    ref_letters = input("Reference letters, like DPL: ").strip().upper()
    while not is_ref_letters(ref_letters):
        ref_letters = input("Please use 2 to 4 letters, like DPL: ").strip().upper()
    payment_days = int(input("Days to pay an invoice (Enter for 14): ").strip() or 14)
    bank_details = ask_lines("Bank details for invoices")
    contact = input("Phone or email for customers: ").strip()
    brand_colour = input("Accent colour, like #b8975a (Enter for gold): ").strip() or "#b8975a"
    while not is_colour(brand_colour):
        brand_colour = input("Please use # and 6 letters or digits (Enter for gold): ").strip() or "#b8975a"

    save_business_details(
        client.id,
        legal_name=legal_name,
        address=address,
        ref_letters=ref_letters,
        payment_days=payment_days,
        bank_details=bank_details,
        brand_colour=brand_colour,
        contact=contact,
    )
    print(f"Saved details for {client.business_name}.")


if __name__ == "__main__":
    main()
