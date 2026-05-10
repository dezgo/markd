import os
from datetime import date, datetime, timezone, timedelta
from functools import wraps

from dateutil.relativedelta import relativedelta
from sqlalchemy import text, inspect as sa_inspect

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from dotenv import load_dotenv

load_dotenv()

from database import db
from models import Todo, PushSubscription, RECURRENCE_UNITS


def next_due_date(base: date, interval: int, unit: str) -> date:
    if unit == "days":
        return base + timedelta(days=interval)
    if unit == "weeks":
        return base + timedelta(weeks=interval)
    if unit == "months":
        return base + relativedelta(months=interval)
    if unit == "years":
        return base + relativedelta(years=interval)
    return base


def _valid_time(t: str) -> bool:
    try:
        h, m = t.split(":")
        return 0 <= int(h) <= 23 and 0 <= int(m) <= 59
    except Exception:
        return False


def parse_recurrence(data: dict):
    """Return (interval, unit) or (None, None). Validates and returns an error string on failure."""
    interval = data.get("recurrence_interval")
    unit = data.get("recurrence_unit") or None

    if interval is None and unit is None:
        return None, None, None

    try:
        interval = int(interval)
        if interval < 1:
            raise ValueError
    except (TypeError, ValueError):
        return None, None, "recurrence_interval must be a positive integer"

    if unit not in RECURRENCE_UNITS:
        return None, None, f"recurrence_unit must be one of {sorted(RECURRENCE_UNITS)}"

    return interval, unit, None


app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]
_default_db = "sqlite:////var/www/markd/markd.db"
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", _default_db)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

with app.app_context():
    db.create_all()
    # Migrate: add new columns if upgrading from old single-column schema
    existing = [c["name"] for c in sa_inspect(db.engine).get_columns("todos")]
    new_cols = {
        "recurrence_interval": "INTEGER",
        "recurrence_unit":     "VARCHAR(10)",
        "due_time":            "VARCHAR(5)",
        "notes":               "TEXT",
        "spawned_from_id":     "INTEGER",
        "notified_at":         "DATETIME",
    }
    with db.engine.connect() as conn:
        for col, coltype in new_cols.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE todos ADD COLUMN {col} {coltype}"))
                conn.commit()

UI_PASSWORD = os.environ["UI_PASSWORD"]
API_KEY = os.environ["API_KEY"]


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def require_api_key(f):
    """Accept either a valid browser session or the API key header."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("logged_in"):
            return f(*args, **kwargs)
        key = request.headers.get("X-API-Key") or request.args.get("api_key")
        if key != API_KEY:
            abort(401)
        return f(*args, **kwargs)
    return decorated


def require_session(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# UI routes
# ---------------------------------------------------------------------------

@app.route("/favicon.ico")
def favicon():
    return send_from_directory(app.static_folder, "favicon.ico")


@app.route("/sw.js")
def service_worker():
    resp = send_from_directory(app.static_folder, "sw.js")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == UI_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
        error = "Incorrect password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@require_session
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

@app.route("/todos", methods=["GET"])
@require_api_key
def get_todos():
    todos = Todo.query.order_by(Todo.created_at.desc()).all()
    return jsonify([t.to_dict() for t in todos])


@app.route("/todos", methods=["POST"])
@require_api_key
def create_todo():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400

    due_date = None
    if data.get("due_date"):
        try:
            due_date = date.fromisoformat(data["due_date"])
        except ValueError:
            return jsonify({"error": "due_date must be YYYY-MM-DD"}), 400

    due_time = data.get("due_time") or None
    if due_time and not _valid_time(due_time):
        return jsonify({"error": "due_time must be HH:MM"}), 400

    notes = data.get("notes") or None

    interval, unit, err = parse_recurrence(data)
    if err:
        return jsonify({"error": err}), 400

    todo = Todo(
        title=title, due_date=due_date, due_time=due_time,
        notes=notes, recurrence_interval=interval, recurrence_unit=unit,
    )
    db.session.add(todo)
    db.session.commit()
    return jsonify(todo.to_dict()), 201


@app.route("/todos/<int:todo_id>", methods=["PATCH"])
@require_api_key
def update_todo(todo_id):
    todo = db.session.get(Todo, todo_id)
    if todo is None:
        abort(404)

    data = request.get_json(silent=True) or {}

    if "title" in data:
        title = data["title"].strip()
        if not title:
            return jsonify({"error": "title cannot be empty"}), 400
        todo.title = title

    if "done" in data:
        new_done = bool(data["done"])
        if new_done and not todo.done and todo.recurrence_interval and todo.recurrence_unit:
            # Completing a recurring task — spawn the next instance
            base = todo.due_date or date.today()
            db.session.add(Todo(
                title=todo.title,
                notes=todo.notes,
                due_time=todo.due_time,
                recurrence_interval=todo.recurrence_interval,
                recurrence_unit=todo.recurrence_unit,
                due_date=next_due_date(base, todo.recurrence_interval, todo.recurrence_unit),
                spawned_from_id=todo.id,
            ))
        elif not new_done and todo.done and todo.recurrence_interval:
            # Un-completing — delete the spawned child if it hasn't been completed yet
            child = Todo.query.filter_by(spawned_from_id=todo.id, done=False).first()
            if child:
                db.session.delete(child)
        todo.done = new_done

    if "recurrence_interval" in data or "recurrence_unit" in data:
        interval, unit, err = parse_recurrence(data)
        if err:
            return jsonify({"error": err}), 400
        todo.recurrence_interval = interval
        todo.recurrence_unit = unit

    if "due_time" in data:
        due_time = data["due_time"] or None
        if due_time and not _valid_time(due_time):
            return jsonify({"error": "due_time must be HH:MM"}), 400
        todo.due_time = due_time

    if "notes" in data:
        todo.notes = data["notes"] or None

    if "due_date" in data:
        if data["due_date"] is None:
            todo.due_date = None
        else:
            try:
                todo.due_date = date.fromisoformat(data["due_date"])
            except ValueError:
                return jsonify({"error": "due_date must be YYYY-MM-DD"}), 400

    todo.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(todo.to_dict())


@app.route("/todos/<int:todo_id>", methods=["DELETE"])
@require_api_key
def delete_todo(todo_id):
    todo = db.session.get(Todo, todo_id)
    if todo is None:
        abort(404)
    db.session.delete(todo)
    db.session.commit()
    return "", 204


# ---------------------------------------------------------------------------
# Push notifications
# ---------------------------------------------------------------------------

VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_CONTACT = os.environ.get("VAPID_CONTACT", "mailto:admin@example.com")


@app.route("/push/vapid-public-key")
@require_session
def push_vapid_key():
    return jsonify({"publicKey": VAPID_PUBLIC_KEY})


@app.route("/push/subscribe", methods=["POST"])
@require_session
def push_subscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")
    keys = data.get("keys") or {}
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    if not endpoint or not p256dh or not auth:
        return jsonify({"error": "endpoint, keys.p256dh, and keys.auth are required"}), 400

    sub = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if sub:
        sub.p256dh = p256dh
        sub.auth = auth
    else:
        db.session.add(PushSubscription(endpoint=endpoint, p256dh=p256dh, auth=auth))
    db.session.commit()
    return "", 204


@app.route("/push/subscribe", methods=["DELETE"])
@require_session
def push_unsubscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")
    if endpoint:
        PushSubscription.query.filter_by(endpoint=endpoint).delete()
        db.session.commit()
    return "", 204


if __name__ == "__main__":
    app.run(debug=True)
