"""Markd — Flask app factory.

Routes live in routes/, domain logic in recurrence.py / scheduling.py /
push.py / mail.py, and configuration in config.py. What is left here is the
wiring: build the app, run the schema bootstrap, register the blueprints.

The module-level `app` is created at import time because gunicorn is pointed
at `app:app` and the cron scripts do `from app import app`.
"""

import os
import sys
from datetime import datetime, timedelta

from flask import Flask
from sqlalchemy import inspect as sa_inspect, text
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash

import antispam
import config
import routes
from database import db
from models import SchemaMigration, Todo, User
from scheduling import parse_hhmm

# Bumped on every release. Sole source of truth — stamped into app.js and sw.js
# when the app starts and exposed via /version for the client-side staleness
# check. deploy.sh greps this line to confirm a deploy actually took, so it
# stays a plain literal in this file.
APP_VERSION = "v68"




def _ensure_columns(table: str, cols: dict):
    existing = [c["name"] for c in sa_inspect(db.engine).get_columns(table)]
    with db.engine.connect() as conn:
        for col, coltype in cols.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}"))
                conn.commit()


def _run_once(name: str, migrate):
    """Run a data migration the first time only, recording that it ran.

    Adding a column is idempotent; a backfill is not. This one reads the legacy
    due_date/due_time pair, so re-running it after the user cleared a due date
    would put it back.
    """
    if db.session.get(SchemaMigration, name) is not None:
        return
    count = migrate()
    db.session.add(SchemaMigration(name=name))
    db.session.commit()
    print(f"migration {name}: applied to {count} row(s)", file=sys.stderr, flush=True)


def _backfill_due_columns() -> int:
    """Split the legacy due_date/due_time pair into due_at / due_on.

    A row with a time was always a UTC instant, so it becomes due_at directly.
    A row without one was a plain local date, so it becomes due_on. The legacy
    columns are left untouched, which is what makes rolling back to the
    previous release safe.
    """
    todos = Todo.query.filter(
        Todo.due_at.is_(None), Todo.due_on.is_(None), Todo.due_date.isnot(None),
    ).all()
    for todo in todos:
        t = parse_hhmm(todo.due_time)
        if t is None:
            todo.due_on = todo.due_date
        else:
            todo.due_at = datetime.combine(todo.due_date, t)
    return len(todos)


def _bootstrap_schema():
    db.create_all()  # creates users, email_tokens, and any new tables

    _ensure_columns("todos", {
        "recurrence_interval": "INTEGER",
        "recurrence_unit":     "VARCHAR(20)",
        "recurrence_days":     "VARCHAR(15)",
        "due_time":            "VARCHAR(5)",
        "due_at":              "DATETIME",
        "due_on":              "DATE",
        "notes":               "TEXT",
        "spawned_from_id":     "INTEGER",
        "notified_at":         "DATETIME",
        "user_id":             "INTEGER",
    })
    _ensure_columns("push_subscriptions", {
        "user_id": "INTEGER",
    })
    _ensure_columns("user_settings", {
        "theme": "VARCHAR(16) NOT NULL DEFAULT 'indigo'",
    })

    _run_once("2026_09_due_at_due_on", _backfill_due_columns)

    # Initial admin: convert single-password app into multi-user. Runs once.
    if User.query.count() == 0:
        admin_email = config.INITIAL_ADMIN_EMAIL
        admin_password = config.INITIAL_ADMIN_PASSWORD
        if admin_email and admin_password:
            admin = User(
                email=admin_email,
                password_hash=generate_password_hash(admin_password),
                email_verified=True,
            )
            db.session.add(admin)
            db.session.commit()
            with db.engine.connect() as conn:
                conn.execute(text("UPDATE todos SET user_id = :uid WHERE user_id IS NULL"),
                             {"uid": admin.id})
                conn.execute(text("UPDATE push_subscriptions SET user_id = :uid WHERE user_id IS NULL"),
                             {"uid": admin.id})
                conn.commit()
            print(f"Created initial admin user: {admin_email}", file=sys.stderr, flush=True)
        else:
            print(
                "WARNING: no users exist and INITIAL_ADMIN_EMAIL/UI_PASSWORD not set — "
                "no initial admin created. Sign up via /signup.",
                file=sys.stderr, flush=True,
            )


def _stamp_version(app, filename: str) -> str:
    """Read a static bundle with the release version substituted in."""
    with open(os.path.join(app.static_folder, filename)) as f:
        return f.read().replace("__APP_VERSION__", APP_VERSION)


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY

    # nginx proxies over a unix socket, so without this every request looks like
    # it came from the same place and per-IP rate limiting silently does nothing.
    # One proxy hop (nginx) — do not raise these counts unless a real proxy is
    # added in front, or clients can spoof X-Forwarded-For and evade the limits.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    app.config["SQLALCHEMY_DATABASE_URI"] = config.DATABASE_URL
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Persistent login: iOS kills the PWA's WebKit process aggressively, which
    # drops any non-permanent (browser-session) cookie. A server-set Max-Age
    # cookie survives.
    app.config.update(
        PERMANENT_SESSION_LIFETIME=timedelta(days=90),
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

    db.init_app(app)
    with app.app_context():
        _bootstrap_schema()

    app.config["APP_VERSION"] = APP_VERSION
    app.config["APP_JS"] = _stamp_version(app, "app.js")
    app.config["SW_JS"] = _stamp_version(app, "sw.js")

    routes.register(app)

    if not config.QUIET_STARTUP:
        for message in config.startup_warnings():
            config.warn(message)
        if not antispam.is_turnstile_enabled():
            config.warn("Turnstile keys not set — signup CAPTCHA disabled "
                        "(other defences still active).")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
