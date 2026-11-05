from datetime import date, datetime, time, timezone

from database import Job
from diary import UK, check_booking, start_time, week_ahead, when


def test_when_shows_day_time_and_end_day():
    assert when(start_time(date(2026, 11, 9), time(9, 0))) == "Mon 9 Nov, 9am"
    assert when(start_time(date(2026, 11, 9), time(14, 30))) == "Mon 9 Nov, 2:30pm"
    # No time set, and a job running to Friday.
    assert when(start_time(date(2026, 11, 9), None), date(2026, 11, 13)) == "Mon 9 Nov to Fri 13 Nov"


def test_when_uses_uk_summer_time():
    # 8am UTC in July is 9am in the UK, because of British Summer Time.
    assert when(datetime(2026, 7, 6, 8, 0, tzinfo=timezone.utc)) == "Mon 6 Jul, 9am"


def test_week_ahead_groups_by_day_and_repeats_long_jobs():
    jobs = [
        Job(client_id=1, customer_name="Mrs Jones", work="Bathroom", address="12 Test Road\nLiverpool",
            starts_at=start_time(date(2026, 11, 9), time(8, 0)), ends_on=date(2026, 11, 10)),
        Job(client_id=1, customer_name="Mr Smith", work="Boiler service",
            starts_at=start_time(date(2026, 11, 9), time(14, 0))),
    ]

    assert week_ahead(jobs, date(2026, 11, 9)) == (
        "Mon 9 Nov\n"
        "  8am Mrs Jones: Bathroom (12 Test Road)\n"
        "  2pm Mr Smith: Boiler service\n"
        "Tue 10 Nov\n"
        "  Mrs Jones: Bathroom (12 Test Road)"
    )


def test_week_ahead_with_nothing_booked():
    assert week_ahead([], date(2026, 11, 9)) is None


def test_check_booking_rejects_dates_that_dont_make_sense():
    today = date(2026, 11, 5)
    assert check_booking(date(2026, 11, 9), time(9, 0), None, today) == (
        datetime(2026, 11, 9, 9, 0, tzinfo=UK), None
    )
    assert check_booking(date(2026, 11, 1), None, None, today) is None  # in the past
    assert check_booking(date(2026, 11, 9), None, date(2026, 11, 8), today) is None  # ends before it starts
    assert check_booking(date(2028, 1, 1), None, None, today) is None  # over a year away
