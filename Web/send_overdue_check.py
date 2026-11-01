#!/usr/bin/env python3
"""Cron script: run every minute to send each user's daily overdue nag.

For each user whose settings say "fire at HH:MM in their local TZ", if it is
currently that minute there and we have not already fired today, send ONE
bundled push listing their overdue items. If nothing is overdue, stay silent —
this is a nag, not a daily digest.

A timed task falling due in the same minute is folded into the nag rather
than pushed separately — see overdue.py.
"""
import cronlib
from cronlib import log

cronlib.exit_unless_push_configured()

from app import app
from database import db
from models import UserSettings
from overdue import mark_listed_as_notified, nag_due_now, overdue_for_user, user_tz
from push import build_payload, send_to_user
import scheduling


def run():
    with app.app_context():
        now_utc = scheduling.now_utc()

        all_settings = UserSettings.query.filter_by(overdue_check_enabled=True).all()
        if not all_settings:
            return

        considered = fired = 0
        for s in all_settings:
            if user_tz(s) is None:
                log(f"  user {s.user_id}: bad timezone {s.timezone!r} — skipping")
                continue

            today_local = nag_due_now(s, now_utc)
            if today_local is None:
                continue

            considered += 1
            overdue = overdue_for_user(s.user_id, today_local, now_utc)

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
            if delivered > 0 or total == 0:
                mark_listed_as_notified(overdue, now_utc)
            titles = ", ".join(f"'{t.title}'" for t in overdue)
            log(f"  user {s.user_id}: {len(overdue)} overdue [{titles}] -> {delivered}/{total} sub(s)")
            fired += 1

        db.session.commit()
        if considered:
            log(f"run: {len(all_settings)} enabled, {considered} due-this-minute, {fired} push(es) fired")


if __name__ == "__main__":
    run()
