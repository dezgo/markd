"""Which accounts the flood cleanup would delete.

The criterion is about use, not about how a row was created: an account that
owns no todo and has no push subscription, made before the cutoff. Verified
status is deliberately not part of it — the old GET verification link was
tripped by mail security scanners, so it says nothing about a person.

These tests exist to pin the survivors. Getting this wrong deletes someone's
real account.
"""

import itertools
from datetime import datetime, timedelta

import pytest

_seq = itertools.count()
CUTOFF = datetime(2026, 8, 1)
BEFORE = datetime(2026, 6, 15)
AFTER = datetime(2026, 8, 20)


@pytest.fixture
def make_user(flask_app):
    def _make(created=BEFORE, verified=False, todos=0, pushes=0):
        from database import db
        from models import PushSubscription, Todo, User
        with flask_app.app_context():
            u = User(email=f"purge{next(_seq)}@example.invalid",
                     password_hash="x", email_verified=verified, created_at=created)
            db.session.add(u)
            db.session.commit()
            for i in range(todos):
                db.session.add(Todo(user_id=u.id, title=f"t{i}"))
            for i in range(pushes):
                db.session.add(PushSubscription(user_id=u.id,
                                                endpoint=f"https://push/{u.id}/{i}",
                                                p256dh="p", auth="a"))
            db.session.commit()
            return u.id
    return _make


def _doomed(flask_app):
    from purge_dormant import dormant
    with flask_app.app_context():
        return {u.id for u in dormant(CUTOFF).all()}


def test_a_dormant_account_is_selected(flask_app, make_user):
    assert make_user() in _doomed(flask_app)


def test_owning_a_todo_saves_you(flask_app, make_user):
    assert make_user(todos=1) not in _doomed(flask_app)


def test_a_push_subscription_saves_you(flask_app, make_user):
    """Someone who installed the PWA but keeps an empty list still uses it."""
    assert make_user(pushes=1) not in _doomed(flask_app)


def test_signing_up_after_the_cutoff_saves_you(flask_app, make_user):
    assert make_user(created=AFTER) not in _doomed(flask_app)


def test_being_flagged_verified_does_not_save_you(flask_app, make_user):
    """326 of these were verified by a mail scanner, not a person."""
    assert make_user(verified=True) in _doomed(flask_app)


def test_the_admin_is_never_selected(flask_app):
    """Belt and braces: even if the admin somehow looked dormant."""
    from accounts import ADMIN_USER_ID
    assert ADMIN_USER_ID not in _doomed(flask_app)


def test_a_completed_todo_still_counts_as_use(flask_app, make_user):
    from database import db
    from models import Todo
    uid = make_user(todos=1)
    with flask_app.app_context():
        for t in Todo.query.filter_by(user_id=uid).all():
            t.done = True
        db.session.commit()
    assert uid not in _doomed(flask_app)


def test_deleting_a_selected_account_leaves_nothing_behind(flask_app, make_user):
    """The cascade is the same one the settings page uses; orphaned push rows
    were the previous bug."""
    from accounts import delete_user
    from database import db
    from models import EmailToken, PushSubscription, Todo, User, UserSettings

    uid = make_user()
    with flask_app.app_context():
        db.session.add(UserSettings(user_id=uid, timezone="Australia/Sydney"))
        db.session.add(EmailToken(user_id=uid, token=f"tok{uid}", purpose="verify",
                                  expires_at=datetime.utcnow() + timedelta(hours=1)))
        db.session.commit()

        delete_user(db.session.get(User, uid))
        db.session.commit()

        assert db.session.get(User, uid) is None
        for model in (Todo, PushSubscription, UserSettings, EmailToken):
            assert model.query.filter_by(user_id=uid).count() == 0, model.__name__
