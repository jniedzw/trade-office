"""Booked jobs in UK time: reading dates, and laying out the week ahead."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UK = ZoneInfo("Europe/London")
# A booking at exactly midnight means "that day, no time set".
NO_TIME = time(0, 0)


def to_uk(moment):
    """Return a UTC time from the database in UK time (GMT or BST)."""
    # SQLite gives times back without a timezone; they were saved as UTC.
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(UK)


def uk_now(now_utc):
    """Return the time now in the UK."""
    return now_utc.astimezone(UK)


def start_time(day, at):
    """Return the UK day and time as one time-zone-aware moment, or midnight for no time."""
    return datetime.combine(day, at or NO_TIME, tzinfo=UK)


def clock(moment):
    """Return a time like "9am" or "2:30pm"."""
    minutes = f":{moment:%M}" if moment.minute else ""
    return f"{moment.hour % 12 or 12}{minutes}{'am' if moment.hour < 12 else 'pm'}"


def when(starts_at, ends_on=None):
    """Return a booking for people to read, like "Mon 9 Nov, 9am" or "Mon 9 Nov to Fri 13 Nov"."""
    start = to_uk(starts_at)
    text = f"{start:%a} {start.day} {start:%b}"
    if ends_on and ends_on != start.date():
        text += f" to {ends_on:%a} {ends_on.day} {ends_on:%b}"
    if start.time() != NO_TIME:
        text += f", {clock(start)}"
    return text


def days_of(job):
    """Return every UK date a booked job covers, from its start to its end."""
    first = to_uk(job.starts_at).date()
    last = job.ends_on or first
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def week_ahead(jobs, today):
    """Return the next 7 days' booked jobs as text, grouped by day, or None if there are none."""
    days = [today + timedelta(days=n) for n in range(7)]
    lines = []
    for day in days:
        todays = sorted(
            (job for job in jobs if job.starts_at and day in days_of(job)),
            key=lambda job: to_uk(job.starts_at).time(),
        )
        if not todays:
            continue
        lines.append(f"{day:%a} {day.day} {day:%b}")
        for job in todays:
            start = to_uk(job.starts_at)
            # The start time only shows on the first day, and only if one was set.
            at = f"{clock(start)} " if start.date() == day and start.time() != NO_TIME else ""
            where = f" ({job.address.splitlines()[0]})" if job.address else ""
            lines.append(f"  {at}{job.customer_name}: {job.work}{where}")
    return "\n".join(lines) if lines else None


def check_booking(day, at, end_day, today):
    """Return the booking's (start, end date) if it makes sense, or None.

    day and end_day are dates, at is a time or None, and today is today's UK date.
    """
    if day < today or day > today + timedelta(days=365):
        return None
    if end_day is not None and not (day <= end_day <= day + timedelta(days=60)):
        return None
    return start_time(day, at), end_day


def parse_iso_date(text):
    """Return a date from "2026-11-09", or None."""
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def parse_iso_time(text):
    """Return a time from "09:30", or None."""
    try:
        return time.fromisoformat(text) if text else None
    except ValueError:
        return None
