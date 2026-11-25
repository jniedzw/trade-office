"""The menu and the help text, kept in one place.

"morning" shows three buttons (Jobs, Money, More), and each opens its own short menu.
When a new command works, add it to a group in MENU_GROUPS (or NOT_IN_MENU). A test
fails until every command is in one of them. After changing a group, remake its
template: python create_button_templates.py WHATSAPP_JOBS_MENU (or MONEY, MORE)
"""

# Each group: its button title, and its choices as (title, command, description).
# A group with more than 3 choices becomes a list, since WhatsApp allows 3 buttons.
MENU_GROUPS = {
    "JOBS": ("Jobs", [
        ("Log a job", "LOG", "Save a new job or enquiry"),
        ("New quote", "QUOTE", "Tell Jack about a job"),
        ("Book a job", "BOOK", "Put a job in your diary"),
        ("What's on this week", "WEEK", "Your jobs for the next 7 days"),
        ("Job done", "DONE", "Mark a job as finished"),
    ]),
    "MONEY": ("Money", [
        ("Who owes me", "OWED", "Unpaid invoices, oldest first"),
        ("Mark as paid", "PAID", "Record a payment"),
    ]),
    "MORE": ("More", [
        ("My prices", "PRICES", "See your saved prices"),
        ("My page", "PAGE", "Your jobs and money on the web"),
        ("Help", "HELP", "Everything you can send"),
    ]),
}

# Every choice in every group, in one list.
MENU_CHOICES = [choice for title, choices in MENU_GROUPS.values() for choice in choices]

# Commands reached another way: by buttons, or typed with a quote number.
NOT_IN_MENU = [
    "FINAL", "CHANGE", "INVOICE", "PRICE", "MARKUP", "ALERTS", "EXPORT",
    "MOVE", "CANCEL", "UNDO", "SEND", "REMIND", "MENU",
]

def group_text(group):
    """Return a group's menu as plain text, for when its template isn't set up."""
    title, choices = MENU_GROUPS[group]
    return f"{title}:\n" + "\n".join(f"{command}: {description}" for _, command, description in choices)

HELP_TEXT = (
    "Things you can send me:\n"
    "LOG and the job, to save a new job\n"
    "QUOTE and the job, in your own words\n"
    "CHANGE Q6 and what to change\n"
    "FINAL Q6, or FINAL and the customer's name\n"
    "SEND Q6 to text a final quote to the customer\n"
    "INVOICE Q6, or INVOICE and the customer's name\n"
    "BOOK and the job and when, to book it in\n"
    "WEEK to see what's on\n"
    "DONE J4, or DONE and the customer's name, when a job's finished\n"
    "MOVE J4 Thursday 2pm, or MOVE Mrs Jones to Thursday 2pm\n"
    "CANCEL J4, or CANCEL and the customer's name\n"
    "UNDO to undo the last thing I did\n"
    "OWED to see who owes you\n"
    "REMIND to text overdue customers a polite reminder\n"
    "PAID INV-0244, or PAID and the customer's name\n"
    "PRICE boiler service 85 to save a price\n"
    "PRICES to see your prices\n"
    "MARKUP 15 to add 15% to materials\n"
    "EXPORT for all your data as a spreadsheet\n"
    "PAGE for a link to your web page\n"
    "Or just say hi for the menu."
)


def command_for_title(text):
    """Return the command for a menu button's title, like "New quote" or "Jobs", or None."""
    for title, command, _ in MENU_CHOICES:
        if text.strip().upper() == title.upper():
            return command
    for group, (title, _) in MENU_GROUPS.items():
        if text.strip().upper() == title.upper():
            return f"MENU {group}"
    return None
