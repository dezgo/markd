#!/usr/bin/env python3
"""Cron script: run every minute to send each user's daily overdue nag.

For each user whose settings say "fire at HH:MM in their local TZ", if it is
currently that minute there and we have not already fired today, send ONE
bundled push listing their overdue items. If nothing is overdue, stay silent —
this is a nag, not a daily digest.
"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import cronlib
from cronlib import log

cronlib.exit_unless_push_configured()

from app import app
from database import db
from models import Todo, UserSettings
from push import build_payload, send_to_user


def overdue_for_user(user_id: int, today_local: date, now_utc_naive: datetime):
    """A user's overdue todos.

    Two conditions because the two storage shapes mean different things: a
    timed todo is late once its instant has passed, an all-day todo only once
    the whole day has gone in the owner's zone. Both are expressed in SQL —
    an all-day task is not "overdue" at one minute past midnight.
    """
    return Todo.query.filter(
        Todo.user_id == user_id,
        Todo.done == False,
        db.or_(
            db.and_(Todo.due_at != None, Todo.due_at < now_utc_naive),
            db.and_(Todo.due_on != None, Todo.due_on < today_local),
        ),
    ).all()


def run():
    with app.app_context():
        now_utc = datetime.now(timezone.utc)
        now_utc_naive = now_utc.replace(tzinfo=None)

        all_settings = UserSettings.query.filter_by(overdue_check_enabled=True).all()
        if not all_settings:
            return

        considered = fired = 0
        for s in all_settings:
            try:
                tz = ZoneInfo(s.timezone or "UTC")
            except ZoneInfoNotFoundError:
                log(f"  user {s.user_id}: bad timezone {s.timezone!r} — skipping")
                continue

            local_now = now_utc.astimezone(tz)
            today_local = local_now.date()

            if local_now.strftime("%H:%M") != s.overdue_check_time:
                continue
            if s.last_overdue_check_date == today_local:
                continue

            considered += 1
            overdue = overdue_for_user(s.user_id, today_local, now_utc_naive)

            # Stamp the date either way, so the remaining ticks inside this
            # minute do not re-check the same user.
            s.last_overdue_check_date = today_local

            if not overdue:
                log(f"  user {s.user_id}: nothing overdue — silent")
                continue

            title = "1 overdue task" if len(overdue) == 1 else f"{len(overdue)} overdue tasks"
            payload = build_payload(title, [t.title for t in overdue],
                                    tag=f"overdue-{today_local.isoformat()}")
            delivered, total = send_to_user(s.user_id, payload, log=log)
            titles = ", ".join(f"'{t.title}'" for t in overdue)
            log(f"  user {s.user_id}: {len(overdue)} overdue [{titles}] -> {delivered}/{total} sub(s)")
            fired += 1

        db.session.commit()
        if considered:
            log(f"run: {len(all_settings)} enabled, {considered} due-this-minute, {fired} push(es) fired")


if __name__ == "__main__":
    run()
