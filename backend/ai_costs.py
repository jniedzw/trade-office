"""Show what the AI has cost in the last 30 days, per client and per job type.

Run it where the database lives (on Render, in the Shell):
    python ai_costs.py
"""

from collections import defaultdict
from datetime import timedelta

from database import ai_calls_since, now_utc

# Dollars per million tokens (input, output), from Anthropic's price list.
PRICES = {
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-5-5": (4.00, 20.00),
}
# Unknown models are priced like the dearest one, so costs are never understated.
DEAREST = max(PRICES.values())


def cost_in_dollars(call):
    """Return what one AI call cost, in dollars."""
    input_price, output_price = PRICES.get(call.model, DEAREST)
    return (call.input_tokens * input_price + call.output_tokens * output_price) / 1_000_000


def summary(calls):
    """Return a line per client and job type: calls, cost and average time."""
    groups = defaultdict(list)
    for call in calls:
        groups[(call.client_id, call.purpose)].append(call)
    lines = []
    for (client_id, purpose), group in sorted(groups.items(), key=lambda item: str(item[0])):
        cost = sum(cost_in_dollars(call) for call in group)
        seconds = sum(call.seconds for call in group) / len(group)
        lines.append(
            f"Client {client_id}, {purpose}: {len(group)} calls, ${cost:.4f}, {seconds:.1f}s on average"
        )
    total = sum(cost_in_dollars(call) for call in calls)
    lines.append(f"Total: {len(calls)} calls, ${total:.4f}")
    return lines


def main():
    calls = ai_calls_since(now_utc() - timedelta(days=30))
    for line in summary(calls):
        print(line)


if __name__ == "__main__":
    main()
