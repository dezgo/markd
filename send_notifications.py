#!/usr/bin/env python3
"""Cron script: run every minute to push notifications for due todos.

Bundling rule: if a user has multiple todos firing in the same tick, send ONE
push that covers all of them. Markd never sends more than one push to a user
at a time.
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, time as dt_time, timezone

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_CONTACT = os.environ.get("VAPID_CONTACT", "mailto:admin@example.com")

if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
    print("VAPID keys not configured — skipping", flush=True)
    sys.exit(0)

from pywebpush import WebPushException, webpush

from app import app
from database import db
from models import PushSubscription, Todo


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _build_payload(todos, now_utc):
    if len(todos) == 1:
        t = todos[0]
        return json.dumps({
            "title": t.title,
            "body": "Due now",
            "tag": f"todo-{t.id}",
        })
    titles = [t.title for t in todos]
    shown = titles[:5]
    body = ", ".join(shown)
    if len(titles) > 5:
        body += f", +{len(titles) - 5} more"
    return json.dumps({
        "title": f"{len(titles)} tasks due now",
        "body": body,
        "tag": f"todo-batch-{now_utc.strftime('%Y%m%d%H%M')}",
    })


def _send_to_user(user_id, todos, now_utc):
    subs = PushSubscription.query.filter_by(user_id=user_id).all()
    if not subs:
        return 0, 0
    payload = _build_payload(todos, now_utc)
    sent_ok = 0
    dead_subs = []
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_CONTACT},
            )
            sent_ok += 1
        except WebPushException as exc:
            code = exc.response.status_code if exc.response is not None else "?"
            if code in (404, 410):
                dead_subs.append(sub)
                log(f"  sub {sub.id}: gone ({code}) — removing")
            else:
                log(f"  sub {sub.id}: FAIL ({code}) {exc}")
        except Exception as exc:
            log(f"  sub {sub.id}: ERROR {exc!r}")
    for sub in dead_subs:
        db.session.delete(sub)
    return sent_ok, len(subs)


def run():
    with app.app_context():
        # due_date + due_time are stored as UTC; compare against UTC now
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

        # Only notify tasks that have a specific time set (stored as UTC)
        candidates = Todo.query.filter(
            Todo.done == False,
            Todo.notified_at == None,
            Todo.due_date != None,
            Todo.due_time != None,
        ).all()

        to_notify = []
        for todo in candidates:
            h, m = map(int, todo.due_time.split(":"))
            due_dt = datetime.combine(todo.due_date, dt_time(h, m))
            if due_dt <= now_utc:
                to_notify.append(todo)

        sub_count = PushSubscription.query.count()
        log(f"run: {len(candidates)} candidate(s), {len(to_notify)} due, {sub_count} sub(s) total")

        if not to_notify:
            return

        by_user = defaultdict(list)
        for todo in to_notify:
            by_user[todo.user_id].append(todo)

        for user_id, todos in by_user.items():
            sent_ok, total = _send_to_user(user_id, todos, now_utc)
            for t in todos:
                t.notified_at = now_utc
            titles = ", ".join(f"'{t.title}'" for t in todos)
            log(f"  user {user_id}: {len(todos)} todo(s) [{titles}] -> {sent_ok}/{total} sub(s)")

        db.session.commit()


if __name__ == "__main__":
    run()
