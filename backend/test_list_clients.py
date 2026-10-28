from database import Client
from list_clients import describe


def test_describe_shows_status_and_numbers():
    client = Client(
        id=1,
        business_name="Test Plumbing",
        owner_mobile="+447700900101",
        twilio_number="+447700900001",
        text_back_message="Hi",
        active=False,
    )
    assert describe(client) == (
        "1: Test Plumbing (PAUSED) | Twilio +447700900001 | owner +447700900101"
    )
