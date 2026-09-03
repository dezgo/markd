"""Translating between how a todo is stored and how its owner sees it.

A todo with a time is stored as a UTC instant split across two columns:
`due_date` is a UTC calendar date and `due_time` a UTC wall clock. That is the
right thing for the notifier, which only ever asks "is this instant in the
past yet".

It is the wrong frame for anything involving weekdays. "Repeats on Mon, Wed,
Fri" is a statement about the owner's calendar, and for anyone east of UTC a
morning alarm sits on the previous UTC day — so 07:00 Brisbane on a Wednesday
is stored as Tuesday 21:00 UTC. Doing weekday arithmetic on the stored date
therefore lands the user on Thursday, and every completion drags it another
day out.

Every function here is pure and takes the timezone explicitly, so the rules
can be tested without a database or a request.
"""

from datetime import datetime, time, timezone
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


def local_date(due_date, due_time, tz):
    """The calendar date the owner sees this todo on.

    A todo with no time carries a plain local date already — there is no
    instant to convert — so it is returned untouched.
    """
    t = parse_hhmm(due_time)
    if due_date is None or t is None:
        return due_date
    return datetime.combine(due_date, t, tzinfo=UTC).astimezone(tz).date()


def local_time_of_day(due_date, due_time, tz):
    """The wall-clock time the owner sees, or None for a date-only todo."""
    t = parse_hhmm(due_time)
    if due_date is None or t is None:
        return None
    return datetime.combine(due_date, t, tzinfo=UTC).astimezone(tz).time()


def to_storage(target_local_date, local_time, tz):
    """(due_date, due_time) for a todo the owner should see on that local date.

    `local_time` None means a date-only todo, which is stored as-is. Otherwise
    the local wall time is re-anchored on the target date — rebuilt against the
    zone rather than carried as a fixed offset, so a recurrence crossing a DST
    boundary keeps its local time instead of sliding by an hour.
    """
    if local_time is None:
        return target_local_date, None
    utc_dt = datetime.combine(target_local_date, local_time, tzinfo=tz).astimezone(UTC)
    return utc_dt.date(), utc_dt.strftime("%H:%M")


def today_in(tz):
    return datetime.now(tz).date()
