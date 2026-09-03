"""Recurrence rules and scheduling maths.

Deliberately free of Flask, SQLAlchemy and env config: everything here is a
pure function over dates and plain dicts, so it can be tested without standing
up an app. The recurring-weekday bug that shipped for months was a missing
start date, not bad arithmetic — the arithmetic was never covered because
nothing in this codebase could be imported without a live environment.

Weekday numbering is the JavaScript convention throughout (Sun=0..Sat=6),
because that is what the browser hands us and what `recurrence_days` stores.
Python's own convention (Mon=0..Sun=6) appears only inside the helpers that
bridge the two.
"""

import calendar
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

RECURRENCE_UNITS = {"days", "weeks", "months", "years", "monthly-last", "monthly-2last"}

# The units that pair with an "every N" count. The rest carry their own rule.
INTERVAL_UNITS = {"days", "weeks", "months", "years"}

# Units that mean "the nth-last <weekday> of the month".
MONTHLY_WEEKDAY_UNITS = {"monthly-last", "monthly-2last"}


def js_weekday(d: date) -> int:
    """Python weekday (Mon=0..Sun=6) -> JS weekday (Sun=0..Sat=6)."""
    return (d.weekday() + 1) % 7


def py_weekday(js_dow: int) -> int:
    """JS weekday (Sun=0..Sat=6) -> Python weekday (Mon=0..Sun=6)."""
    return (js_dow - 1) % 7


def weekday_set(days_csv: str) -> set:
    """CSV of JS weekday numbers -> set of ints. Tolerates empty segments."""
    return {int(d) for d in days_csv.split(",") if d}


def _nth_last_weekday_of_month(year: int, month: int, weekday_py: int, n: int) -> date:
    """The nth-to-last occurrence of `weekday_py` in the month. n=1 means last."""
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() != weekday_py:
        d -= timedelta(days=1)
    return d - timedelta(days=7 * (n - 1))


def first_due_date(base: date, days_csv: str) -> date:
    """First date on or after `base` falling on one of the CSV weekdays.

    Seeds the start date for a weekday recurrence the user created without
    picking one. Without a due_date such a task never lands in a day group and
    the notifier skips it, so it silently never comes up.
    """
    days = weekday_set(days_csv)
    for offset in range(0, 7):
        candidate = base + timedelta(days=offset)
        if js_weekday(candidate) in days:
            return candidate
    return base


def next_due_date(base: date, interval: int, unit: str, days_csv: str = None) -> date:
    """The occurrence strictly after `base`."""
    if unit in MONTHLY_WEEKDAY_UNITS and days_csv:
        target = py_weekday(int(days_csv.split(",")[0]))
        n = 1 if unit == "monthly-last" else 2
        y, m = base.year, base.month
        candidate = _nth_last_weekday_of_month(y, m, target, n)
        if candidate <= base:
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
            candidate = _nth_last_weekday_of_month(y, m, target, n)
        return candidate

    if unit == "weeks" and days_csv:
        days = weekday_set(days_csv)
        for offset in range(1, 8):
            candidate = base + timedelta(days=offset)
            if js_weekday(candidate) in days:
                return candidate
        return base + timedelta(days=7)

    if unit == "days":
        return base + timedelta(days=interval)
    if unit == "weeks":
        return base + timedelta(weeks=interval)
    if unit == "months":
        return base + relativedelta(months=interval)
    if unit == "years":
        return base + relativedelta(years=interval)
    return base


def valid_time(t: str) -> bool:
    """True for a well-formed 24-hour "HH:MM"."""
    try:
        h, m = t.split(":")
        return 0 <= int(h) <= 23 and 0 <= int(m) <= 59
    except (AttributeError, TypeError, ValueError):
        return False


def parse_recurrence(data: dict):
    """Normalise a request payload's recurrence fields.

    Returns (interval, unit, days_csv, error). `error` is a string on rejection
    and None otherwise; on rejection the other three are None.

    A weekday recurrence is stored as unit "weeks" with an interval of 1 and a
    days CSV — the interval is synthetic and carries no meaning, since the days
    fully determine the schedule.
    """
    interval = data.get("recurrence_interval")
    unit = data.get("recurrence_unit") or None
    days = data.get("recurrence_days")

    if interval is None and unit is None and not days:
        return None, None, None, None

    if unit in MONTHLY_WEEKDAY_UNITS:
        if not days:
            return None, None, None, f"recurrence_days (single weekday) is required for {unit}"
        try:
            day_list = [int(d) for d in str(days).split(",") if d != ""]
        except ValueError:
            return None, None, None, "recurrence_days must be a weekday number 0-6"
        if len(day_list) != 1 or not 0 <= day_list[0] <= 6:
            return None, None, None, "recurrence_days must be a single weekday number 0-6"
        return None, unit, str(day_list[0]), None

    if unit == "weeks" and days is not None and not str(days).strip():
        # Distinguish "weekday recurrence with nothing picked" from "every N
        # weeks"; otherwise this falls through to a misleading interval error.
        return None, None, None, "recurrence_days must name at least one weekday (0-6)"

    if days and unit == "weeks":
        try:
            day_set = sorted({int(d) for d in str(days).split(",") if d != ""})
        except ValueError:
            return None, None, None, "recurrence_days must be comma-separated weekday numbers (0-6)"
        if not day_set or any(d < 0 or d > 6 for d in day_set):
            return None, None, None, "recurrence_days values must be 0-6 (Sun=0..Sat=6)"
        return 1, "weeks", ",".join(str(d) for d in day_set), None

    try:
        interval = int(interval)
        if interval < 1:
            raise ValueError
    except (TypeError, ValueError):
        return None, None, None, "recurrence_interval must be a positive integer"

    if unit not in RECURRENCE_UNITS:
        return None, None, None, f"recurrence_unit must be one of {sorted(RECURRENCE_UNITS)}"

    return interval, unit, None, None


def needs_seed_date(unit: str, days_csv: str) -> bool:
    """True when this recurrence is one that must be given a start date."""
    return unit == "weeks" and bool(days_csv)
