#!/usr/bin/env python3
"""Delete the accounts left behind by the June-July 2026 signup flood.

1633 accounts were created between May and July 2026; four of them belong to
people. The rest were automated signups. Roughly a fifth of those show as
"verified" because the old GET-based verification link was fetched by the
recipients' mail security gateways, so verified status is not evidence of a
person and is deliberately not part of the criterion below.

An account is dormant when it owns no todo, has no push subscription, and was
created before the cutoff. That is a statement about use, not about how the
row was made, which is why it is safe: any account someone actually used fails
it. Deletion goes through accounts.delete_user, the same cascade the account
settings page uses, so no dependent rows are orphaned.

This is a one-off. Ongoing hygiene — unverified accounts on a rolling window,
expired tokens, old rate-limit rows — is purge_stale_signups.py, which setup.sh
installs as a daily cron. Run that one first: it removes every unverified row,
which is most of this, and what is left here is the scanner-verified remainder.

Dry run unless --apply is passed. --apply takes a backup first.

    python3 purge_dormant.py                     # report only
    python3 purge_dormant.py --apply             # back up, then delete
    python3 purge_dormant.py --cutoff 2026-08-01
"""

import argparse
import sys
from collections import Counter
from datetime import datetime

import config

# Before `from app import app`, which is what emits them. A maintenance script
# reporting that Turnstile is off just buries its own output.
config.QUIET_STARTUP = True

from accounts import ADMIN_USER_ID, delete_user  # noqa: E402
from app import app  # noqa: E402
from dbbackup import backup  # noqa: E402
from database import db
from models import PushSubscription, Todo, User

DEFAULT_CUTOFF = "2026-08-01"
BATCH = 200


def dormant(cutoff):
    """Accounts with nothing to lose. The admin is never a candidate."""
    has_todo = db.session.query(Todo.user_id).distinct().subquery()
    has_push = db.session.query(PushSubscription.user_id).distinct().subquery()
    return (User.query
            .filter(User.created_at < cutoff)
            .filter(User.id != ADMIN_USER_ID)
            .filter(~User.id.in_(db.session.query(has_todo.c.user_id)))
            .filter(~User.id.in_(db.session.query(has_push.c.user_id)))
            .order_by(User.id))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="actually delete (default is a dry run)")
    ap.add_argument("--cutoff", default=DEFAULT_CUTOFF,
                    help=f"only accounts created before this date (default {DEFAULT_CUTOFF})")
    args = ap.parse_args()

    cutoff = datetime.strptime(args.cutoff, "%Y-%m-%d")

    with app.app_context():
        total = User.query.count()
        victims = dormant(cutoff).all()
        keeping = total - len(victims)

        by_month = Counter(u.created_at.strftime("%Y-%m") for u in victims
                           if u.created_at)
        verified = sum(1 for u in victims if u.email_verified)

        print(f"\n  accounts            {total:,}")
        print(f"  dormant before {args.cutoff}  {len(victims):,}"
              f"   ({verified:,} of them flagged verified)")
        print(f"  keeping             {keeping:,}\n")

        for month in sorted(by_month):
            print(f"    {month}   {by_month[month]:,}")

        survivors = [u for u in User.query.order_by(User.id).all()
                     if u.id not in {v.id for v in victims}]
        print("\n  surviving accounts:")
        for u in survivors[:25]:
            print(f"    {u.id:>5}  {u.email}")
        if len(survivors) > 25:
            print(f"    ... and {len(survivors) - 25:,} more")

        if not args.apply:
            print("\n  Dry run. Re-run with --apply to delete.\n")
            return

        if not victims:
            print("\n  Nothing to do.\n")
            return

        print()
        path = backup(app.config["SQLALCHEMY_DATABASE_URI"], "predormant")
        if not path:
            print("  !! not a local sqlite database — back it up yourself first.")
            sys.exit(1)
        print(f"    backup: {path}")

        done = 0
        for user in victims:
            delete_user(user)
            done += 1
            if done % BATCH == 0:
                db.session.commit()
                print(f"    deleted {done:,}/{len(victims):,}")
        db.session.commit()
        print(f"    deleted {done:,}/{len(victims):,}")
        print(f"\n  Done. {User.query.count():,} accounts remain.\n")


if __name__ == "__main__":
    main()
