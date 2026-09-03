import base64
import hashlib
import hmac
import json
import os
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
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import resend

load_dotenv()

from database import db
from models import (
    EmailToken, PushSubscription, RECURRENCE_UNITS, SuppressedEmail, Todo, User, UserSettings,
)
import antispam


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _nth_last_weekday_of_month(year: int, month: int, weekday_py: int, n: int) -> date:
    """Return the date in (year, month) that is the nth-to-last occurrence of weekday_py.
    weekday_py uses Python convention (Mon=0..Sun=6). n=1 means last."""
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() != weekday_py:
        d -= timedelta(days=1)
    d -= timedelta(days=7 * (n - 1))
    return d


def _weekday_set(days_csv: str) -> set:
    """CSV of JS weekday numbers (Sun=0..Sat=6) -> set of ints."""
    return {int(d) for d in days_csv.split(",") if d}


def _js_weekday(d: date) -> int:
    """Python weekday (Mon=0..Sun=6) -> JS weekday (Sun=0..Sat=6)."""
    return (d.weekday() + 1) % 7


def first_due_date(base: date, days_csv: str) -> date:
    """First date on or after `base` falling on one of the CSV weekdays.

    Used to seed a start date for a "on set weekdays" task the user created
    without picking one; without a due_date such a task never surfaces in a
    day group and never notifies.
    """
    days = _weekday_set(days_csv)
    for offset in range(0, 7):
        candidate = base + timedelta(days=offset)
        if _js_weekday(candidate) in days:
            return candidate
    return base


def next_due_date(base: date, interval: int, unit: str, days_csv: str = None) -> date:
    if unit in ("monthly-last", "monthly-2last") and days_csv:
        js_weekday = int(days_csv.split(",")[0])
        py_weekday = (js_weekday - 1) % 7  # JS Sun=0 -> Py Sun=6; JS Mon=1 -> Py Mon=0
        n = 1 if unit == "monthly-last" else 2
        y, m = base.year, base.month
        candidate = _nth_last_weekday_of_month(y, m, py_weekday, n)
        if candidate <= base:
            if m == 12:
                y, m = y + 1, 1
            else:
                m += 1
            candidate = _nth_last_weekday_of_month(y, m, py_weekday, n)
        return candidate

    if unit == "weeks" and days_csv:
        days = _weekday_set(days_csv)
        for offset in range(1, 8):
            candidate = base + timedelta(days=offset)
            if _js_weekday(candidate) in days:
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


def parse_recurrence(data: dict):
    interval = data.get("recurrence_interval")
    unit = data.get("recurrence_unit") or None
    days = data.get("recurrence_days")

    if interval is None and unit is None and not days:
        return None, None, None, None

    if unit in ("monthly-last", "monthly-2last"):
        if not days:
            return None, None, None, f"recurrence_days (single weekday) is required for {unit}"
        try:
            day_list = [int(d) for d in str(days).split(",") if d != ""]
        except ValueError:
            return None, None, None, "recurrence_days must be a weekday number 0-6"
        if len(day_list) != 1 or not 0 <= day_list[0] <= 6:
            return None, None, None, "recurrence_days must be a single weekday number 0-6"
        return None, unit, str(day_list[0]), None

    if unit == "weeks" and days is not None and not str(days).strip():
        # Distinguish "weekday recurrence with nothing picked" from "every N
        # weeks"; otherwise this falls through to a misleading interval error.
        return None, None, None, "recurrence_days must name at least one weekday (0-6)"

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

# nginx proxies over a unix socket, so without this every request looks like it
# came from the same place and per-IP rate limiting silently does nothing.
# One proxy hop (nginx) — do not raise these counts unless a real proxy is added
# in front, or clients can spoof X-Forwarded-For and evade the limits.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

_default_db = "sqlite:///" + os.path.join(os.path.dirname(os.path.abspath(__file__)), "markd.db")
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
        "recurrence_unit":     "VARCHAR(20)",
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
    _ensure_columns("user_settings", {
        "theme": "VARCHAR(16) NOT NULL DEFAULT 'indigo'",
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
# Transactional mail sends from a dedicated subdomain so app-mail reputation is
# insulated from the root domain. See deploy/README.md for the DNS records.
EMAIL_FROM = os.environ.get("EMAIL_FROM", "Markd <markd@mail.appfoundry.cc>")
RESEND_WEBHOOK_SECRET = os.environ.get("RESEND_WEBHOOK_SECRET", "")
if RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY

if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
    print("WARNING: VAPID keys not configured — push notifications disabled.", file=sys.stderr, flush=True)
if not RESEND_API_KEY:
    print("WARNING: RESEND_API_KEY not set — email verification and password reset disabled.", file=sys.stderr, flush=True)
if not RESEND_WEBHOOK_SECRET:
    print("WARNING: RESEND_WEBHOOK_SECRET not set — bounce/complaint suppression disabled.", file=sys.stderr, flush=True)
if not antispam.is_turnstile_enabled():
    print("WARNING: Turnstile keys not set — signup CAPTCHA disabled (other defences still active).", file=sys.stderr, flush=True)

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
    """Single choke point for outbound mail.

    Every send passes the suppression list and the global hourly cap, so no code
    path — present or future — can mail a known-bad address or flood the domain.
    """
    if not RESEND_API_KEY:
        print(f"(would send email to {to}: {subject})", file=sys.stderr, flush=True)
        return False

    if antispam.is_suppressed(to):
        print(f"Suppressed address, not sending to {to}: {subject}", file=sys.stderr, flush=True)
        return False

    if not antispam.global_mail_rate_ok():
        print(
            f"GLOBAL MAIL CAP HIT — dropping send to {to} ({subject}). "
            "Something is generating mail in bulk; check /diagnostics.",
            file=sys.stderr, flush=True,
        )
        return False

    try:
        resend.Emails.send({"from": EMAIL_FROM, "to": [to], "subject": subject, "html": html})
        antispam.record_event("mail:global")
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
APP_VERSION = "v59"

THEMES = {"indigo", "mint", "sunset", "berry", "slate"}


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

def _log_blocked(stage: str, ip: str, email: str, detail: str = ""):
    """One line per rejected signup, so the logs show whether the gate is doing
    anything and which layer is carrying the load."""
    suffix = f" ({detail})" if detail else ""
    print(f"signup blocked [{stage}] ip={ip} email={email}{suffix}", file=sys.stderr, flush=True)


def _signup_form(error=None):
    """Every render mints a fresh timing token — a stale one fails the trap."""
    return render_template(
        "signup.html",
        error=error,
        form_token=antispam.issue_form_token(),
        honeypot_field=antispam.HONEYPOT_FIELD,
        turnstile_site_key=antispam.TURNSTILE_SITE_KEY,
    )


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "GET":
        return _signup_form()

    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    ip = antispam.client_ip()

    # Layer 1. Silent rejection: bots get the same "check your inbox" page a
    # real signup gets, so they can't tell a block from a success and tune
    # against it. No user row, no email.
    if not antispam.form_looks_human(request.form):
        _log_blocked("trap", ip, email)
        return render_template("verify_pending.html", email=email, resent=False)

    # Layer 2a. Anti-hammering, loose enough to survive a few typos.
    if not antispam.signup_attempt_rate_ok(ip):
        _log_blocked("attempt-rate", ip, email)
        return _signup_form("Too many attempts. Please try again later.")
    antispam.record_event(f"signup:attempt:{ip}")
    antispam.prune_rate_events()

    # Cheap local checks before anything that costs a network round trip.
    if len(password) < 8:
        return _signup_form("Password must be at least 8 characters.")

    # Layer 4. Before DNS, so a bot can't use signup as a free DNS prober.
    if not antispam.verify_turnstile(request.form, ip):
        _log_blocked("turnstile", ip, email)
        return _signup_form("Couldn't verify that you're human. Please try again.")

    # Layer 3. The direct bounce-preventer.
    reason = antispam.validate_email_address(email)
    if reason:
        _log_blocked("address", ip, email, reason)
        if reason == "syntax":
            return _signup_form("Please enter a valid email address.")
        return _signup_form(
            "That address doesn't look like it can receive mail. "
            "Please check it, or use a different one."
        )

    if User.query.filter_by(email=email).first():
        return _signup_form("An account with that email already exists.")

    # Layer 2b. The tight limit — only reached when mail is about to be sent.
    if not antispam.signup_send_rate_ok(ip):
        _log_blocked("send-rate", ip, email)
        return _signup_form("Too many accounts created from here recently. Please try again later.")

    user = User(
        email=email,
        password_hash=generate_password_hash(password),
        email_verified=False,
    )
    db.session.add(user)
    db.session.commit()
    antispam.record_event(f"signup:sent:{ip}")
    send_verification_email(user)
    return render_template("verify_pending.html", email=email, resent=False)


@app.route("/resend-verification", methods=["POST"])
def resend_verification():
    """Real users lose the first email; without this their only route back in
    was to sign up again with a different address. Tightly limited, and it never
    reveals whether the account exists or is already verified."""
    email = (request.form.get("email") or "").strip().lower()
    ip = antispam.client_ip()

    if antispam.rate_exceeded(f"resend:ip:{ip}", antispam.LIMIT_RESEND_IP_HOUR):
        _log_blocked("resend-rate", ip, email)
    else:
        antispam.record_event(f"resend:ip:{ip}")
        user = User.query.filter_by(email=email).first()
        if user and not user.email_verified:
            send_verification_email(user)

    return render_template("verify_pending.html", email=email, resent=True)


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
        ip = antispam.client_ip()
        # Rate-limited for the same reason signup is: this route will mail any
        # address that has an account, and bot-created accounts are exactly the
        # dead addresses we must stop mailing. Over the limit we still report
        # "sent", to keep the no-disclosure property below intact.
        if antispam.rate_exceeded(f"forgot:ip:{ip}", antispam.LIMIT_FORGOT_IP_HOUR):
            _log_blocked("forgot-rate", ip, email)
        else:
            antispam.record_event(f"forgot:ip:{ip}")
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


# ---------------------------------------------------------------------------
# Resend webhook — bounce/complaint suppression
# ---------------------------------------------------------------------------

def _verify_svix_signature(raw_body: bytes) -> bool:
    """Verify Resend's Svix-signed webhook.

    Signed content is "{id}.{timestamp}.{body}", HMAC-SHA256 with the secret's
    base64 payload, compared against any v1 signature in the header.
    """
    if not RESEND_WEBHOOK_SECRET:
        return False

    msg_id = request.headers.get("svix-id", "")
    timestamp = request.headers.get("svix-timestamp", "")
    sig_header = request.headers.get("svix-signature", "")
    if not (msg_id and timestamp and sig_header):
        return False

    # Reject replays of an old, legitimately-signed delivery.
    try:
        age = abs(datetime.now(timezone.utc).timestamp() - int(timestamp))
    except ValueError:
        return False
    if age > 300:
        return False

    secret = RESEND_WEBHOOK_SECRET
    if secret.startswith("whsec_"):
        secret = secret[len("whsec_"):]
    try:
        key = base64.b64decode(secret)
    except Exception:
        return False

    signed = f"{msg_id}.{timestamp}.".encode() + raw_body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()

    # Header holds space-separated "v1,<sig>" entries — Svix sends more than one
    # during a secret rotation.
    for part in sig_header.split():
        version, _, sig = part.partition(",")
        if version == "v1" and hmac.compare_digest(sig, expected):
            return True
    return False


@app.route("/webhooks/resend", methods=["POST"])
def resend_webhook():
    raw = request.get_data()
    if not _verify_svix_signature(raw):
        abort(401)

    try:
        event = json.loads(raw.decode())
    except (ValueError, UnicodeDecodeError):
        abort(400)

    event_type = event.get("type", "")
    data = event.get("data") or {}
    recipients = data.get("to") or []
    if isinstance(recipients, str):
        recipients = [recipients]

    if event_type == "email.bounced":
        # Transient bounces (full mailbox, greylisting) recover on their own —
        # suppressing those would lock out real users.
        bounce_type = ((data.get("bounce") or {}).get("type") or "").lower()
        if bounce_type and bounce_type != "permanent":
            print(f"Resend {bounce_type} bounce for {recipients} — not suppressing",
                  file=sys.stderr, flush=True)
            return jsonify({"ok": True, "action": "ignored-transient"})
        reason = "bounced"
    elif event_type == "email.complained":
        reason = "complained"
    else:
        return jsonify({"ok": True, "action": "ignored"})

    for addr in recipients:
        addr = (addr or "").strip().lower()
        if not addr:
            continue
        antispam.suppress(addr, reason, detail=json.dumps(data.get("bounce") or {})[:500])
        # A hard bounce on an unverified account means the address was never
        # real. Drop the row so the junk doesn't accumulate.
        user = User.query.filter_by(email=addr).first()
        if user and not user.email_verified:
            EmailToken.query.filter_by(user_id=user.id).delete()
            db.session.delete(user)
            db.session.commit()
            print(f"Removed unverified user after {reason}: {addr}", file=sys.stderr, flush=True)
        else:
            print(f"Suppressed {addr} ({reason})", file=sys.stderr, flush=True)

    return jsonify({"ok": True, "action": reason})


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
    s = _get_or_create_settings(current_user_id())
    return render_template("index.html", theme=s.theme, version=APP_VERSION)


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

    # A weekday recurrence with no start date would sit undated forever: it never
    # lands in a day group and the notifier skips it. Seed the first occurrence.
    if due_date is None and unit == "weeks" and days_csv:
        due_date = first_due_date(date.today(), days_csv)

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

    # Same invariant as create_todo: a weekday recurrence always has a start date.
    if todo.due_date is None and todo.recurrence_unit == "weeks" and todo.recurrence_days:
        todo.due_date = first_due_date(date.today(), todo.recurrence_days)

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
    return render_template("settings.html", settings=s, version=APP_VERSION)


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

    if "theme" in data:
        theme = data["theme"]
        if theme not in THEMES:
            return jsonify({"error": f"theme must be one of: {', '.join(sorted(THEMES))}"}), 400
        s.theme = theme

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
        "email_from": EMAIL_FROM,
        # Signup gate health. unverified_users climbing and mail_sent_last_24h
        # spiking together is the bot-signup signature that started all this.
        "turnstile_enabled": antispam.is_turnstile_enabled(),
        "bounce_webhook_configured": bool(RESEND_WEBHOOK_SECRET),
        "unverified_users": User.query.filter_by(email_verified=False).count(),
        "suppressed_addresses": SuppressedEmail.query.count(),
        "mail_sent_last_24h": antispam.count_since(
            "mail:global", datetime.now(timezone.utc) - timedelta(days=1)
        ),
        "mail_sent_last_hour": antispam.count_since(
            "mail:global", datetime.now(timezone.utc) - timedelta(hours=1)
        ),
        "mail_cap_per_hour": antispam.LIMIT_GLOBAL_MAIL_HOUR[0],
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
