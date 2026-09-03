"""Test fixtures.

The environment has to be populated before `app` is imported, because config.py
reads it at module scope and the required keys raise on absence. That is
deliberate — a missing SECRET_KEY should stop the process at boot, not on the
first request — so tests supply one rather than the code going lazy.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db")
os.close(_DB_FD)

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + _DB_PATH.replace("\\", "/"))
os.environ.setdefault("INITIAL_ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("UI_PASSWORD", "test-password-123")

API_HEADERS = {"X-API-Key": "test-api-key", "Content-Type": "application/json"}


@pytest.fixture(scope="session")
def flask_app():
    from app import app as flask_app
    flask_app.config["TESTING"] = True
    yield flask_app


@pytest.fixture
def client(flask_app):
    """A client authenticated by API key, with the todo table emptied."""
    from database import db
    from models import Todo, UserSettings
    with flask_app.app_context():
        Todo.query.delete()
        UserSettings.query.delete()
        db.session.commit()
    return flask_app.test_client()


@pytest.fixture
def api(client):
    """Thin JSON wrapper so the tests read as API calls, not plumbing."""
    import json

    class _Api:
        def get(self, path):
            return client.get(path, headers=API_HEADERS)

        def post(self, path, payload):
            return client.post(path, data=json.dumps(payload), headers=API_HEADERS)

        def patch(self, path, payload):
            return client.patch(path, data=json.dumps(payload), headers=API_HEADERS)

        def delete(self, path):
            return client.delete(path, headers=API_HEADERS)

    return _Api()


@pytest.fixture
def set_timezone(flask_app):
    """Give the API-key user (id 1) a timezone, as the browser would."""
    def _set(name):
        from database import db
        from models import UserSettings
        with flask_app.app_context():
            s = db.session.get(UserSettings, 1)
            if s is None:
                s = UserSettings(user_id=1)
                db.session.add(s)
            s.timezone = name
            db.session.commit()
    return _set
