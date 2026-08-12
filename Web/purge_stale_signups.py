#!/usr/bin/env python3
"""Cron script: delete accounts that were never verified, plus expired tokens.

Bot signups leave unverified User rows behind forever. Beyond the clutter, they
are a live liability: every one is an address that can be targeted through
/forgot-password, and each is a dead mailbox waiting to bounce. Anything still
unverified after PURGE_UNVERIFIED_DAYS is junk — a real user who wanted the
account would have clicked the link, or used "send it again".

Run daily:
    0 4 * * * /var/www/markd/Web/.venv/bin/python /var/www/markd/Web/purge_stale_signups.py

Pass --dry-run to see what would go without deleting anything.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from app import app
from database import db
from models import EmailToken, PushSubscription, RateEvent, Todo, User, UserSettings

PURGE_UNVERIFIED_DAYS = int(os.environ.get("PURGE_UNVERIFIED_DAYS", "7"))

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

    for user in stale:
        log(f"{'would delete' if DRY_RUN else 'deleting'} unverified {user.email} "
            f"(created {user.created_at:%Y-%m-%d})")
        if DRY_RUN:
            continue
        # Explicit cleanup of dependents — there are no cascade rules on these
        # relationships, so orphan rows would otherwise be left behind.
        EmailToken.query.filter_by(user_id=user.id).delete()
        PushSubscription.query.filter_by(user_id=user.id).delete()
        UserSettings.query.filter_by(user_id=user.id).delete()
        Todo.query.filter_by(user_id=user.id).delete()
        db.session.delete(user)

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
