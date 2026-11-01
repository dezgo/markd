#!/usr/bin/env python3
"""Cron script: run every minute to push notifications for due todos.

Bundling rule: if a user has several todos firing in the same tick, send ONE
push covering all of them. See push.py — Markd never sends a user more than
one notification at a time.
"""
from collections import defaultdict
import cronlib
from cronlib import log

cronlib.exit_unless_push_configured()

from app import app
from database import db
from models import PushSubscription, Todo
import scheduling
from overdue import users_with_nag_due_now
from push import due_payload, send_to_user


def run():
    with app.app_context():
        now_utc = scheduling.now_utc()

        # due_at is a UTC instant in a real DATETIME column, so this is one
        # indexed comparison. It used to load every dated todo and re-combine
        # a date with a "HH:MM" string in Python to decide the same thing.
        # All-day todos (due_on) are deliberately not here — they have no time
        # to fire at, and the daily overdue nag covers them.
        to_notify = Todo.query.filter(
            Todo.done == False,
            Todo.notified_at == None,
            Todo.due_at != None,
            Todo.due_at <= now_utc,
        ).all()

        # Scheduled-but-not-yet-fired, for context in the log: "0 due" alone
        # cannot be told apart from "nothing is scheduled at all".
        pending = Todo.query.filter(
            Todo.done == False, Todo.notified_at == None, Todo.due_at != None,
        ).count()
        sub_count = PushSubscription.query.count()
        log(f"run: {pending} scheduled, {len(to_notify)} due, {sub_count} sub(s) total")
        if not to_notify:
            return

        # A user whose overdue nag fires this minute gets these tasks inside
        # it instead (overdue.py). Left unmarked, so if the nag does not take
        # them they go out on their own next minute.
        held = users_with_nag_due_now(now_utc)

        by_user = defaultdict(list)
        for todo in to_notify:
            if todo.user_id in held:
                continue
            by_user[todo.user_id].append(todo)
        for user_id in held & {t.user_id for t in to_notify}:
            log(f"  user {user_id}: held for this minute's overdue nag")

        for user_id, todos in by_user.items():
            delivered, total = send_to_user(
                user_id, due_payload(todos, now_utc),
                # A task due at a chosen time is allowed to wake the device.
                urgency="high", log=log,
            )
            # Mark notified only if it actually went somewhere, or there was
            # nowhere to send it. Subscriptions existing but all failing is
            # transient, so leave it unmarked and retry next tick.
            if delivered > 0 or total == 0:
                for t in todos:
                    t.notified_at = now_utc
            titles = ", ".join(f"'{t.title}'" for t in todos)
            log(f"  user {user_id}: {len(todos)} todo(s) [{titles}] -> {delivered}/{total} sub(s)")

        db.session.commit()


if __name__ == "__main__":
    run()
