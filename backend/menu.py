"""The Options menu and the help text, kept in one place.

When a new command works, add it to MENU_CHOICES (or NOT_IN_MENU). A test fails
until every command is in one of them. After changing MENU_CHOICES, remake the
menu template: python create_button_templates.py WHATSAPP_MENU
"""

# Each choice: its title in the list, the command it sends, and a short description.
# WhatsApp allows 10 choices, with titles up to 24 characters.
MENU_CHOICES = [
    ("Log a job", "LOG", "Save a new job or enquiry"),
    ("New quote", "QUOTE", "Tell Jack about a job"),
    ("Book a job", "BOOK", "Put a job in your diary"),
    ("What's on this week", "WEEK", "Your jobs for the next 7 days"),
    ("Who owes me", "OWED", "Unpaid invoices, oldest first"),
    ("Mark as paid", "PAID", "Record a payment"),
    ("My prices", "PRICES", "See your saved prices"),
    ("My page", "PAGE", "Your jobs and money on the web"),
    ("Help", "HELP", "Everything you can send"),
]

# Commands reached another way: by buttons, or typed with a quote number.
NOT_IN_MENU = ["FINAL", "CHANGE", "INVOICE", "PRICE", "MARKUP", "ALERTS", "EXPORT"]

# What Jack says to anything he doesn't recognise, like "morning".
MENU_TEXT = "Hi, it's Jack. What do you need? Tap Options."

HELP_TEXT = (
    "Things you can send me:\n"
    "LOG and the job, to save a new job\n"
    "QUOTE and the job, in your own words\n"
    "CHANGE Q6 and what to change\n"
    "FINAL Q6, or FINAL and the customer's name\n"
    "INVOICE Q6, or INVOICE and the customer's name\n"
    "BOOK and the job and when, to book it in\n"
    "WEEK to see what's on\n"
    "OWED to see who owes you\n"
    "PAID INV-0244, or PAID and the customer's name\n"
    "PRICE boiler service 85 to save a price\n"
    "PRICES to see your prices\n"
    "MARKUP 15 to add 15% to materials\n"
    "EXPORT for all your data as a spreadsheet\n"
    "PAGE for a link to your web page\n"
    "Or just say hi for the menu."
)


def command_for_title(text):
    """Return the command for a menu choice's title, like "New quote", or None."""
    for title, command, _ in MENU_CHOICES:
        if text.strip().upper() == title.upper():
            return command
    return None
