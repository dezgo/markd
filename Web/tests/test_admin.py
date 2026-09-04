"""The admin usage overview, and who may see it."""

import itertools
from datetime import date, datetime, timedelta, timezone

import pytest

ADMIN = {"email": "admin@example.com", "password": "test-password-123"}


@pytest.fixture
def other_user(flask_app):
    """A second, verified account that is not the admin."""
    from database import db
    from models import User
    from werkzeug.security import generate_password_hash
    with flask_app.app_context():
        u = User.query.filter_by(email="other@example.com").first()
        if u is None:
            u = User(email="other@example.com",
                     password_hash=generate_password_hash("other-password-123"),
                     email_verified=True)
            db.session.add(u)
            db.session.commit()
        return u.id


def _login_admin(client):
    return client.post("/login", data=ADMIN)


def _login_other(client):
    return client.post("/login", data={"email": "other@example.com",
                                       "password": "other-password-123"})


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------

def test_logged_out_is_redirected_to_login(client):
    r = client.get("/admin")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_a_non_admin_gets_404_not_403(client, other_user):
    """404 so the route's existence is not advertised to ordinary accounts."""
    _login_other(client)
    assert client.get("/admin").status_code == 404


def test_the_admin_can_see_it(client):
    _login_admin(client)
    assert client.get("/admin").status_code == 200


def test_an_api_key_alone_does_not_open_the_admin_page(client):
    """The API key acts as user 1 for the todo API; it must not unlock a page."""
    r = client.get("/admin", headers={"X-API-Key": "test-api-key"})
    assert r.status_code == 302


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------

def test_every_account_is_listed(client, other_user):
    _login_admin(client)
    body = client.get("/admin").get_data(as_text=True)
    assert "admin@example.com" in body
    assert "other@example.com" in body


def test_counts_are_per_user(client, api, other_user, flask_app):
    from database import db
    from models import Todo
    api.post("/todos", {"title": "admin task one"})
    api.post("/todos", {"title": "admin task two"})
    with flask_app.app_context():
        db.session.add(Todo(user_id=other_user, title="their task"))
        db.session.commit()

    _login_admin(client)
    from routes.admin import _gather
    with flask_app.app_context():
        stats = _gather()

    by_id = {r["id"]: r for r in stats["rows"]}
    assert by_id[1]["todos_total"] == 2
    assert by_id[other_user]["todos_total"] == 1
    assert stats["todo_count"] == 3


def test_active_means_recently_touched(client, api, flask_app):
    from database import db
    from models import Todo
    from routes.admin import _gather

    api.post("/todos", {"title": "recent"})
    with flask_app.app_context():
        stats = _gather()
        assert next(r for r in stats["rows"] if r["id"] == 1)["is_active"]

    # Age the row past the window.
    with flask_app.app_context():
        for t in Todo.query.filter_by(user_id=1).all():
            t.updated_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400)
        db.session.commit()
        stats = _gather()
        assert not next(r for r in stats["rows"] if r["id"] == 1)["is_active"]


def test_orphan_subscriptions_are_surfaced(client, flask_app):
    """Residue of the old split-brain account deletion."""
    from database import db
    from models import PushSubscription
    from routes.admin import _gather
    with flask_app.app_context():
        db.session.add(PushSubscription(user_id=999999, endpoint="https://x/1",
                                        p256dh="p", auth="a"))
        db.session.commit()
        assert _gather()["orphan_subscriptions"] == 1
        PushSubscription.query.filter_by(user_id=999999).delete()
        db.session.commit()


def test_the_page_does_not_run_a_query_per_user(flask_app, other_user):
    """Aggregates, not N+1 — the page has to stay cheap to open."""
    from routes.admin import _gather
    from sqlalchemy import event
    from database import db

    seen = []
    with flask_app.app_context():
        engine = db.engine

        def _count(conn, cursor, statement, *a):
            seen.append(statement)

        event.listen(engine, "before_cursor_execute", _count)
        try:
            _gather()
        finally:
            event.remove(engine, "before_cursor_execute", _count)

    selects = [s for s in seen if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) <= 10, f"{len(selects)} queries — looks like an N+1"


# ---------------------------------------------------------------------------
# Scale — the instance has ~1600 accounts, most of them dormant bot signups
# ---------------------------------------------------------------------------

# Emails must stay unique across every test in the session-scoped database, and
# a real KDF over hundreds of rows costs more than the whole rest of the suite —
# nothing here ever logs these accounts in.
_bulk_seq = itertools.count()
_UNUSABLE_HASH = "x"


def _bulk_users(flask_app, n, verified=False, days_old=90):
    from database import db
    from models import User
    emails = [f"bot{next(_bulk_seq)}@example.invalid" for _ in range(n)]
    with flask_app.app_context():
        when = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_old)
        db.session.bulk_save_objects([
            User(email=e, password_hash=_UNUSABLE_HASH,
                 email_verified=verified, created_at=when) for e in emails])
        db.session.commit()
    return emails


def test_dormant_unverified_accounts_are_counted_but_not_listed(flask_app, api):
    """The whole point of the rework: 1267 never-verified rows must not bury
    the handful of real accounts."""
    from routes.admin import _gather
    api.post("/todos", {"title": "a real todo"})
    bots = _bulk_users(flask_app, 40)
    with flask_app.app_context():
        stats = _gather()

    assert stats["user_count"] >= 41
    assert stats["hidden_unverified"] >= 40
    listed = {r["email"] for r in stats["rows"]}
    assert not (set(bots) & listed)
    assert stats["unverified_count"] >= 40


def test_a_recent_signup_is_listed_even_though_unverified(flask_app):
    """Inside the grace window it is still plausibly a person mid-signup."""
    from routes.admin import _gather
    fresh = _bulk_users(flask_app, 3, days_old=1)
    with flask_app.app_context():
        listed = {r["email"] for r in _gather()["rows"]}
    assert set(fresh) <= listed


def test_the_account_table_is_capped(flask_app):
    from routes.admin import ROW_LIMIT, _gather
    _bulk_users(flask_app, ROW_LIMIT + 25, verified=True)
    with flask_app.app_context():
        stats = _gather()
    assert len(stats["rows"]) == ROW_LIMIT
    assert stats["hidden_idle"] >= 25


def test_totals_count_everyone_not_just_listed_rows(flask_app, api):
    """A capped table must not produce capped numbers."""
    from routes.admin import ROW_LIMIT, _gather
    _bulk_users(flask_app, ROW_LIMIT + 5, verified=True)
    with flask_app.app_context():
        stats = _gather()
    assert stats["user_count"] > len(stats["rows"])
    assert stats["verified_count"] >= ROW_LIMIT + 5


def test_the_signup_timeline_buckets_by_month(flask_app):
    from routes.admin import _gather
    _bulk_users(flask_app, 5, days_old=400)
    _bulk_users(flask_app, 7, days_old=2)
    with flask_app.app_context():
        stats = _gather()
    assert stats["timeline"], "expected at least one month"
    assert stats["timeline_peak"] >= 5
    assert stats["timeline"] == sorted(stats["timeline"]), "months must be ordered"
    assert sum(n for _, n in stats["timeline"]) <= stats["user_count"]


def test_the_page_still_renders_at_scale(client, flask_app):
    from routes.admin import _gather
    dormant = _bulk_users(flask_app, 300)
    _login_admin(client)
    r = client.get("/admin")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert not any(e in body for e in dormant), "dormant signups reached the page"
    assert "not listed" in body, "the page must account for the rows it hides"


def test_query_count_does_not_grow_with_users(flask_app):
    """The N+1 guard, re-armed against a realistic row count."""
    from routes.admin import _gather
    from sqlalchemy import event
    from database import db

    def _count_queries():
        seen = []
        with flask_app.app_context():
            engine = db.engine

            def _tap(conn, cursor, statement, *a):
                seen.append(statement)

            event.listen(engine, "before_cursor_execute", _tap)
            try:
                _gather()
            finally:
                event.remove(engine, "before_cursor_execute", _tap)
        return len([s for s in seen if s.lstrip().upper().startswith("SELECT")])

    small = _count_queries()
    _bulk_users(flask_app, 250, verified=True)
    assert _count_queries() == small, "query count must be independent of user count"
