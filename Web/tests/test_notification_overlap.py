"""A task due in the same minute as the overdue nag must ping once, not twice.

Both cron senders run every minute in no fixed order, so each order is tested.
Delivery is faked: what matters is how many pushes each user was sent.
"""

import importlib
import json
from datetime import date, datetime

import pytest

# 07:00 in Sydney (AEDT) on 2 Nov 2026, a couple of seconds into the minute.
NOW = datetime(2026, 11, 1, 20, 0, 3)
DUE = datetime(2026, 11, 1, 20, 0, 0)


@pytest.fixture
def senders(client, flask_app, monkeypatch):
    import config
    monkeypatch.setattr(config, "PUSH_CONFIGURED", True)
    due = importlib.import_module("send_notifications")
    nag = importlib.import_module("send_overdue_check")

    import scheduling
    monkeypatch.setattr(scheduling, "now_utc", lambda: NOW)

    sent = []

    def fake_send(user_id, payload, **kw):
        sent.append((user_id, json.loads(payload)))
        return 1, 1

    monkeypatch.setattr(due, "send_to_user", fake_send)
    monkeypatch.setattr(nag, "send_to_user", fake_send)
    return due, nag, sent


def _setup(flask_app, nag_time="07:00"):
    from database import db
    from models import Todo, User, UserSettings
    with flask_app.app_context():
        user = User.query.filter_by(email="overlap@example.com").first()
        if user is None:
            user = User(email="overlap@example.com", password_hash="x")
            db.session.add(user)
            db.session.flush()
        db.session.add(UserSettings(user_id=user.id, timezone="Australia/Sydney",
                                    overdue_check_time=nag_time))
        db.session.add(Todo(user_id=user.id, title="Upload receipts",
                            due_on=date(2026, 10, 31)))
        db.session.add(Todo(user_id=user.id, title="Strength day", due_at=DUE))
        db.session.commit()
        return user.id


@pytest.mark.parametrize("order", ["due_first", "nag_first"])
def test_one_push_when_due_time_meets_nag(senders, flask_app, order):
    due, nag, sent = senders
    _setup(flask_app)
    first, second = (due, nag) if order == "due_first" else (nag, due)
    first.run()
    second.run()

    assert len(sent) == 1
    assert sent[0][1]["title"] == "2 overdue tasks"
    assert "Strength day" in sent[0][1]["body"]

    # And it stays covered: the next minute does not push it on its own.
    import scheduling
    scheduling.now_utc = lambda: datetime(2026, 11, 1, 20, 1, 3)
    due.run()
    assert len(sent) == 1


def test_task_due_at_another_time_still_pings_alone(senders, flask_app):
    due, nag, sent = senders
    _setup(flask_app, nag_time="06:30")
    due.run()
    nag.run()
    assert len(sent) == 1
    assert sent[0][1] == {"title": "Strength day", "body": "Due now",
                          "tag": sent[0][1]["tag"]}
