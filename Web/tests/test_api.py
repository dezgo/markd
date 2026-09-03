"""End-to-end API behaviour.

Written against the routes as they were before being split into blueprints, so
it doubles as the check that the split changed nothing. Covers the todo CRUD
surface, recurrence handling through the real request path, auth gating, and
the settings validation.
"""

from datetime import date, timedelta

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

def test_weekday_recurrence_is_seeded_with_a_start_date(api, set_timezone):
    set_timezone("UTC")
    t = api.post("/todos", {"title": "gym", "recurrence_unit": "weeks",
                            "recurrence_days": MWF}).get_json()
    assert t["due_date"] is not None, "a weekday recurrence must get a start date"
    assert t["recurrence_days"] == MWF
    assert t["recurrence_interval"] == 1
    seeded = date.fromisoformat(t["due_date"])
    assert (seeded.weekday() + 1) % 7 in {1, 3, 5}
    assert seeded >= date.today()


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
    long_ago = (date.today() - timedelta(days=90)).isoformat()
    t = api.post("/todos", {"title": "gym", "due_date": long_ago,
                            "recurrence_unit": "weeks",
                            "recurrence_days": MWF}).get_json()
    api.patch(f"/todos/{t['id']}", {"done": True})
    child = [x for x in api.get("/todos").get_json()
             if x["spawned_from_id"] == t["id"]][0]
    assert date.fromisoformat(child["due_date"]) > date.today()


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


def test_weekday_recurrence_respects_the_owners_timezone(api, set_timezone):
    """The Canberra bug, through the real API: every occurrence must land on a
    weekday the user actually chose, in their own calendar."""
    from zoneinfo import ZoneInfo
    from scheduling import local_date
    set_timezone("Australia/Sydney")
    syd = ZoneInfo("Australia/Sydney")

    t = api.post("/todos", {"title": "Strength day", "due_time": "07:00",
                            "recurrence_unit": "weeks",
                            "recurrence_days": MWF}).get_json()
    for _ in range(8):
        shown = local_date(date.fromisoformat(t["due_date"]), t["due_time"], syd)
        assert (shown.weekday() + 1) % 7 in {1, 3, 5}, f"{shown:%a} was never chosen"
        api.patch(f"/todos/{t['id']}", {"done": True})
        t = [x for x in api.get("/todos").get_json()
             if x["spawned_from_id"] == t["id"]][0]


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
