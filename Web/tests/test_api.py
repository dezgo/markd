"""End-to-end API behaviour.

Written against the routes as they were before being split into blueprints, so
it doubles as the check that the split changed nothing. Covers the todo CRUD
surface, recurrence handling through the real request path, auth gating, and
the settings validation.
"""

from datetime import date, time, timedelta

import pytest

MWF = "1,3,5"


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------

def test_create_and_list(api):
    r = api.post("/todos", {"title": "Buy milk"})
    assert r.status_code == 201
    assert r.get_json()["title"] == "Buy milk"

    listed = api.get("/todos").get_json()
    assert [t["title"] for t in listed] == ["Buy milk"]


def test_title_is_required(api):
    assert api.post("/todos", {}).status_code == 400
    assert api.post("/todos", {"title": "   "}).status_code == 400


def test_title_is_trimmed(api):
    assert api.post("/todos", {"title": "  spaced  "}).get_json()["title"] == "spaced"


def test_update_title(api):
    tid = api.post("/todos", {"title": "old"}).get_json()["id"]
    r = api.patch(f"/todos/{tid}", {"title": "new"})
    assert r.status_code == 200
    assert r.get_json()["title"] == "new"


def test_update_rejects_empty_title(api):
    tid = api.post("/todos", {"title": "keep"}).get_json()["id"]
    assert api.patch(f"/todos/{tid}", {"title": "  "}).status_code == 400
    assert api.get("/todos").get_json()[0]["title"] == "keep"


def test_delete(api):
    tid = api.post("/todos", {"title": "gone"}).get_json()["id"]
    assert api.delete(f"/todos/{tid}").status_code == 204
    assert api.get("/todos").get_json() == []


def test_missing_todo_is_404(api):
    assert api.patch("/todos/999999", {"title": "x"}).status_code == 404
    assert api.delete("/todos/999999").status_code == 404


def test_notes_round_trip_and_clear(api):
    tid = api.post("/todos", {"title": "t", "notes": "some note"}).get_json()["id"]
    assert api.get("/todos").get_json()[0]["notes"] == "some note"
    assert api.patch(f"/todos/{tid}", {"notes": None}).get_json()["notes"] is None


# ---------------------------------------------------------------------------
# Dates and times
# ---------------------------------------------------------------------------

def test_due_date_round_trip(api):
    r = api.post("/todos", {"title": "t", "due_date": "2026-09-10"})
    assert r.get_json()["due_date"] == "2026-09-10"


@pytest.mark.parametrize("bad", ["10-09-2026", "2026-13-01", "not-a-date"])
def test_bad_due_date_is_rejected(api, bad):
    assert api.post("/todos", {"title": "t", "due_date": bad}).status_code == 400


@pytest.mark.parametrize("bad", ["24:00", "9", "aa:bb"])
def test_bad_due_time_is_rejected(api, bad):
    assert api.post("/todos", {"title": "t", "due_time": bad}).status_code == 400


def test_due_date_can_be_cleared(api):
    tid = api.post("/todos", {"title": "t", "due_date": "2026-09-10"}).get_json()["id"]
    assert api.patch(f"/todos/{tid}", {"due_date": None}).get_json()["due_date"] is None


# ---------------------------------------------------------------------------
# Recurrence through the real request path
# ---------------------------------------------------------------------------

def _utc_today():
    """Today in the zone these tests put the user in.

    The built-in reads the machine's local zone. On a UTC+10 developer machine
    that is a day ahead of the app's UTC view between midnight and 10am, so
    comparing the two made these tests fail for ten hours a day — the same
    instant-versus-calendar-date confusion the recurrence code itself had.
    """
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).date()


def test_weekday_recurrence_is_seeded_with_a_start_date(api, set_timezone):
    set_timezone("UTC")
    t = api.post("/todos", {"title": "gym", "recurrence_unit": "weeks",
                            "recurrence_days": MWF}).get_json()
    assert t["due_date"] is not None, "a weekday recurrence must get a start date"
    assert t["recurrence_days"] == MWF
    assert t["recurrence_interval"] == 1
    seeded = date.fromisoformat(t["due_date"])
    assert (seeded.weekday() + 1) % 7 in {1, 3, 5}
    assert seeded >= _utc_today()


def test_interval_recurrence_is_not_given_a_date(api):
    t = api.post("/todos", {"title": "plants", "recurrence_unit": "days",
                            "recurrence_interval": 3}).get_json()
    assert t["due_date"] is None
    assert (t["recurrence_interval"], t["recurrence_unit"]) == (3, "days")


def test_weekday_recurrence_with_no_days_is_rejected(api):
    r = api.post("/todos", {"title": "x", "recurrence_unit": "weeks",
                            "recurrence_days": ""})
    assert r.status_code == 400
    assert "weekday" in r.get_json()["error"]


def test_completing_a_recurring_todo_spawns_the_next_one(api, set_timezone):
    set_timezone("UTC")
    t = api.post("/todos", {"title": "gym", "recurrence_unit": "weeks",
                            "recurrence_days": MWF}).get_json()
    api.patch(f"/todos/{t['id']}", {"done": True})

    todos = api.get("/todos").get_json()
    children = [x for x in todos if x["spawned_from_id"] == t["id"]]
    assert len(children) == 1
    child = children[0]
    assert child["done"] is False
    assert date.fromisoformat(child["due_date"]) > date.fromisoformat(t["due_date"])
    assert (date.fromisoformat(child["due_date"]).weekday() + 1) % 7 in {1, 3, 5}


def test_the_spawned_todo_is_always_in_the_future(api, set_timezone):
    """A long-overdue recurring task must not respawn into the past."""
    set_timezone("UTC")
    long_ago = (_utc_today() - timedelta(days=90)).isoformat()
    t = api.post("/todos", {"title": "gym", "due_date": long_ago,
                            "recurrence_unit": "weeks",
                            "recurrence_days": MWF}).get_json()
    api.patch(f"/todos/{t['id']}", {"done": True})
    child = [x for x in api.get("/todos").get_json()
             if x["spawned_from_id"] == t["id"]][0]
    assert date.fromisoformat(child["due_date"]) > _utc_today()


def test_un_completing_removes_the_spawned_todo(api, set_timezone):
    set_timezone("UTC")
    t = api.post("/todos", {"title": "gym", "recurrence_unit": "weeks",
                            "recurrence_days": MWF}).get_json()
    api.patch(f"/todos/{t['id']}", {"done": True})
    assert any(x["spawned_from_id"] == t["id"] for x in api.get("/todos").get_json())

    api.patch(f"/todos/{t['id']}", {"done": False})
    assert not any(x["spawned_from_id"] == t["id"] for x in api.get("/todos").get_json())


def test_a_non_recurring_todo_spawns_nothing(api):
    t = api.post("/todos", {"title": "one off"}).get_json()
    api.patch(f"/todos/{t['id']}", {"done": True})
    assert len(api.get("/todos").get_json()) == 1


def _as_local(todo, tz):
    """What the client renders: the API's UTC pair, seen in the owner's zone."""
    from datetime import datetime, timezone
    if todo["due_time"] is None:
        return date.fromisoformat(todo["due_date"]), None
    utc = datetime.combine(date.fromisoformat(todo["due_date"]),
                           datetime.strptime(todo["due_time"], "%H:%M").time(),
                           tzinfo=timezone.utc)
    local = utc.astimezone(tz)
    return local.date(), local.time()


def test_weekday_recurrence_respects_the_owners_timezone(api, set_timezone):
    """The Canberra bug, through the real API: every occurrence must land on a
    weekday the user actually chose, in their own calendar — and keep 07:00
    across the DST change."""
    from datetime import time
    from zoneinfo import ZoneInfo
    set_timezone("Australia/Sydney")
    syd = ZoneInfo("Australia/Sydney")

    t = api.post("/todos", {"title": "Strength day", "due_time": "07:00",
                            "recurrence_unit": "weeks",
                            "recurrence_days": MWF}).get_json()
    for _ in range(20):
        shown, at = _as_local(t, syd)
        assert (shown.weekday() + 1) % 7 in {1, 3, 5}, f"{shown:%a} was never chosen"
        assert at == time(7, 0), f"local time drifted to {at}"
        api.patch(f"/todos/{t['id']}", {"done": True})
        t = [x for x in api.get("/todos").get_json()
             if x["spawned_from_id"] == t["id"]][0]


def _freeze_local(monkeypatch, when):
    """Pin the clock scheduling reads, so "today" stops being the test's input.

    The regression below was live for a day before anything went red, because
    the only test covering it happened to run on a Friday.
    """
    import scheduling as mod
    from datetime import datetime as _real

    class _DT(_real):
        @classmethod
        def now(cls, tz=None):
            return when.astimezone(tz) if tz else when.replace(tzinfo=None)

    monkeypatch.setattr(mod, "datetime", _DT)


@pytest.mark.parametrize("day_offset", range(7))
def test_a_weekday_recurrence_never_starts_on_an_unchosen_day(
        api, set_timezone, monkeypatch, day_offset):
    """Creating "07:00 on Mon/Wed/Fri" must land on Mon, Wed or Fri whatever
    day it is created — the payload the UI actually sends carries a time and no
    date, and resolving that bare time to "tomorrow" once put the first
    occurrence on a day the user never ticked."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    syd = ZoneInfo("Australia/Sydney")
    set_timezone("Australia/Sydney")

    # 11:00 local: past 07:00, so the bare time resolves forward to tomorrow.
    created_on = datetime(2026, 9, 7, 11, 0, tzinfo=syd) + timedelta(days=day_offset)
    _freeze_local(monkeypatch, created_on)

    t = api.post("/todos", {"title": "Strength day", "due_time": "07:00",
                            "recurrence_unit": "weeks",
                            "recurrence_days": MWF}).get_json()
    shown, at = _as_local(t, syd)
    assert (shown.weekday() + 1) % 7 in {1, 3, 5},         f"created {created_on:%a}, first occurrence landed on {shown:%a}"
    assert at == time(7, 0)
    assert shown >= created_on.date(), "must not seed into the past"


def test_clearing_the_date_reseeds_onto_a_chosen_weekday(api, set_timezone, monkeypatch):
    """The documented repair for a todo already stuck on the wrong day: open it,
    clear the date, save. That sends date=null with the time still set, which is
    the same shape as create — so it has to snap the same way."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    syd = ZoneInfo("Australia/Sydney")
    set_timezone("Australia/Sydney")
    _freeze_local(monkeypatch, datetime(2026, 9, 11, 11, 0, tzinfo=syd))   # a Friday

    t = api.post("/todos", {"title": "Strength day", "due_date": "2026-09-12",
                            "due_time": "07:00", "recurrence_unit": "weeks",
                            "recurrence_days": MWF}).get_json()
    assert _as_local(t, syd)[0].weekday() == 5, "premise: starts on a Saturday"

    t = api.patch(f"/todos/{t['id']}", {"due_date": None, "due_time": "07:00"}).get_json()
    shown, at = _as_local(t, syd)
    assert (shown.weekday() + 1) % 7 in {1, 3, 5}, f"still on {shown:%a}"
    assert at == time(7, 0)


def test_an_explicit_date_is_still_obeyed(api, set_timezone):
    """The snap must not override a date the user typed, even an odd one."""
    set_timezone("Australia/Sydney")
    t = api.post("/todos", {"title": "one-off start", "due_date": "2026-09-12",
                            "due_time": "07:00", "recurrence_unit": "weeks",
                            "recurrence_days": MWF}).get_json()
    assert t["due_date"] is not None


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------

def test_todo_api_rejects_a_missing_key(client):
    assert client.get("/todos").status_code == 401


def test_todo_api_rejects_a_wrong_key(client):
    assert client.get("/todos", headers={"X-API-Key": "nope"}).status_code == 401


@pytest.mark.parametrize("path", ["/app", "/settings", "/diagnostics",
                                  "/push/vapid-public-key"])
def test_session_routes_redirect_when_logged_out(client, path):
    r = client.get(path)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_public_pages_are_reachable(client):
    for path in ("/", "/login", "/signup", "/forgot-password", "/version"):
        assert client.get(path).status_code == 200, path


def test_version_endpoint_matches_the_module_constant(client):
    import app as app_module
    assert client.get("/version").get_json()["version"] == app_module.APP_VERSION


def test_static_bundles_have_the_version_stamped_in(client):
    import app as app_module
    body = client.get("/app.js").get_data(as_text=True)
    assert "__APP_VERSION__" not in body
    assert app_module.APP_VERSION in body


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def _login(client):
    return client.post("/login", data={"email": "admin@example.com",
                                       "password": "test-password-123"},
                       follow_redirects=False)


def test_settings_validation(client):
    import json
    _login(client)
    hdr = {"Content-Type": "application/json"}

    def patch(payload):
        return client.patch("/api/settings", data=json.dumps(payload), headers=hdr)

    assert patch({"overdue_check_time": "25:00"}).status_code == 400
    assert patch({"timezone": "Not/AZone"}).status_code == 400
    assert patch({"theme": "chartreuse"}).status_code == 400

    assert patch({"overdue_check_time": "07:30"}).status_code == 200
    assert patch({"timezone": "Australia/Sydney"}).status_code == 200
    assert patch({"theme": "mint"}).get_json()["theme"] == "mint"
