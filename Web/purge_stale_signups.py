#!/usr/bin/env python3
"""Cron script: delete accounts that were never verified, plus expired tokens.

Bot signups leave unverified User rows behind forever. Beyond the clutter, they
are a live liability: every one is an address that can be targeted through
/forgot-password, and each is a dead mailbox waiting to bounce. Anything still
unverified after PURGE_UNVERIFIED_DAYS is junk — a real user who wanted the
account would have clicked the link, or used "send it again".

Installed as a daily cron by setup.sh. It was documented as a manual step and
never actually added, which is the whole reason 1267 flood accounts from June
and July were still sitting in the database in September.

Pass --dry-run to see what would go without deleting anything.

This handles unverified rows on a rolling window. It deliberately never touches
a verified account, however idle: a real person who verifies and then ignores
the app for a month must not be deleted by a nightly job. The one-off cleanup of
accounts that were verified by a mail scanner rather than a person lives in
purge_dormant.py.
"""
import sys
from datetime import datetime, timedelta, timezone

import config

# Importing config is what loads .env — it derives the path from its own
# __file__, so this works whatever directory cron runs it from. Setting this
# before importing app keeps four static config warnings out of a log that
# exists to show what was deleted.
config.QUIET_STARTUP = True

from accounts import delete_user  # noqa: E402
from app import app  # noqa: E402
from database import db  # noqa: E402
from dbbackup import backup  # noqa: E402
from models import EmailToken, RateEvent, User  # noqa: E402

PURGE_UNVERIFIED_DAYS = config.PURGE_UNVERIFIED_DAYS

# A normal night removes a handful. Anything past this is a backlog being
# cleared for the first time, and is worth a copy of the database first.
BACKUP_THRESHOLD = 50

DRY_RUN = "--dry-run" in sys.argv


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def purge_unverified(cutoff):
    stale = User.query.filter(
        User.email_verified.is_(False),
        User.created_at < cutoff,
    ).all()
    if not stale:
        log("no stale unverified accounts")
        return 0

    if len(stale) >= BACKUP_THRESHOLD and not DRY_RUN:
        path = backup(app.config["SQLALCHEMY_DATABASE_URI"], "prepurge")
        log(f"{len(stale)} accounts to remove — backed up to {path or 'nowhere (not sqlite)'}")

    # One line per account would be thousands on the first run. Log the shape
    # instead, and the individual addresses only when there are few enough to
    # be worth reading.
    if len(stale) <= 20:
        for user in stale:
            log(f"{'would delete' if DRY_RUN else 'deleting'} unverified "
                f"{user.email} (created {user.created_at:%Y-%m-%d})")
    else:
        by_month = {}
        for user in stale:
            key = user.created_at.strftime("%Y-%m") if user.created_at else "unknown"
            by_month[key] = by_month.get(key, 0) + 1
        for month in sorted(by_month):
            log(f"  {month}: {by_month[month]}")

    for user in stale:
        if DRY_RUN:
            continue
        # There are no cascade rules on these relationships, so dependents are
        # cleared explicitly — see accounts.delete_user, which is also what the
        # bounce webhook uses so the two paths cannot drift apart again.
        delete_user(user)

    if not DRY_RUN:
        db.session.commit()
    return len(stale)


def purge_expired_tokens(now):
    q = EmailToken.query.filter(EmailToken.expires_at < now)
    n = q.count()
    if n and not DRY_RUN:
        q.delete()
        db.session.commit()
    log(f"{'would delete' if DRY_RUN else 'deleted'} {n} expired email tokens")
    return n


def purge_rate_events(now):
    cutoff = now - timedelta(days=2)
    q = RateEvent.query.filter(RateEvent.created_at < cutoff)
    n = q.count()
    if n and not DRY_RUN:
        q.delete()
        db.session.commit()
    log(f"{'would delete' if DRY_RUN else 'deleted'} {n} old rate-limit events")
    return n


def main():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=PURGE_UNVERIFIED_DAYS)
    if DRY_RUN:
        log("DRY RUN — nothing will be deleted")
    log(f"purging unverified accounts created before {cutoff:%Y-%m-%d %H:%M}")

    with app.app_context():
        users = purge_unverified(cutoff)
        purge_expired_tokens(now)
        purge_rate_events(now)

    log(f"done — {users} unverified account(s) {'would be ' if DRY_RUN else ''}removed")


if __name__ == "__main__":
    main()
