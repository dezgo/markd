"""Admin usage overview.

Answers the questions the per-user diagnostics page cannot: who else is on
this instance, are they actually using it, and where did the push
subscriptions come from.

Two constraints shape this module, both learned from the real data:

  * Every count is a grouped aggregate rather than a query per user. The page
    has to stay cheap enough that reading it is never a reason not to.
  * Most accounts on this instance are bot signups that never verified, so the
    per-account table lists only accounts that show a sign of life and
    summarises the rest. Rendering every row would bury the real users.
"""

from collections import Counter
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
# A signup this new is still plausibly a person who hasn't clicked the link yet.
GRACE_DAYS = 7
# Most rows past this are noise; the summary line accounts for the remainder.
ROW_LIMIT = 100
# How far back the signup histogram goes.
TIMELINE_MONTHS = 12


def _by_user(query):
    return dict(query.all())


def _month_counts(dates):
    """{'YYYY-MM': n} — bucketed here rather than in SQL, because strftime is
    SQLite-specific and the whole point of this page is that it keeps working."""
    return Counter(d.strftime("%Y-%m") for d in dates if d)


def _gather():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    active_cutoff = now - timedelta(days=ACTIVE_WINDOW_DAYS)
    grace_cutoff = now - timedelta(days=GRACE_DAYS)

    # One row per user, four scalar columns — no ORM hydration. At ~1600 users
    # this is far cheaper than the aggregates it feeds.
    users = db.session.query(
        User.id, User.email, User.email_verified, User.created_at).all()

    total = _by_user(db.session.query(Todo.user_id, func.count(Todo.id))
                     .group_by(Todo.user_id))
    active = _by_user(db.session.query(Todo.user_id, func.count(Todo.id))
                      .filter(Todo.done == False).group_by(Todo.user_id))
    last_touch = _by_user(db.session.query(Todo.user_id, func.max(Todo.updated_at))
                          .group_by(Todo.user_id))
    subs = _by_user(db.session.query(PushSubscription.user_id,
                                     func.count(PushSubscription.id))
                    .group_by(PushSubscription.user_id))
    settings = {s.user_id: s for s in db.session.query(
        UserSettings.user_id, UserSettings.timezone,
        UserSettings.overdue_check_enabled).all()}

    rows, listed, hidden_unverified, hidden_idle = [], 0, 0, 0
    for uid, email, verified, created_at in users:
        seen = last_touch.get(uid)
        todos_total = total.get(uid, 0)

        # A row earns its place by showing a sign of life. An unverified
        # account with no todos is a bot signup; there are more of those than
        # everything else combined.
        interesting = bool(verified or todos_total or subs.get(uid)
                           or (created_at and created_at >= grace_cutoff))
        if not interesting:
            hidden_unverified += 1
            continue

        listed += 1
        if listed > ROW_LIMIT:
            hidden_idle += 1
            continue

        s = settings.get(uid)
        rows.append({
            "id": uid,
            "email": email,
            "verified": verified,
            "created_at": created_at,
            "todos_total": todos_total,
            "todos_active": active.get(uid, 0),
            "subscriptions": subs.get(uid, 0),
            "last_seen": seen,
            "is_active": bool(seen and seen >= active_cutoff),
            "timezone": s.timezone if s else "—",
            "nag_enabled": bool(s and s.overdue_check_enabled),
        })

    # Most recently used first, then newest signup. Nothing about the account
    # id ordering was useful once the table stopped being five rows long.
    rows.sort(key=lambda r: (r["last_seen"] or datetime.min,
                             r["created_at"] or datetime.min), reverse=True)

    # Subscriptions whose user row is gone. Before delete_user was unified,
    # the bounce webhook removed the account and left these behind, so a
    # non-zero count here is that bug's residue.
    known = {u[0] for u in users}
    orphan_subs = sum(n for uid, n in subs.items() if uid not in known)

    created = [c for _, _, _, c in users]
    verified_count = sum(1 for _, _, v, _ in users if v)
    signups = _month_counts(created)
    months = sorted(signups)[-TIMELINE_MONTHS:]

    return {
        "rows": rows,
        "active_window_days": ACTIVE_WINDOW_DAYS,
        "row_limit": ROW_LIMIT,
        "grace_days": GRACE_DAYS,
        "hidden_unverified": hidden_unverified,
        "hidden_idle": hidden_idle,
        "user_count": len(users),
        "verified_count": verified_count,
        "unverified_count": len(users) - verified_count,
        "active_count": sum(1 for r in rows if r["is_active"]),
        "todo_count": sum(total.values()),
        "subscription_count": sum(subs.values()),
        "orphan_subscriptions": orphan_subs,
        "suppressed": SuppressedEmail.query.count(),
        "mail_24h": antispam.count_since(
            "mail:global", datetime.now(timezone.utc) - timedelta(days=1)),
        "signups_24h": sum(1 for c in created if c and c >= now - timedelta(days=1)),
        "signups_7d": sum(1 for c in created if c and c >= grace_cutoff),
        "signups_30d": sum(1 for c in created if c and c >= active_cutoff),
        "timeline": [(m, signups[m]) for m in months],
        "timeline_peak": max((signups[m] for m in months), default=0),
        "turnstile_enabled": antispam.is_turnstile_enabled(),
    }


@bp.route("/admin")
@require_admin
def admin_overview():
    return render_template("admin.html", stats=_gather())
