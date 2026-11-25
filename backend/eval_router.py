"""Check the AI turns plain messages into the right command.

This calls the real AI, so each run costs a few pence. Usage:
    python eval_router.py
"""

import ai

# Made-up messages, the command they mean, and a word that must be in the details (or None).
CASES = [
    ("mrs jones has paid", "PAID", "jones"),
    ("got paid for INV-0244", "PAID", "0244"),
    ("move dave to thursday 2pm", "MOVE", "thursday"),
    ("can you book mr smith in for tuesday 9am, boiler service", "BOOK", "smith"),
    ("finished at the patels", "DONE", "patel"),
    ("job 4 is done", "DONE", "4"),
    ("whats on this week", "WEEK", None),
    ("who still owes me money", "OWED", None),
    ("chase everyone who hasnt paid", "REMIND", None),
    ("text mrs jones her quote", "SEND", "jones"),
    ("cancel the jones job, she's gone elsewhere", "CANCEL", "jones"),
    ("new job, Bev at 4 Park Lane, outside tap leaking", "LOG", "bev"),
    ("quote mr okafor 2 rads 360", "QUOTE", "360"),
    ("oops undo that", "UNDO", None),
    ("my boiler service price is now 90", "PRICE", "90"),
    ("thanks mate that's great", None, None),
    ("what's the weather like", None, None),
]


def check(message, command, word):
    """Return what the AI got wrong for one message, or an empty string."""
    routed = ai.route_message(message)
    if command is None:
        return "" if routed is None else f"should do nothing, but read it as {routed}"
    if routed is None:
        return f"did nothing, but it's {command}"
    got, details = routed
    if got != command:
        return f"read it as {got}, but it's {command}"
    if word and word not in details.lower():
        return f"details {details!r} are missing {word!r}"
    return ""


def main():
    print(f"Model: {ai.AI_MODEL}")
    passed = 0
    for number, (message, command, word) in enumerate(CASES, start=1):
        problem = check(message, command, word)
        if problem:
            print(f"{number:2}. FAIL {message!r}: {problem}")
        else:
            passed += 1
    print(f"Passed {passed} of {len(CASES)}")


if __name__ == "__main__":
    main()
