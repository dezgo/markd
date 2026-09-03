"""Admin usage overview.

Answers the questions the per-user diagnostics page cannot: who else is on
this instance, are they actually using it, and where did the push
subscriptions come from.

Every count is a grouped aggregate rather than a query per user — the page has
to stay cheap enough that reading it is never a reason not to.
"""

from datetime import datetime, timedelta, timezone

from flask import Blueprint, render_template
from sqlalchemy import func

import antispam
from accounts import require_admin
from database import db
from models import PushSubscription, SuppressedEmail, Todo, User, UserSettings

bp = Blueprint("admin", __name__)

# How recently a user must have touched a todo to count as active.
ACTIVE_WINDOW_DAYS = 30


def _counts_by_user(query):
    return dict(query.group_by(Todo.user_id).all())


def _gather():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    active_cutoff = now - timedelta(days=ACTIVE_WINDOW_DAYS)

    total = _counts_by_user(db.session.query(Todo.user_id, func.count(Todo.id)))
    active = _counts_by_user(
        db.session.query(Todo.user_id, func.count(Todo.id)).filter(Todo.done == False))
    last_touch = dict(
        db.session.query(Todo.user_id, func.max(Todo.updated_at))
        .group_by(Todo.user_id).all())
    subs = dict(
        db.session.query(PushSubscription.user_id, func.count(PushSubscription.id))
        .group_by(PushSubscription.user_id).all())
    settings = {s.user_id: s for s in UserSettings.query.all()}

    rows = []
    for user in User.query.order_by(User.created_at).all():
        seen = last_touch.get(user.id)
        s = settings.get(user.id)
        rows.append({
            "id": user.id,
            "email": user.email,
            "verified": user.email_verified,
            "created_at": user.created_at,
            "todos_total": total.get(user.id, 0),
            "todos_active": active.get(user.id, 0),
            "subscriptions": subs.get(user.id, 0),
            "last_seen": seen,
            "is_active": bool(seen and seen >= active_cutoff),
            "timezone": s.timezone if s else "—",
            "nag_enabled": bool(s and s.overdue_check_enabled),
        })

    # Subscriptions whose user row is gone. Before delete_user was unified,
    # the bounce webhook removed the account and left these behind, so a
    # non-zero count here is that bug's residue.
    known = {u.id for u in User.query.all()}
    orphan_subs = sum(n for uid, n in subs.items() if uid not in known)

    return {
        "rows": rows,
        "active_window_days": ACTIVE_WINDOW_DAYS,
        "user_count": len(rows),
        "verified_count": sum(1 for r in rows if r["verified"]),
        "active_count": sum(1 for r in rows if r["is_active"]),
        "todo_count": sum(r["todos_total"] for r in rows),
        "subscription_count": sum(r["subscriptions"] for r in rows),
        "orphan_subscriptions": orphan_subs,
        "suppressed": SuppressedEmail.query.count(),
        "mail_24h": antispam.count_since(
            "mail:global", datetime.now(timezone.utc) - timedelta(days=1)),
        "signups_7d": db.session.query(func.count(User.id)).filter(
            User.created_at >= now - timedelta(days=7)).scalar(),
    }


@bp.route("/admin")
@require_admin
def admin_overview():
    return render_template("admin.html", stats=_gather())
