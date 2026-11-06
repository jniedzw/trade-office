"""The Sunday 6pm week-ahead update: the week's jobs and the money owed."""

from datetime import timedelta

import settings
from alerts import alert_by_sms
from database import (
    active_clients,
    booked_jobs,
    now_utc,
    save_pending_alert,
    unpaid_invoices,
    whatsapp_alerts_on,
)
from diary import days_of, uk_now
from quotes import money
from sms import send_weekly_update


def week_summary(client_id, now):
    """Return ("4 jobs", "£1,335.50"): next week's booked jobs and what's owed.

    "Next week" is the 7 days from tomorrow, since this goes out on a Sunday evening.
    """
    tomorrow = uk_now(now).date() + timedelta(days=1)
    week = {tomorrow + timedelta(days=n) for n in range(7)}
    count = sum(1 for job in booked_jobs(client_id, now) if week & set(days_of(job)))
    jobs = "1 job" if count == 1 else f"{count} jobs"
    owed = sum(invoice["total_pence"] for invoice in unpaid_invoices(client_id))
    return jobs, money(owed)


def send_weekly_updates():
    """Send every active client on WhatsApp their week ahead, with SMS as the backup."""
    if not (settings.WHATSAPP_NUMBER and settings.WHATSAPP_WEEKLY_TEMPLATE):
        return
    for client in active_clients():
        if not whatsapp_alerts_on(client.id):
            continue
        jobs, owed = week_summary(client.id, now_utc())
        backup = f"Your week ahead: {jobs} booked, and {owed} owed to you. Reply WEEK on WhatsApp for details."
        try:
            message_sid = send_weekly_update(client.owner_mobile, jobs, owed)
            # Like missed-call alerts: if WhatsApp hasn't delivered it in a few minutes, SMS takes over.
            save_pending_alert(message_sid, client.id, backup)
        except Exception as error:
            print(f"Weekly update failed: {type(error).__name__}", flush=True)
            alert_by_sms(client, backup)
