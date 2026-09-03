#!/usr/bin/env python3
"""Cron script: run every minute to push notifications for due todos.

Bundling rule: if a user has several todos firing in the same tick, send ONE
push covering all of them. See push.py — Markd never sends a user more than
one notification at a time.
"""
from collections import defaultdict
from datetime import datetime, time as dt_time, timezone

import cronlib
from cronlib import log

cronlib.exit_unless_push_configured()

from app import app
from database import db
from models import PushSubscription, Todo
from push import build_payload, send_to_user


def due_now(todos, now_utc):
    """Todos whose due_date + due_time (stored as UTC) has arrived."""
    due = []
    for todo in todos:
        try:
            h, m = map(int, todo.due_time.split(":"))
        except (AttributeError, ValueError):
            log(f"  todo {todo.id}: unparseable due_time {todo.due_time!r} — skipping")
            continue
        if datetime.combine(todo.due_date, dt_time(h, m)) <= now_utc:
            due.append(todo)
    return due


def payload_for(todos, now_utc):
    if len(todos) == 1:
        t = todos[0]
        return build_payload(t.title, [t.title], tag=f"todo-{t.id}")
    return build_payload(
        f"{len(todos)} tasks due now",
        [t.title for t in todos],
        tag=f"todo-batch-{now_utc.strftime('%Y%m%d%H%M')}",
    )


def run():
    with app.app_context():
        # due_date + due_time are stored as UTC; compare against UTC now.
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

        candidates = Todo.query.filter(
            Todo.done == False,
            Todo.notified_at == None,
            Todo.due_date != None,
            Todo.due_time != None,
        ).all()
        to_notify = due_now(candidates, now_utc)

        sub_count = PushSubscription.query.count()
        log(f"run: {len(candidates)} candidate(s), {len(to_notify)} due, {sub_count} sub(s) total")
        if not to_notify:
            return

        by_user = defaultdict(list)
        for todo in to_notify:
            by_user[todo.user_id].append(todo)

        for user_id, todos in by_user.items():
            delivered, total = send_to_user(
                user_id, payload_for(todos, now_utc),
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
