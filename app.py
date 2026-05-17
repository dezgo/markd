import os
import re
import secrets
import sys
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.relativedelta import relativedelta
from sqlalchemy import inspect as sa_inspect, text

from flask import (
    Flask,
    abort,
    flash,
    g,
    get_flashed_messages,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from dotenv import load_dotenv
from werkzeug.security import check_password_hash, generate_password_hash

import resend

load_dotenv()

from database import db
from models import EmailToken, PushSubscription, RECURRENCE_UNITS, Todo, User, UserSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def next_due_date(base: date, interval: int, unit: str, days_csv: str = None) -> date:
    if unit == "weeks" and days_csv:
        days = {int(d) for d in days_csv.split(",") if d}
        for offset in range(1, 8):
            candidate = base + timedelta(days=offset)
            js_dow = (candidate.weekday() + 1) % 7  # Sun=0..Sat=6
            if js_dow in days:
                return candidate
        return base + timedelta(days=7)

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


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _valid_email(e: str) -> bool:
    return bool(e and _EMAIL_RE.match(e))


def parse_recurrence(data: dict):
    interval = data.get("recurrence_interval")
    unit = data.get("recurrence_unit") or None
    days = data.get("recurrence_days")

    if interval is None and unit is None and not days:
        return None, None, None, None

    if days and unit == "weeks":
        try:
            day_set = sorted({int(d) for d in str(days).split(",") if d != ""})
        except ValueError:
            return None, None, None, "recurrence_days must be comma-separated weekday numbers (0-6)"
        if not day_set or any(d < 0 or d > 6 for d in day_set):
            return None, None, None, "recurrence_days values must be 0-6 (Sun=0..Sat=6)"
        return 1, "weeks", ",".join(str(d) for d in day_set), None

    try:
        interval = int(interval)
        if interval < 1:
            raise ValueError
    except (TypeError, ValueError):
        return None, None, None, "recurrence_interval must be a positive integer"

    if unit not in RECURRENCE_UNITS:
        return None, None, None, f"recurrence_unit must be one of {sorted(RECURRENCE_UNITS)}"

    return interval, unit, None, None


# ---------------------------------------------------------------------------
# App init + DB
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]
_default_db = "sqlite:////var/www/markd/markd.db"
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", _default_db)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Persistent login: iOS kills the PWA's WebKit process aggressively, which drops
# any non-permanent (browser-session) cookie. A server-set Max-Age cookie survives.
app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(days=90),
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

db.init_app(app)


def _ensure_columns(table: str, cols: dict):
    existing = [c["name"] for c in sa_inspect(db.engine).get_columns(table)]
    with db.engine.connect() as conn:
        for col, coltype in cols.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}"))
                conn.commit()


with app.app_context():
    db.create_all()  # creates users, email_tokens, and any new tables

    _ensure_columns("todos", {
        "recurrence_interval": "INTEGER",
        "recurrence_unit":     "VARCHAR(10)",
        "recurrence_days":     "VARCHAR(15)",
        "due_time":            "VARCHAR(5)",
        "notes":               "TEXT",
        "spawned_from_id":     "INTEGER",
        "notified_at":         "DATETIME",
        "user_id":             "INTEGER",
    })
    _ensure_columns("push_subscriptions", {
        "user_id": "INTEGER",
    })

    # Initial admin: convert single-password app into multi-user. Runs once.
    if User.query.count() == 0:
        admin_email = os.environ.get("INITIAL_ADMIN_EMAIL", "").strip().lower()
        admin_password = os.environ.get("UI_PASSWORD", "")
        if admin_email and admin_password:
            admin = User(
                email=admin_email,
                password_hash=generate_password_hash(admin_password),
                email_verified=True,
            )
            db.session.add(admin)
            db.session.commit()
            with db.engine.connect() as conn:
                conn.execute(text(f"UPDATE todos SET user_id = {admin.id} WHERE user_id IS NULL"))
                conn.execute(text(f"UPDATE push_subscriptions SET user_id = {admin.id} WHERE user_id IS NULL"))
                conn.commit()
            print(f"Created initial admin user: {admin_email}", file=sys.stderr, flush=True)
        else:
            print(
                "WARNING: no users exist and INITIAL_ADMIN_EMAIL/UI_PASSWORD not set — "
                "no initial admin created. Sign up via /signup.",
                file=sys.stderr, flush=True,
            )


# ---------------------------------------------------------------------------
# Env config
# ---------------------------------------------------------------------------

API_KEY = os.environ["API_KEY"]
APP_URL = os.environ.get("APP_URL", "https://markd.appfoundry.cc").rstrip("/")

VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_CONTACT = os.environ.get("VAPID_CONTACT", "mailto:admin@example.com")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "Markd <markd@appfoundry.cc>")
if RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY

if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
    print("WARNING: VAPID keys not configured — push notifications disabled.", file=sys.stderr, flush=True)
if not RESEND_API_KEY:
    print("WARNING: RESEND_API_KEY not set — email verification and password reset disabled.", file=sys.stderr, flush=True)

NOTIFICATIONS_LOG = "/var/log/markd/notifications.log"


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def current_user_id():
    if "_user_id" in g:
        return g._user_id
    return session.get("user_id")


def current_user():
    uid = current_user_id()
    if uid:
        return db.session.get(User, uid)
    return None


def require_session(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


def require_api_key(f):
    """Accept either a logged-in session or the API key header.

    API key requests act as the initial admin (user_id=1) for backwards compat.
    Per-user API keys can come later.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("user_id"):
            return f(*args, **kwargs)
        key = request.headers.get("X-API-Key") or request.args.get("api_key")
        if key != API_KEY:
            abort(401)
        g._user_id = 1
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------

def _send_email(to: str, subject: str, html: str):
    if not RESEND_API_KEY:
        print(f"(would send email to {to}: {subject})", file=sys.stderr, flush=True)
        return False
    try:
        resend.Emails.send({"from": EMAIL_FROM, "to": [to], "subject": subject, "html": html})
        return True
    except Exception as e:
        print(f"Email send failed for {to}: {e}", file=sys.stderr, flush=True)
        return False


def make_token(user_id: int, purpose: str, hours: int) -> str:
    token = secrets.token_urlsafe(32)
    db.session.add(EmailToken(
        user_id=user_id,
        token=token,
        purpose=purpose,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=hours),
    ))
    db.session.commit()
    return token


def consume_token(token: str, purpose: str) -> User:
    """Return the user if the token is valid and unused, else None. Marks it used."""
    rec = EmailToken.query.filter_by(token=token, purpose=purpose).first()
    if not rec or rec.used_at is not None:
        return None
    if rec.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        return None
    rec.used_at = datetime.now(timezone.utc)
    user = db.session.get(User, rec.user_id)
    db.session.commit()
    return user


def _email_layout(preview: str, heading: str, intro: str, button_label: str,
                  button_url: str, expiry_note: str, extra_note: str = "") -> str:
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f4f5fb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#111827;">
<span style="display:none;font-size:0;line-height:0;max-height:0;max-width:0;opacity:0;overflow:hidden;">{preview}</span>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f4f5fb;padding:40px 16px;">
  <tr><td align="center">
    <table role="presentation" width="520" cellspacing="0" cellpadding="0" border="0" style="background:#ffffff;border-radius:14px;box-shadow:0 4px 20px rgba(99,102,241,0.08);overflow:hidden;max-width:520px;width:100%;">
      <tr><td style="padding:32px 32px 0;text-align:center;">
        <span style="display:inline-block;font-size:26px;font-weight:700;color:#6366f1;letter-spacing:-0.5px;">Markd</span>
      </td></tr>
      <tr><td style="padding:24px 36px 36px;">
        <h1 style="margin:0 0 12px;font-size:20px;font-weight:600;color:#111827;text-align:center;">{heading}</h1>
        <p style="margin:0 0 28px;color:#6b7280;font-size:15px;line-height:1.55;text-align:center;">{intro}</p>
        <p style="text-align:center;margin:0 0 28px;">
          <a href="{button_url}" style="display:inline-block;background:#6366f1;color:#ffffff;text-decoration:none;padding:13px 32px;border-radius:10px;font-size:15px;font-weight:600;">{button_label}</a>
        </p>
        <p style="margin:0 0 6px;color:#9ca3af;font-size:12px;text-align:center;">Or paste this link:</p>
        <p style="margin:0 0 18px;font-size:12px;word-break:break-all;text-align:center;"><a href="{button_url}" style="color:#6366f1;text-decoration:none;">{button_url}</a></p>
        <p style="margin:0;color:#9ca3af;font-size:12px;text-align:center;line-height:1.5;">{expiry_note}{(' ' + extra_note) if extra_note else ''}</p>
      </td></tr>
    </table>
    <p style="margin:16px 0 0;color:#9ca3af;font-size:11px;text-align:center;">Markd · markd.appfoundry.cc</p>
  </td></tr>
</table>
</body>
</html>"""


def send_verification_email(user: User):
    token = make_token(user.id, "verify", hours=24)
    url = f"{APP_URL}/verify/{token}"
    html = _email_layout(
        preview="Verify your email to start using Markd.",
        heading="Verify your email",
        intro="Click the button below to confirm your email and finish setting up your Markd account.",
        button_label="Verify my email",
        button_url=url,
        expiry_note="This link expires in 24 hours.",
    )
    return _send_email(user.email, "Verify your Markd email", html)


def send_reset_email(user: User):
    token = make_token(user.id, "reset", hours=1)
    url = f"{APP_URL}/reset-password/{token}"
    html = _email_layout(
        preview="Reset your Markd password.",
        heading="Reset your password",
        intro="Someone (hopefully you) requested a password reset for your Markd account. Click below to set a new one.",
        button_label="Set new password",
        button_url=url,
        expiry_note="This link expires in 30 minutes.",
        extra_note="If you didn't request this, just ignore the email.",
    )
    return _send_email(user.email, "Reset your Markd password", html)


# ---------------------------------------------------------------------------
# Static / SW
# ---------------------------------------------------------------------------

# Bumped on every release. Sole source of truth — stamped into app.js and sw.js
# at server startup (see _versioned below) and exposed via /version for the
# client-side staleness check.
APP_VERSION = "v41"


def _versioned(filename: str) -> str:
    with open(os.path.join(app.static_folder, filename)) as f:
        return f.read().replace("__APP_VERSION__", APP_VERSION)


_APP_JS = _versioned("app.js")
_SW_JS = _versioned("sw.js")


def _no_cache_js(body: str):
    resp = make_response(body)
    resp.headers["Content-Type"] = "application/javascript"
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(app.static_folder, "favicon.ico")


@app.route("/sw.js")
def service_worker():
    return _no_cache_js(_SW_JS)


@app.route("/app.js")
def app_js():
    return _no_cache_js(_APP_JS)


@app.route("/version")
def version():
    resp = jsonify({"version": APP_VERSION})
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


# ---------------------------------------------------------------------------
# Auth UI routes
# ---------------------------------------------------------------------------

@app.route("/signup", methods=["GET", "POST"])
def signup():
    error = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if not _valid_email(email):
            error = "Please enter a valid email address."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif User.query.filter_by(email=email).first():
            error = "An account with that email already exists."
        else:
            user = User(
                email=email,
                password_hash=generate_password_hash(password),
                email_verified=False,
            )
            db.session.add(user)
            db.session.commit()
            send_verification_email(user)
            return render_template("verify_pending.html", email=email)
    return render_template("signup.html", error=error)


@app.route("/verify/<token>")
def verify_email(token):
    user = consume_token(token, "verify")
    if not user:
        return render_template("auth_message.html", title="Link invalid",
                               message="This verification link is invalid or has expired.",
                               link_text="Sign up again", link_href=url_for("signup"))
    user.email_verified = True
    db.session.commit()
    flash("Email verified. You can now log in.")
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    next_url = request.args.get("next") or request.form.get("next") or url_for("app_home")
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            if not user.email_verified:
                error = "Please verify your email first. Check your inbox for the link."
            else:
                session.permanent = True
                session["user_id"] = user.id
                return redirect(next_url)
        else:
            error = "Incorrect email or password."
    flashed = get_flashed_messages()
    return render_template("login.html", error=error, flashed=flashed, next_url=next_url)


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    sent = False
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user:
            send_reset_email(user)
        sent = True  # show same message regardless, to avoid revealing account existence
    return render_template("forgot.html", sent=sent)


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    rec = EmailToken.query.filter_by(token=token, purpose="reset").first()
    valid = rec and rec.used_at is None and rec.expires_at.replace(tzinfo=timezone.utc) >= datetime.now(timezone.utc)
    if not valid:
        return render_template("auth_message.html", title="Link invalid",
                               message="This reset link is invalid or has expired.",
                               link_text="Request a new one", link_href=url_for("forgot_password"))
    error = None
    if request.method == "POST":
        password = request.form.get("password") or ""
        if len(password) < 8:
            error = "Password must be at least 8 characters."
        else:
            user = consume_token(token, "reset")
            if user:
                user.password_hash = generate_password_hash(password)
                db.session.commit()
                flash("Password updated. You can now log in.")
                return redirect(url_for("login"))
            error = "This link is no longer valid."
    return render_template("reset.html", error=error, token=token)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def landing():
    if session.get("user_id"):
        return redirect(url_for("app_home"))
    return render_template("landing.html")


@app.route("/app")
@require_session
def app_home():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Todo API
# ---------------------------------------------------------------------------

@app.route("/todos", methods=["GET"])
@require_api_key
def get_todos():
    todos = Todo.query.filter_by(user_id=current_user_id()).order_by(Todo.created_at.desc()).all()
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

    interval, unit, days_csv, err = parse_recurrence(data)
    if err:
        return jsonify({"error": err}), 400

    todo = Todo(
        user_id=current_user_id(),
        title=title, due_date=due_date, due_time=due_time,
        notes=notes, recurrence_interval=interval, recurrence_unit=unit,
        recurrence_days=days_csv,
    )
    db.session.add(todo)
    db.session.commit()
    return jsonify(todo.to_dict()), 201


@app.route("/todos/<int:todo_id>", methods=["PATCH"])
@require_api_key
def update_todo(todo_id):
    todo = Todo.query.filter_by(id=todo_id, user_id=current_user_id()).first()
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
            base = todo.due_date or date.today()
            today = date.today()
            nxt = next_due_date(base, todo.recurrence_interval, todo.recurrence_unit, todo.recurrence_days)
            # Fast-forward past any overdue occurrences so the spawned instance is in the future.
            while nxt <= today:
                nxt = next_due_date(nxt, todo.recurrence_interval, todo.recurrence_unit, todo.recurrence_days)
            db.session.add(Todo(
                user_id=todo.user_id,
                title=todo.title,
                notes=todo.notes,
                due_time=todo.due_time,
                recurrence_interval=todo.recurrence_interval,
                recurrence_unit=todo.recurrence_unit,
                recurrence_days=todo.recurrence_days,
                due_date=nxt,
                spawned_from_id=todo.id,
            ))
        elif not new_done and todo.done and todo.recurrence_interval:
            child = Todo.query.filter_by(spawned_from_id=todo.id, done=False, user_id=current_user_id()).first()
            if child:
                db.session.delete(child)
        todo.done = new_done

    if "recurrence_interval" in data or "recurrence_unit" in data or "recurrence_days" in data:
        interval, unit, days_csv, err = parse_recurrence(data)
        if err:
            return jsonify({"error": err}), 400
        todo.recurrence_interval = interval
        todo.recurrence_unit = unit
        todo.recurrence_days = days_csv

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
    todo = Todo.query.filter_by(id=todo_id, user_id=current_user_id()).first()
    if todo is None:
        abort(404)
    db.session.delete(todo)
    db.session.commit()
    return "", 204


# ---------------------------------------------------------------------------
# Push notifications
# ---------------------------------------------------------------------------

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
        sub.user_id = current_user_id()
        sub.p256dh = p256dh
        sub.auth = auth
    else:
        db.session.add(PushSubscription(
            user_id=current_user_id(),
            endpoint=endpoint, p256dh=p256dh, auth=auth,
        ))
    db.session.commit()
    return "", 204


@app.route("/push/subscribe", methods=["DELETE"])
@require_session
def push_unsubscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")
    if endpoint:
        PushSubscription.query.filter_by(endpoint=endpoint, user_id=current_user_id()).delete()
        db.session.commit()
    return "", 204


# ---------------------------------------------------------------------------
# User settings (incl. daily overdue check)
# ---------------------------------------------------------------------------

def _get_or_create_settings(user_id: int) -> UserSettings:
    s = db.session.get(UserSettings, user_id)
    if s is None:
        s = UserSettings(user_id=user_id)
        db.session.add(s)
        db.session.commit()
    return s


@app.route("/settings")
@require_session
def settings_page():
    s = _get_or_create_settings(current_user_id())
    return render_template("settings.html", settings=s)


@app.route("/api/settings", methods=["GET"])
@require_session
def get_settings():
    s = _get_or_create_settings(current_user_id())
    return jsonify(s.to_dict())


@app.route("/api/settings", methods=["PATCH"])
@require_session
def update_settings():
    data = request.get_json(silent=True) or {}
    s = _get_or_create_settings(current_user_id())

    if "overdue_check_enabled" in data:
        s.overdue_check_enabled = bool(data["overdue_check_enabled"])

    if "overdue_check_time" in data:
        t = data["overdue_check_time"]
        if not isinstance(t, str) or not _valid_time(t):
            return jsonify({"error": "overdue_check_time must be HH:MM"}), 400
        s.overdue_check_time = t

    if "timezone" in data:
        tz = data["timezone"]
        if not isinstance(tz, str) or not tz:
            return jsonify({"error": "timezone must be a non-empty IANA name"}), 400
        try:
            ZoneInfo(tz)
        except ZoneInfoNotFoundError:
            return jsonify({"error": f"unknown timezone: {tz}"}), 400
        s.timezone = tz

    db.session.commit()
    return jsonify(s.to_dict())


# ---------------------------------------------------------------------------
# Diagnostics (per-user)
# ---------------------------------------------------------------------------

@app.route("/diagnostics")
@require_session
def diagnostics():
    log_tail = []
    try:
        with open(NOTIFICATIONS_LOG) as f:
            log_tail = [line.rstrip() for line in f.readlines()[-30:]]
    except FileNotFoundError:
        log_tail = ["<log file not found — has the cron run yet?>"]
    except PermissionError:
        log_tail = ["<permission denied reading log>"]

    uid = current_user_id()
    pending = Todo.query.filter(
        Todo.user_id == uid,
        Todo.done == False,
        Todo.notified_at == None,
        Todo.due_date != None,
        Todo.due_time != None,
    ).count()

    info = {
        "user_email": current_user().email if current_user() else "(none)",
        "user_count": User.query.count(),
        "vapid_configured": bool(VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY),
        "vapid_public_key_preview": (VAPID_PUBLIC_KEY[:30] + "…") if VAPID_PUBLIC_KEY else "(not set)",
        "vapid_contact": VAPID_CONTACT,
        "email_configured": bool(RESEND_API_KEY),
        "subscription_count": PushSubscription.query.filter_by(user_id=uid).count(),
        "todos_total": Todo.query.filter_by(user_id=uid).count(),
        "todos_active": Todo.query.filter_by(user_id=uid, done=False).count(),
        "todos_pending_notification": pending,
        "server_time_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "log_tail": log_tail,
    }
    return render_template("diagnostics.html", info=info)


if __name__ == "__main__":
    app.run(debug=True)
