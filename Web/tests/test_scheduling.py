"""Storage frame vs. the owner's calendar.

Anchored on the real failure: a Canberra user (Australia/Sydney) with a 07:00
alarm on Mon/Wed/Fri saw the task land on Tue/Thu/Sat, because 07:00 local is
21:00 UTC the previous day and the weekday arithmetic ran on the stored UTC
date.

Sydney is the primary case rather than a fixed-offset zone on purpose: it
observes DST, so the stored UTC wall clock for a 07:00 task changes across
October and April while the local time the user sees must not.
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from recurrence import first_due_date, next_due_date
from scheduling import (
    due_fields,
    is_overdue,
    local_date_of,
    local_time_of,
    next_occurrence_of,
    parse_hhmm,
    resolve_tz,
)

SYD = ZoneInfo("Australia/Sydney")        # UTC+10 AEST / +11 AEDT — the real one
BNE = ZoneInfo("Australia/Brisbane")      # UTC+10 year round, DST-free control
LA = ZoneInfo("America/Los_Angeles")      # west of UTC
UTC = timezone.utc

MWF = "1,3,5"


def _freeze(monkeypatch, when):
    """Pin scheduling's clock, for the rules that depend on "now"."""
    import scheduling as mod

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return when.astimezone(tz) if tz else when.replace(tzinfo=None)

    monkeypatch.setattr(mod, "datetime", _DT)


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
    ("07:00", time(7, 0)), ("23:59", time(23, 59)),
    ("", None), (None, None), ("bogus", None), ("25:00", None),
])
def test_parse_hhmm(s, expected):
    assert parse_hhmm(s) == expected


# ---------------------------------------------------------------------------
# The reported failure
# ---------------------------------------------------------------------------

def test_morning_todo_is_stored_on_the_previous_utc_day():
    """The premise the whole module exists for."""
    due_at, due_on = due_fields(date(2026, 9, 9), time(7, 0), SYD)   # local Wed
    assert due_on is None
    assert due_at == datetime(2026, 9, 8, 21, 0)   # Tuesday 21:00 UTC, AEST


def test_local_date_recovers_the_day_the_owner_sees():
    assert local_date_of(datetime(2026, 9, 8, 21, 0), None, SYD) == date(2026, 9, 9)


def test_round_trip_is_stable_across_the_dst_start():
    for offset in range(70):   # spans 2026-10-04
        local_day = date(2026, 9, 1) + timedelta(days=offset)
        due_at, due_on = due_fields(local_day, time(7, 0), SYD)
        assert local_date_of(due_at, due_on, SYD) == local_day
        assert local_time_of(due_at, SYD) == time(7, 0)


def test_weekday_recurrence_stays_on_the_chosen_local_weekdays():
    """The end-to-end property that was broken: every occurrence the user sees
    must fall on a day they actually ticked."""
    local_time = time(7, 0)
    seeded = first_due_date(date(2026, 9, 3), MWF)          # from local Thursday
    due_at, due_on = due_fields(seeded, local_time, SYD)

    seen = []
    for _ in range(20):   # far enough to cross the 2026-10-04 DST start
        shown = local_date_of(due_at, due_on, SYD)
        assert local_time_of(due_at, SYD) == local_time
        seen.append(shown)
        due_at, due_on = due_fields(next_due_date(shown, 1, "weeks", MWF),
                                    local_time, SYD)

    assert seen[0] == date(2026, 9, 4)  # the Friday, not Saturday
    for d in seen:
        assert (d.weekday() + 1) % 7 in {1, 3, 5}, f"{d:%a} was never chosen"
    assert any(d >= date(2026, 10, 4) for d in seen), "should cross the DST start"


def test_the_old_utc_arithmetic_would_have_failed_this():
    """Guards the regression directly: advancing the stored UTC date lands the
    user on a weekday they never picked."""
    due_at, _ = due_fields(date(2026, 9, 9), time(7, 0), SYD)   # local Wed
    naive_next = next_due_date(due_at.date(), 1, "weeks", MWF)  # the old bug
    shown = local_date_of(datetime.combine(naive_next, due_at.time()), None, SYD)
    assert (shown.weekday() + 1) % 7 not in {1, 3, 5}


# ---------------------------------------------------------------------------
# Other zones and DST
# ---------------------------------------------------------------------------

def test_west_of_utc_is_also_handled():
    """Los Angeles evening crosses forward into the next UTC day."""
    due_at, _ = due_fields(date(2026, 9, 9), time(20, 0), LA)
    assert due_at.date() == date(2026, 9, 10)
    assert local_date_of(due_at, None, LA) == date(2026, 9, 9)


def test_utc_users_are_unaffected():
    due_at, _ = due_fields(date(2026, 9, 9), time(7, 0), UTC)
    assert due_at == datetime(2026, 9, 9, 7, 0)


@pytest.mark.parametrize("before_day,after_day,label", [
    (date(2026, 10, 1), date(2026, 10, 8), "DST starts 2026-10-04"),
    (date(2026, 4, 1), date(2026, 4, 8), "DST ends 2026-04-05"),
])
def test_local_time_survives_a_dst_transition(before_day, after_day, label):
    """A 07:00 weekly task stays at 07:00 local on both sides, which means the
    stored UTC wall clock has to move by an hour."""
    before, _ = due_fields(before_day, time(7, 0), SYD)
    after, _ = due_fields(after_day, time(7, 0), SYD)
    assert local_time_of(before, SYD) == time(7, 0), label
    assert local_time_of(after, SYD) == time(7, 0), label
    assert before.time() != after.time(), label


def test_brisbane_control_never_shifts():
    assert due_fields(date(2026, 10, 1), time(7, 0), BNE)[0].time() == time(21, 0)
    assert due_fields(date(2026, 10, 8), time(7, 0), BNE)[0].time() == time(21, 0)


# ---------------------------------------------------------------------------
# All-day todos
# ---------------------------------------------------------------------------

def test_all_day_todos_are_stored_as_a_plain_local_date():
    due_at, due_on = due_fields(date(2026, 9, 9), None, SYD)
    assert (due_at, due_on) == (None, date(2026, 9, 9))
    assert local_date_of(due_at, due_on, SYD) == date(2026, 9, 9)
    assert local_time_of(due_at, SYD) is None


def test_unscheduled_todos_pass_through():
    assert due_fields(None, None, SYD) == (None, None)
    assert local_date_of(None, None, SYD) is None


# ---------------------------------------------------------------------------
# next_occurrence_of — a time with no date
# ---------------------------------------------------------------------------

def test_a_time_still_ahead_today_resolves_to_today(monkeypatch):
    _freeze(monkeypatch, datetime(2026, 9, 3, 6, 0, tzinfo=SYD))
    assert next_occurrence_of(time(7, 0), SYD) == date(2026, 9, 3)


def test_a_time_already_past_resolves_to_tomorrow(monkeypatch):
    _freeze(monkeypatch, datetime(2026, 9, 3, 8, 0, tzinfo=SYD))
    assert next_occurrence_of(time(7, 0), SYD) == date(2026, 9, 4)


# ---------------------------------------------------------------------------
# is_overdue
# ---------------------------------------------------------------------------

def test_a_timed_todo_is_overdue_once_its_instant_passes():
    now = datetime(2026, 9, 3, 12, 0)
    assert is_overdue(datetime(2026, 9, 3, 11, 59), None, SYD, now)
    assert not is_overdue(datetime(2026, 9, 3, 12, 1), None, SYD, now)


def test_an_all_day_todo_is_not_overdue_during_its_own_day(monkeypatch):
    _freeze(monkeypatch, datetime(2026, 9, 3, 0, 1, tzinfo=SYD))
    assert not is_overdue(None, date(2026, 9, 3), SYD)
    assert is_overdue(None, date(2026, 9, 2), SYD)


def test_an_unscheduled_todo_is_never_overdue():
    assert not is_overdue(None, None, SYD)
