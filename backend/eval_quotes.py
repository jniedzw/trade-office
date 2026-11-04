"""Check the AI quote reader against made-up messages with known right answers.

This calls the real AI, so each run costs a few pence. Usage:
    python eval_quotes.py                     (the model in ai.py)
    python eval_quotes.py claude-haiku-5-5    (try another model)
"""

import sys

import ai

# Made-up messages written the way a tradesperson might send them. For each:
# the customer and total in pence we expect (None means Jack must refuse), and
# how many lines of work the message describes.
CASES = [
    ("bathroom for mrs jones, rip out old suite, new basin and loo, 1,250 all in", "Mrs Jones", 125000, 3),
    ("boiler service for dave", None, None, 0),
    ("Mr Patel - new combi boiler fitted, remove old one, flush system. £2,400", "Mr Patel", 240000, 3),
    ("leaking tap kitchen sarah 85", "Sarah", 8500, 1),
    ("radiator swap x2 for Mr Okafor 360 quid", "Mr Okafor", 36000, 1),
    ("powerflush and new TRVs on 6 rads, Karen Webb, 650", "Karen Webb", 65000, 2),
    ("quote for john, 3 bed house, full rewire? not sure on price yet", None, None, 1),
    ("Gas safety cert for landlord Mr Hughes 65.00", "Mr Hughes", 6500, 1),
    ("unblock toilet and replace cistern parts at 14 Elm Rd (Tom) - 120", "Tom", 12000, 2),
    ("Mrs Ali shower: remove electric shower, fit thermostatic mixer, tile around. £780.50", "Mrs Ali", 78050, 3),
    # "Plus parts" means 95 isn't the total, so Jack must ask.
    ("outside tap for Bev, 95 plus parts", None, None, 1),
    ("new rads in 2 bedrooms for Gary, about 500-600", None, None, 1),
    ("Mr and Mrs Clarke, move boiler to loft, new flue, 2 days work, 1850", "Mr and Mrs Clarke", 185000, 2),
    ("emma - fix noisy pipes 150", "Emma", 15000, 1),
    ("cylinder replacement 1,100 for Mr Doyle. drain down, remove old cylinder, fit new unvented, G3 cert", "Mr Doyle", 110000, 4),
    ("can you do a quote for the woman on Park Lane, kitchen sink, 2 hours", None, None, 1),
    ("Fiona Grant, underfloor heating in extension 3200, manifold and screed by others", "Fiona Grant", 320000, 1),
    ("replace 3 rad valves for steve @ £45 each, 135 total", "Steve", 13500, 1),
    ("annual service x 3 boilers for Acme Lettings 255", "Acme Lettings", 25500, 1),
    ("Mrs Brown leak under bath, find and fix, 1 hour labour", None, None, 1),
]


def check(message, customer, total_pence, line_count):
    """Return a list of what the AI got wrong for one message (empty if right)."""
    quote = ai.read_quote(message)
    if customer is None:
        return [] if quote is None else [f"should refuse, but priced it {quote['total_pence']}"]
    if quote is None:
        return ["refused, but should have made a quote"]

    problems = []
    if quote["customer_name"].lower() != customer.lower():
        problems.append(f"customer {quote['customer_name']!r}, wanted {customer!r}")
    if quote["total_pence"] != total_pence:
        problems.append(f"total {quote['total_pence']}, wanted {total_pence}")
    # Splitting work into lines is a judgment call, so allow one either way.
    if abs(len(quote["lines"]) - line_count) > 1:
        problems.append(f"{len(quote['lines'])} lines, wanted about {line_count}")
    return problems


def main():
    if len(sys.argv) > 1:
        ai.AI_MODEL = sys.argv[1]
    print(f"Model: {ai.AI_MODEL}")

    passed = 0
    for number, (message, customer, total_pence, line_count) in enumerate(CASES, start=1):
        problems = check(message, customer, total_pence, line_count)
        if problems:
            print(f"{number:2}. FAIL {message[:45]!r}: {'; '.join(problems)}")
        else:
            passed += 1
    print(f"Passed {passed} of {len(CASES)}")


if __name__ == "__main__":
    main()
