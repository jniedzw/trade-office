"""Check the AI's reading of booking times against messages with known right answers.

This calls the real AI, so each run costs about 1p. Usage:
    python eval_bookings.py
"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import ai

# Every case pretends it's Friday 6 November 2026 at 10am in the UK.
NOW = datetime(2026, 11, 6, 10, 0, tzinfo=ZoneInfo("Europe/London"))

# Message, then the start day, start time (None for no time) and last day we expect.
# A start day of None means Jack must ask again rather than guess.
CASES = [
    ("Friday 3pm", date(2026, 11, 6), time(15, 0), None),
    ("friday 9am", date(2026, 11, 13), time(9, 0), None),  # 9am today has passed
    ("next Friday at 3", date(2026, 11, 13), time(15, 0), None),
    ("today 4pm", date(2026, 11, 6), time(16, 0), None),
    ("tomorrow 2pm", date(2026, 11, 7), time(14, 0), None),
    ("monday", date(2026, 11, 9), None, None),
    ("mon 8.30", date(2026, 11, 9), time(8, 30), None),
    ("weds afternoon 2:30", date(2026, 11, 11), time(14, 30), None),
    ("mon to fri", date(2026, 11, 9), None, date(2026, 11, 13)),
    ("3 days from tuesday, start 8am", date(2026, 11, 10), time(8, 0), date(2026, 11, 12)),
    ("12th nov 10am", date(2026, 11, 12), time(10, 0), None),
    ("1st december", date(2026, 12, 1), None, None),
    ("sometime soon", None, None, None),
    ("when he's back from holiday", None, None, None),
]


def check(message, day, at, last_day):
    """Return what the AI got wrong for one message, or an empty string."""
    booking = ai.read_booking(message, NOW)
    if day is None:
        return "" if booking is None else f"should ask again, but booked {booking}"
    if booking is None:
        return "asked again, but should have booked it"
    starts_at, ends_on = booking
    got = (starts_at.date(), starts_at.time() if starts_at.time() != time(0, 0) else None, ends_on)
    wanted = (day, at, last_day)
    return "" if got == wanted else f"got {got}, wanted {wanted}"


def main():
    print(f"Model: {ai.AI_MODEL}")
    passed = 0
    for number, (message, day, at, last_day) in enumerate(CASES, start=1):
        problem = check(message, day, at, last_day)
        if problem:
            print(f"{number:2}. FAIL {message!r}: {problem}")
        else:
            passed += 1
    print(f"Passed {passed} of {len(CASES)}")


if __name__ == "__main__":
    main()
