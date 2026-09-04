"""Signup, login, verification and password reset.

The signup gate's layer ordering is deliberate and documented inline: each
layer is cheaper than the one after it, and the ones that put mail on the wire
come last.
"""

import sys

from flask import Blueprint, flash, get_flashed_messages, redirect,     render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import antispam
from database import db
from mail import consume_token, peek_token, send_reset_email, send_verification_email
from models import EmailToken, User

bp = Blueprint("auth", __name__)


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


@bp.route("/signup", methods=["GET", "POST"])
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


@bp.route("/resend-verification", methods=["POST"])
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


def _bad_verify_link():
    return render_template("auth_message.html", title="Link invalid",
                           message="This verification link is invalid or has expired.",
                           link_text="Sign up again", link_href=url_for("auth.signup"))


@bp.route("/verify/<token>", methods=["GET"])
def verify_email_confirm(token):
    """Show a button. Deliberately does not verify anything.

    This used to be a GET that flipped email_verified, which meant every mail
    security gateway that scans links verified the account for its recipient.
    A human click is a POST; a scanner's fetch is not.
    """
    user = peek_token(token, "verify")
    if not user:
        return _bad_verify_link()
    if user.email_verified:
        return redirect(url_for("auth.login"))
    return render_template("verify_confirm.html", token=token, email=user.email)


@bp.route("/verify/<token>", methods=["POST"])
def verify_email(token):
    user = consume_token(token, "verify")
    if not user:
        return _bad_verify_link()
    user.email_verified = True
    db.session.commit()
    flash("Email verified. You can now log in.")
    return redirect(url_for("auth.login"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    next_url = request.args.get("next") or request.form.get("next") or url_for("pages.app_home")
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        ip = antispam.client_ip()

        # Checked before the hash comparison, so a locked-out attacker cannot
        # use response timing to tell a real account from a fake one.
        if not antispam.login_rate_ok(ip, email):
            _log_blocked("login-rate", ip, email)
            error = "Too many failed attempts. Please wait a few minutes and try again."
            return render_template("login.html", error=error,
                                   flashed=get_flashed_messages(), next_url=next_url)

        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            if not user.email_verified:
                error = "Please verify your email first. Check your inbox for the link."
            else:
                session.permanent = True
                session["user_id"] = user.id
                return redirect(next_url)
        else:
            antispam.record_login_failure(ip, email)
            antispam.prune_rate_events()
            error = "Incorrect email or password."
    flashed = get_flashed_messages()
    return render_template("login.html", error=error, flashed=flashed, next_url=next_url)


@bp.route("/forgot-password", methods=["GET", "POST"])
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


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if not peek_token(token, "reset"):
        return render_template("auth_message.html", title="Link invalid",
                               message="This reset link is invalid or has expired.",
                               link_text="Request a new one", link_href=url_for("auth.forgot_password"))
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
                return redirect(url_for("auth.login"))
            error = "This link is no longer valid."
    return render_template("reset.html", error=error, token=token)
