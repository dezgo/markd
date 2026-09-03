"""Storage frame vs. the owner's calendar.

Anchored on the real failure: a Canberra user (Australia/Sydney) with a 07:00
alarm on Mon/Wed/Fri saw the task land on Tue/Thu/Sat, because 07:00 local is
21:00 UTC the previous day and the weekday arithmetic ran on the stored UTC
date.

Sydney is the primary case rather than a fixed-offset zone on purpose: it
observes DST, so the stored UTC wall clock for a 07:00 task changes across
October and April while the local time the user sees must not.
"""

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from recurrence import first_due_date, next_due_date
from scheduling import (
    local_date,
    local_time_of_day,
    parse_hhmm,
    resolve_tz,
    to_storage,
)

SYD = ZoneInfo("Australia/Sydney")        # UTC+10 AEST / +11 AEDT — the real one
BNE = ZoneInfo("Australia/Brisbane")      # UTC+10 year round, DST-free control
LA = ZoneInfo("America/Los_Angeles")      # west of UTC
UTC = timezone.utc

MWF = "1,3,5"


# ---------------------------------------------------------------------------
# resolve_tz / parse_hhmm
# ---------------------------------------------------------------------------

def _offset(tz):
    return datetime(2026, 9, 3, 12, tzinfo=timezone.utc).astimezone(tz).utcoffset()


@pytest.mark.parametrize("name", ["Not/AZone", "", None, "Mars/Olympus"])
def test_resolve_tz_falls_back_rather_than_raising(name):
    assert _offset(resolve_tz(name)) == _offset(timezone.utc)


def test_resolve_tz_returns_the_real_zone():
    assert resolve_tz("Australia/Sydney") == SYD


@pytest.mark.parametrize("s,expected", [
    ("07:00", time(7, 0)),
    ("23:59", time(23, 59)),
    ("", None), (None, None), ("bogus", None), ("25:00", None),
])
def test_parse_hhmm(s, expected):
    assert parse_hhmm(s) == expected


# ---------------------------------------------------------------------------
# The reported failure
# ---------------------------------------------------------------------------

def test_morning_todo_is_stored_on_the_previous_utc_day():
    """The premise the whole module exists for."""
    stored_date, stored_time = to_storage(date(2026, 9, 9), time(7, 0), SYD)  # local Wed
    assert stored_date == date(2026, 9, 8)   # Tuesday in UTC
    assert stored_time == "21:00"            # AEST in September


def test_local_date_recovers_the_day_the_owner_sees():
    assert local_date(date(2026, 9, 8), "21:00", SYD) == date(2026, 9, 9)


def test_round_trip_is_stable():
    # Spans the 2026-10-04 DST start, so the round trip is exercised on both
    # sides of the transition and on the transition day itself.
    for offset in range(70):
        local_day = date.fromordinal(date(2026, 9, 1).toordinal() + offset)
        stored_date, stored_time = to_storage(local_day, time(7, 0), SYD)
        assert local_date(stored_date, stored_time, SYD) == local_day
        assert local_time_of_day(stored_date, stored_time, SYD) == time(7, 0)


def test_weekday_recurrence_stays_on_the_chosen_local_weekdays():
    """The end-to-end property that was broken: every occurrence a Brisbane
    user sees must fall on a day they actually ticked."""
    wanted = {1, 3, 5}  # Mon, Wed, Fri
    local_time = time(7, 0)

    seeded = first_due_date(date(2026, 9, 3), MWF)          # from local Thursday
    stored_date, stored_time = to_storage(seeded, local_time, SYD)

    seen = []
    for _ in range(20):   # far enough to cross the 2026-10-04 DST start
        shown = local_date(stored_date, stored_time, SYD)
        assert local_time_of_day(stored_date, stored_time, SYD) == local_time
        seen.append(shown)
        nxt = next_due_date(shown, 1, "weeks", MWF)
        stored_date, stored_time = to_storage(nxt, local_time, SYD)

    assert any(d >= date(2026, 10, 4) for d in seen), "should cross the DST start"

    assert seen[0] == date(2026, 9, 4)  # the Friday, not Saturday
    for d in seen:
        assert (d.weekday() + 1) % 7 in wanted, f"{d} ({d:%a}) is not a chosen weekday"


def test_the_old_utc_arithmetic_would_have_failed_this():
    """Guards the regression directly: advancing the stored UTC date lands the
    Brisbane user on a weekday they never picked."""
    stored_date, stored_time = to_storage(date(2026, 9, 9), time(7, 0), SYD)  # local Wed
    naive_next = next_due_date(stored_date, 1, "weeks", MWF)   # advances the UTC date
    shown = local_date(naive_next, stored_time, SYD)
    assert (shown.weekday() + 1) % 7 not in {1, 3, 5}


# ---------------------------------------------------------------------------
# Other zones
# ---------------------------------------------------------------------------

def test_west_of_utc_is_also_handled():
    """Los Angeles evening crosses forward into the next UTC day."""
    stored_date, stored_time = to_storage(date(2026, 9, 9), time(20, 0), LA)
    assert stored_date == date(2026, 9, 10)
    assert local_date(stored_date, stored_time, LA) == date(2026, 9, 9)


def test_utc_users_are_unaffected():
    assert to_storage(date(2026, 9, 9), time(7, 0), UTC) == (date(2026, 9, 9), "07:00")
    assert local_date(date(2026, 9, 9), "07:00", UTC) == date(2026, 9, 9)


@pytest.mark.parametrize("before_day,after_day,label", [
    (date(2026, 10, 1), date(2026, 10, 8), "DST starts 2026-10-04"),
    (date(2026, 4, 1), date(2026, 4, 8), "DST ends 2026-04-05"),
])
def test_local_time_survives_a_dst_transition(before_day, after_day, label):
    """A 07:00 weekly task stays at 07:00 local on both sides, which means the
    stored UTC wall clock has to move by an hour."""
    before = to_storage(before_day, time(7, 0), SYD)
    after = to_storage(after_day, time(7, 0), SYD)
    assert local_time_of_day(*before, SYD) == time(7, 0), label
    assert local_time_of_day(*after, SYD) == time(7, 0), label
    assert before[1] != after[1], label


def test_brisbane_control_never_shifts():
    """The DST-free neighbour: same offset all year, so the stored time is
    stable across the dates where Sydney moves."""
    assert to_storage(date(2026, 10, 1), time(7, 0), BNE)[1] == "21:00"
    assert to_storage(date(2026, 10, 8), time(7, 0), BNE)[1] == "21:00"


# ---------------------------------------------------------------------------
# Date-only todos
# ---------------------------------------------------------------------------

def test_date_only_todos_are_already_local_and_pass_through():
    assert local_date(date(2026, 9, 9), None, SYD) == date(2026, 9, 9)
    assert local_time_of_day(date(2026, 9, 9), None, SYD) is None
    assert to_storage(date(2026, 9, 9), None, SYD) == (date(2026, 9, 9), None)


def test_missing_date_is_passed_through():
    assert local_date(None, "07:00", SYD) is None
