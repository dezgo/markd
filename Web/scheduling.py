"""Translating between how a todo is stored and how its owner sees it.

A scheduled todo is stored one of two ways, and never both:

    due_at  a UTC instant, for a todo with a time of day
    due_on  a plain local calendar date, for an all-day todo

The pair they replaced — due_date + due_time — used one column for both jobs:
due_date alone meant a *local* date, but the moment due_time was set it meant a
*UTC* date. Weekday recurrences were computed on that column, so for anyone
east of UTC a 07:00 Monday task was stored on the previous UTC day and surfaced
to the user on Tuesday.

"Repeats on Mon, Wed, Fri" is a claim about the owner's calendar. Everything
here exists to move between that calendar and the stored instant explicitly,
rather than letting the two frames blur into one column. Every function is
pure and takes the timezone as an argument, so the rules are testable without
a database or a request.
"""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc


def resolve_tz(name):
    """A ZoneInfo for `name`, falling back to UTC.

    Unknown zones fall back rather than raise: a bad tz string in settings is
    a reason to schedule slightly wrong, not to fail the request.
    """
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def parse_hhmm(due_time: str):
    """"HH:MM" -> time, or None if it is missing or malformed."""
    try:
        h, m = map(int, due_time.split(":"))
        return time(h, m)
    except (AttributeError, TypeError, ValueError):
        return None


def today_in(tz):
    return datetime.now(tz).date()


def now_utc():
    """Naive UTC, matching how datetimes are stored."""
    return datetime.now(UTC).replace(tzinfo=None)


def local_date_of(due_at, due_on, tz):
    """The calendar date the owner sees this todo on, or None if unscheduled."""
    if due_on is not None:
        return due_on
    if due_at is not None:
        return due_at.replace(tzinfo=UTC).astimezone(tz).date()
    return None


def local_time_of(due_at, tz):
    """The wall-clock time the owner sees, or None for an all-day todo."""
    if due_at is None:
        return None
    return due_at.replace(tzinfo=UTC).astimezone(tz).time()


def due_fields(local_day, local_time, tz):
    """(due_at, due_on) for a todo the owner should see on that local date.

    `local_time` None means an all-day todo, stored as the date itself.
    Otherwise the wall time is anchored against the zone on that specific date
    rather than carried as a fixed offset, so a recurrence crossing a DST
    boundary keeps its local time instead of sliding by an hour.
    """
    if local_day is None:
        return None, None
    if local_time is None:
        return None, local_day
    aware = datetime.combine(local_day, local_time, tzinfo=tz)
    return aware.astimezone(UTC).replace(tzinfo=None), None


def next_occurrence_of(local_time, tz):
    """The next local date on which `local_time` is still ahead.

    A todo given a time but no date used to sit undated forever: it never
    landed in a day group and the notifier, which requires an instant, skipped
    it. Resolving it to today — or tomorrow, if that time has already gone —
    is the reading that matches what the field is for.
    """
    now_local = datetime.now(tz)
    day = now_local.date()
    if local_time <= now_local.time():
        day += timedelta(days=1)
    return day


def is_overdue(due_at, due_on, tz, reference_utc=None):
    """True once this todo's due moment has passed.

    Timed todos compare as instants. All-day todos are overdue only once the
    day itself has passed in the owner's zone — an all-day task is not late at
    00:01.
    """
    if due_at is not None:
        return due_at < (reference_utc or now_utc())
    if due_on is not None:
        return due_on < today_in(tz)
    return False
