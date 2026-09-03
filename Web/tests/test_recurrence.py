"""Recurrence maths and payload parsing.

These are the tests that would have caught the weekday bug: not because the
arithmetic was wrong, but because nothing asserted that a weekday recurrence
ends up with a start date at all.
"""

from datetime import date

import pytest

from recurrence import (
    first_due_date,
    js_weekday,
    needs_seed_date,
    next_due_date,
    parse_recurrence,
    py_weekday,
    valid_time,
    weekday_set,
)

# A fixed week to anchor the weekday tests. 2026-09-06 is a Sunday, so the
# offsets line up with the JS numbering the app stores.
SUN = date(2026, 9, 6)
MON = date(2026, 9, 7)
TUE = date(2026, 9, 8)
WED = date(2026, 9, 9)
THU = date(2026, 9, 10)
FRI = date(2026, 9, 11)
SAT = date(2026, 9, 12)

MWF = "1,3,5"


# ---------------------------------------------------------------------------
# Weekday conversion
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("d,expected", [
    (SUN, 0), (MON, 1), (TUE, 2), (WED, 3), (THU, 4), (FRI, 5), (SAT, 6),
])
def test_js_weekday_matches_javascript_numbering(d, expected):
    assert js_weekday(d) == expected


@pytest.mark.parametrize("js_dow", range(7))
def test_py_weekday_is_the_inverse_of_js_weekday(js_dow):
    # Round-trip through a real date carrying that JS weekday.
    d = date.fromordinal(SUN.toordinal() + js_dow)
    assert js_weekday(d) == js_dow
    assert py_weekday(js_dow) == d.weekday()


def test_weekday_set_ignores_empty_segments():
    assert weekday_set("1,3,5") == {1, 3, 5}
    assert weekday_set("") == set()
    assert weekday_set("1,,5") == {1, 5}


# ---------------------------------------------------------------------------
# first_due_date — the fix for the reported bug
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("created,expected", [
    (SUN, MON),  # created Sunday   -> next M/W/F is Monday
    (MON, MON),  # created Monday   -> today already qualifies
    (TUE, WED),
    (WED, WED),
    (THU, FRI),
    (FRI, FRI),
    (SAT, date(2026, 9, 14)),  # wraps into the following week
])
def test_first_due_date_seeds_on_or_after_today(created, expected):
    assert first_due_date(created, MWF) == expected


def test_first_due_date_is_inclusive_unlike_next_due_date():
    """The distinction that mattered: seeding must include today, advancing
    must not, or a task ticked done would respawn onto the same day."""
    assert first_due_date(MON, MWF) == MON
    assert next_due_date(MON, 1, "weeks", MWF) == WED


def test_first_due_date_with_every_day_selected_is_today():
    assert first_due_date(THU, "0,1,2,3,4,5,6") == THU


# ---------------------------------------------------------------------------
# next_due_date — weekday recurrences
# ---------------------------------------------------------------------------

def test_weekday_recurrence_walks_the_selected_days_in_order():
    walked = []
    cur = MON
    for _ in range(7):
        cur = next_due_date(cur, 1, "weeks", MWF)
        walked.append(cur)
    assert walked == [
        WED, FRI,
        date(2026, 9, 14), date(2026, 9, 16), date(2026, 9, 18),  # Mon Wed Fri
        date(2026, 9, 21), date(2026, 9, 23),                     # Mon Wed
    ]


def test_weekday_recurrence_always_advances():
    """Never return `base`, or completing a task would spawn a duplicate due
    the same day and the fast-forward loop in the API would never terminate."""
    for start_offset in range(14):
        base = date.fromordinal(SUN.toordinal() + start_offset)
        assert next_due_date(base, 1, "weeks", MWF) > base


def test_single_selected_day_lands_one_week_later():
    assert next_due_date(WED, 1, "weeks", "3") == date(2026, 9, 16)


def test_weekday_recurrence_crosses_month_and_year_boundaries():
    # Wed 2026-12-30 -> Fri 2027-01-01
    assert next_due_date(date(2026, 12, 30), 1, "weeks", MWF) == date(2027, 1, 1)


# ---------------------------------------------------------------------------
# next_due_date — interval recurrences
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("interval,unit,expected", [
    (1, "days", date(2026, 9, 8)),
    (3, "days", date(2026, 9, 10)),
    (1, "weeks", date(2026, 9, 14)),
    (2, "weeks", date(2026, 9, 21)),
    (1, "months", date(2026, 10, 7)),
    (1, "years", date(2027, 9, 7)),
])
def test_interval_recurrence(interval, unit, expected):
    assert next_due_date(MON, interval, unit, None) == expected


def test_month_arithmetic_clamps_to_a_real_day():
    # 31 Jan + 1 month has no 31st to land on.
    assert next_due_date(date(2026, 1, 31), 1, "months", None) == date(2026, 2, 28)


def test_leap_day_plus_one_year_clamps():
    assert next_due_date(date(2028, 2, 29), 1, "years", None) == date(2029, 2, 28)


def test_unknown_unit_returns_base_unchanged():
    assert next_due_date(MON, 1, "fortnights", None) == MON


# ---------------------------------------------------------------------------
# next_due_date — monthly weekday recurrences
# ---------------------------------------------------------------------------

def test_last_monday_of_month():
    # Last Monday of Sept 2026 is the 28th.
    assert next_due_date(date(2026, 9, 1), None, "monthly-last", "1") == date(2026, 9, 28)


def test_last_monday_rolls_into_next_month_once_passed():
    assert next_due_date(date(2026, 9, 28), None, "monthly-last", "1") == date(2026, 10, 26)


def test_second_last_monday_of_month():
    assert next_due_date(date(2026, 9, 1), None, "monthly-2last", "1") == date(2026, 9, 21)


def test_monthly_weekday_rolls_over_december():
    assert next_due_date(date(2026, 12, 28), None, "monthly-last", "1") == date(2027, 1, 25)


# ---------------------------------------------------------------------------
# valid_time
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("t", ["00:00", "09:30", "23:59"])
def test_valid_times(t):
    assert valid_time(t)


@pytest.mark.parametrize("t", ["24:00", "12:60", "9", "", "aa:bb", "12:30:00", None, 930])
def test_invalid_times(t):
    assert not valid_time(t)


# ---------------------------------------------------------------------------
# parse_recurrence
# ---------------------------------------------------------------------------

def test_no_recurrence_fields_is_not_an_error():
    assert parse_recurrence({"title": "x"}) == (None, None, None, None)


def test_weekday_recurrence_normalises_to_weeks_with_synthetic_interval():
    assert parse_recurrence({
        "recurrence_unit": "weeks", "recurrence_days": MWF,
    }) == (1, "weeks", MWF, None)


def test_weekday_recurrence_sorts_and_dedupes_days():
    assert parse_recurrence({
        "recurrence_unit": "weeks", "recurrence_days": "5,1,3,1",
    }) == (1, "weeks", MWF, None)


def test_weekday_recurrence_overrides_a_supplied_interval():
    """The days fully determine the schedule, so "every 2 weeks on M/W/F" is
    not representable; the interval is discarded rather than half-honoured."""
    interval, unit, days, err = parse_recurrence({
        "recurrence_unit": "weeks", "recurrence_interval": 2, "recurrence_days": MWF,
    })
    assert (interval, unit, days, err) == (1, "weeks", MWF, None)


def test_empty_days_is_rejected_with_a_message_about_days():
    _, _, _, err = parse_recurrence({"recurrence_unit": "weeks", "recurrence_days": ""})
    assert err and "weekday" in err


def test_interval_recurrence_passes_through():
    assert parse_recurrence({
        "recurrence_unit": "days", "recurrence_interval": 3,
    }) == (3, "days", None, None)


def test_weeks_without_days_is_a_plain_interval_recurrence():
    assert parse_recurrence({
        "recurrence_unit": "weeks", "recurrence_interval": 2,
    }) == (2, "weeks", None, None)


@pytest.mark.parametrize("interval", [0, -1, "abc", None])
def test_bad_interval_is_rejected(interval):
    _, _, _, err = parse_recurrence({"recurrence_unit": "days", "recurrence_interval": interval})
    assert err and "interval" in err


def test_unknown_unit_is_rejected():
    _, _, _, err = parse_recurrence({"recurrence_unit": "fortnights", "recurrence_interval": 1})
    assert err and "recurrence_unit" in err


def test_monthly_weekday_requires_days():
    _, _, _, err = parse_recurrence({"recurrence_unit": "monthly-last"})
    assert err and "required" in err


def test_monthly_weekday_rejects_multiple_days():
    _, _, _, err = parse_recurrence({
        "recurrence_unit": "monthly-last", "recurrence_days": "1,3",
    })
    assert err and "single weekday" in err


def test_monthly_weekday_carries_no_interval():
    assert parse_recurrence({
        "recurrence_unit": "monthly-2last", "recurrence_days": "1",
    }) == (None, "monthly-2last", "1", None)


@pytest.mark.parametrize("days", ["7", "-1", "0,9"])
def test_out_of_range_weekdays_are_rejected(days):
    _, _, _, err = parse_recurrence({"recurrence_unit": "weeks", "recurrence_days": days})
    assert err


def test_non_numeric_days_are_rejected():
    _, _, _, err = parse_recurrence({"recurrence_unit": "weeks", "recurrence_days": "mon,wed"})
    assert err


# ---------------------------------------------------------------------------
# needs_seed_date
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("unit,days,expected", [
    ("weeks", MWF, True),
    ("weeks", None, False),     # plain "every N weeks" keeps whatever date it has
    ("days", None, False),
    ("monthly-last", "1", False),
    (None, None, False),
])
def test_needs_seed_date(unit, days, expected):
    assert needs_seed_date(unit, days) is expected
