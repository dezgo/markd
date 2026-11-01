"""The daily overdue nag's rules, shared by both cron senders.

The two senders run every minute as separate processes, in no fixed order.
When a timed task falls due in the same minute as its owner's overdue nag,
both would push it: the task on its own, and again inside the nag. Markd
never sends a user more than one push at a time, so the nag wins:

- the nag marks every timed task it lists as notified, so the due-time sender
  skips them if it runs second;
- the due-time sender holds a user back during the minute their nag is still
  to fire, so it does not get in first. If the nag never fires (crash, or
  nothing comes of it), the next minute is no longer the nag's minute and the
  task goes out on its own then — a minute late, not lost.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from database import db
from models import Todo, UserSettings


def user_tz(settings: UserSettings):
    """The user's zone, or None if their stored name is not a real zone."""
    try:
        return ZoneInfo(settings.timezone or "UTC")
    except ZoneInfoNotFoundError:
        return None


def nag_due_now(settings: UserSettings, now_utc: datetime):
    """The user's local date if their nag should fire this minute, else None.

    `now_utc` is naive UTC, matching storage.
    """
    if not settings.overdue_check_enabled:
        return None
    tz = user_tz(settings)
    if tz is None:
        return None
    local_now = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    if local_now.strftime("%H:%M") != settings.overdue_check_time:
        return None
    if settings.last_overdue_check_date == local_now.date():
        return None
    return local_now.date()


def users_with_nag_due_now(now_utc: datetime) -> set:
    """IDs of users whose nag has yet to fire in this minute."""
    return {
        s.user_id
        for s in UserSettings.query.filter_by(overdue_check_enabled=True).all()
        if nag_due_now(s, now_utc) is not None
    }


def overdue_for_user(user_id: int, today_local: date, now_utc: datetime):
    """A user's overdue todos.

    Two conditions because the two storage shapes mean different things: a
    timed todo is late once its instant has arrived, an all-day todo only once
    the whole day has gone in the owner's zone. Both are expressed in SQL —
    an all-day task is not "overdue" at one minute past midnight.
    """
    return Todo.query.filter(
        Todo.user_id == user_id,
        Todo.done == False,
        db.or_(
            db.and_(Todo.due_at != None, Todo.due_at <= now_utc),
            db.and_(Todo.due_on != None, Todo.due_on < today_local),
        ),
    ).order_by(Todo.id).all()


def mark_listed_as_notified(todos, now_utc: datetime):
    """Stop the due-time sender pushing a task the nag has just covered."""
    for t in todos:
        if t.due_at is not None and t.notified_at is None:
            t.notified_at = now_utc
