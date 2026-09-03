"""The admin usage overview, and who may see it."""

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
