#!/usr/bin/env python3
"""Cron script: run every minute to send each user's daily overdue nag.

For each user whose settings say "fire at HH:MM in their local TZ", if it's
currently that minute (in their TZ) and we haven't already fired today, send
ONE bundled push listing their overdue items. If nothing is overdue, stay
silent — this is a nag, not a daily digest.
"""
import json
import os
import sys
from datetime import datetime, date, time as dt_time, timezone

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_CONTACT = os.environ.get("VAPID_CONTACT", "mailto:admin@example.com")

if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
    print("VAPID keys not configured — skipping", flush=True)
    sys.exit(0)

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pywebpush import WebPushException, webpush

from app import app
from database import db
from models import PushSubscription, Todo, UserSettings


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _overdue_for_user(user_id: int, today_local: date, now_utc_naive: datetime):
    """Return list of overdue Todos for the user.

    - Date-only todos: due_date < today_in_user_tz.
    - Date+time todos: combined UTC datetime < now (UTC).
    """
    overdue = []
    todos = Todo.query.filter(
        Todo.user_id == user_id,
        Todo.done == False,
        Todo.due_date != None,
    ).all()
    for t in todos:
        if t.due_time:
            try:
                h, m = map(int, t.due_time.split(":"))
            except ValueError:
                continue
            if datetime.combine(t.due_date, dt_time(h, m)) < now_utc_naive:
                overdue.append(t)
        else:
            if t.due_date < today_local:
                overdue.append(t)
    return overdue


def _send_bundled_push(user_id: int, todos, tag: str) -> tuple[int, int]:
    subs = PushSubscription.query.filter_by(user_id=user_id).all()
    if not subs:
        return 0, 0
    titles = [t.title for t in todos]
    shown = titles[:5]
    body = ", ".join(shown)
    if len(titles) > 5:
        body += f", +{len(titles) - 5} more"
    title = "1 overdue task" if len(titles) == 1 else f"{len(titles)} overdue tasks"
    payload = json.dumps({"title": title, "body": body, "tag": tag})

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
        now_utc = datetime.now(timezone.utc)
        now_utc_naive = now_utc.replace(tzinfo=None)

        all_settings = UserSettings.query.filter_by(overdue_check_enabled=True).all()
        if not all_settings:
            return

        considered = 0
        fired = 0
        for s in all_settings:
            try:
                tz = ZoneInfo(s.timezone or "UTC")
            except ZoneInfoNotFoundError:
                log(f"  user {s.user_id}: bad timezone {s.timezone!r} — skipping")
                continue

            local_now = now_utc.astimezone(tz)
            today_local = local_now.date()
            hhmm_now = local_now.strftime("%H:%M")

            if hhmm_now != s.overdue_check_time:
                continue
            if s.last_overdue_check_date == today_local:
                continue

            considered += 1
            overdue = _overdue_for_user(s.user_id, today_local, now_utc_naive)
            if not overdue:
                # Mark anyway so we don't keep checking every minute within the same minute window.
                s.last_overdue_check_date = today_local
                log(f"  user {s.user_id}: nothing overdue — silent")
                continue

            tag = f"overdue-{today_local.isoformat()}"
            sent_ok, total = _send_bundled_push(s.user_id, overdue, tag)
            s.last_overdue_check_date = today_local
            titles = ", ".join(f"'{t.title}'" for t in overdue)
            log(f"  user {s.user_id}: {len(overdue)} overdue [{titles}] -> {sent_ok}/{total} sub(s)")
            fired += 1

        db.session.commit()
        if considered:
            log(f"run: {len(all_settings)} enabled, {considered} due-this-minute, {fired} push(es) fired")


if __name__ == "__main__":
    run()
