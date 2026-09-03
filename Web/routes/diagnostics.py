"""Per-user diagnostics page.

Exists because the signup-bot incident was invisible from the outside: the
counters here are what made "unverified users climbing while mail sent spikes"
legible. Keep it cheap enough to load on a phone.
"""

from datetime import datetime, timedelta, timezone

from flask import Blueprint, render_template

import antispam
from accounts import current_user, current_user_id, is_admin, require_session
from config import EMAIL_FROM, NOTIFICATIONS_LOG, RESEND_API_KEY,     RESEND_WEBHOOK_SECRET, VAPID_CONTACT, VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY
from models import PushSubscription, SuppressedEmail, Todo, User

bp = Blueprint("diagnostics", __name__)


@bp.route("/diagnostics")
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
        Todo.due_at != None,
    ).count()

    info = {
        "is_admin": is_admin(),
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
