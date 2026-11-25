"""Review requests: one text after a job's done, only to customers who said yes."""

from alerts import try_send
from database import (
    get_business_details,
    get_client,
    is_opted_out,
    mark_review_asked,
    now_utc,
    review_requests_due,
)
from diary import uk_now

# Only send during the day, UK time: from 10am, and not after 7pm.
FIRST_HOUR, LAST_HOUR = 10, 19


def review_text(business_name, customer_name, link):
    """Return the review request. Plain and polite, with no offers, and a way to opt out."""
    return (
        f"Hi {customer_name}, thanks for choosing {business_name}. If you have a minute, "
        f"a quick review would really help: {link}\nReply STOP to opt out."
    )


def send_review_requests():
    """Text each customer who's due a review request. Runs every hour."""
    now = now_utc()
    if not FIRST_HOUR <= uk_now(now).hour < LAST_HOUR:
        return
    for job, quote in review_requests_due(now):
        client = get_client(job.client_id)
        details = get_business_details(job.client_id)
        phone = quote.customer_phone or job.customer_phone
        # Mark it asked whatever happens, so a missing link or a STOP never means trying every hour.
        mark_review_asked(job.id)
        if not (client and client.active and details and details.review_link and phone):
            continue
        if is_opted_out(client.id, phone):
            continue
        text = review_text(client.business_name, job.customer_name, details.review_link)
        if not try_send(phone, text, client.twilio_number):
            print("Review request failed to send", flush=True)
