from ai_costs import cost_in_dollars, summary
from database import AiCall


def call(client_id, purpose, model="claude-sonnet-5-5", seconds=2.0):
    return AiCall(
        client_id=client_id,
        purpose=purpose,
        model=model,
        input_tokens=1_000_000,
        output_tokens=100_000,
        seconds=seconds,
    )


def test_cost_in_dollars():
    # $2 for a million tokens in, plus $10 per million out for 100,000 tokens.
    assert cost_in_dollars(call(1, "QuoteDraft")) == 3.0


def test_an_unknown_model_is_priced_like_the_dearest():
    assert cost_in_dollars(call(1, "QuoteDraft", model="claude-new-model")) == 6.0


def test_summary_groups_by_client_and_purpose():
    calls = [call(1, "QuoteDraft", seconds=1.0), call(1, "QuoteDraft", seconds=3.0), call(2, "JobDraft")]

    assert summary(calls) == [
        "Client 1, QuoteDraft: 2 calls, $6.0000, 2.0s on average",
        "Client 2, JobDraft: 1 calls, $3.0000, 2.0s on average",
        "Total: 3 calls, $9.0000",
    ]
