#!/usr/bin/env python3
"""Cron script: run every minute to push notifications for due todos."""
import json
import os
import sys
from datetime import datetime, time as dt_time

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_CONTACT = os.environ.get("VAPID_CONTACT", "mailto:admin@example.com")
TIMEZONE = os.environ.get("TIMEZONE", "UTC")

if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
    print("VAPID keys not configured — skipping", flush=True)
    sys.exit(0)

from dateutil import tz
from pywebpush import WebPushException, webpush

from app import app
from database import db
from models import PushSubscription, Todo

NOTIFY_HOUR = 9  # hour to notify for date-only todos


def run():
    with app.app_context():
        now = datetime.now(tz.gettz(TIMEZONE)).replace(tzinfo=None)

        candidates = Todo.query.filter(
            Todo.done == False,
            Todo.notified_at == None,
            Todo.due_date != None,
        ).all()

        to_notify = []
        for todo in candidates:
            if todo.due_time:
                h, m = map(int, todo.due_time.split(":"))
                due_dt = datetime.combine(todo.due_date, dt_time(h, m))
            else:
                due_dt = datetime.combine(todo.due_date, dt_time(NOTIFY_HOUR, 0))

            if due_dt <= now:
                to_notify.append((todo, due_dt))

        if not to_notify:
            return

        subs = PushSubscription.query.all()

        for todo, due_dt in to_notify:
            if todo.due_time:
                time_label = due_dt.strftime("%-I:%M %p")
                body = f"Due today at {time_label}"
            else:
                body = f"Due today"
            payload = json.dumps({
                "title": todo.title,
                "body": body,
                "tag": f"todo-{todo.id}",
            })

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
                except WebPushException as exc:
                    if exc.response and exc.response.status_code in (404, 410):
                        dead_subs.append(sub)
                    else:
                        print(f"Push error for sub {sub.id}: {exc}", flush=True)
                except Exception as exc:
                    print(f"Push error for sub {sub.id}: {exc}", flush=True)

            for sub in dead_subs:
                db.session.delete(sub)

            todo.notified_at = now

        db.session.commit()


if __name__ == "__main__":
    run()
