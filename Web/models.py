from datetime import datetime, timezone
from database import db


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    email_verified = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class EmailToken(db.Model):
    __tablename__ = "email_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    purpose = db.Column(db.String(10), nullable=False)  # 'verify' | 'reset'
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class RateEvent(db.Model):
    """One row per rate-limited action. Counted over a sliding window.

    `key` is a namespaced bucket, e.g. "signup:ip:203.0.113.4", "signup:global",
    "forgot:ip:...". Rows older than the longest window are pruned opportunistically.
    DB-backed rather than in-process because gunicorn runs 2 workers, which would
    otherwise let every limit through at twice the intended rate.
    """
    __tablename__ = "rate_events"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(160), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           nullable=False, index=True)


class SuppressedEmail(db.Model):
    """Addresses we must never mail again — hard bounces and spam complaints.

    Fed by the Resend webhook. Checked before every send, so one bounce can't
    turn into repeated sends against the same dead mailbox.
    """
    __tablename__ = "suppressed_emails"

    email = db.Column(db.String(255), primary_key=True)
    reason = db.Column(db.String(32), nullable=False)  # 'bounced' | 'complained' | 'manual'
    detail = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class SchemaMigration(db.Model):
    """One row per data migration that has run.

    _ensure_columns handles adding columns, but a backfill must run exactly
    once: re-running one that reads a legacy column would resurrect values the
    user has since cleared. There was no record of what had run before this.
    """
    __tablename__ = "schema_migrations"

    name = db.Column(db.String(100), primary_key=True)
    applied_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class Todo(db.Model):
    __tablename__ = "todos"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    title = db.Column(db.String(500), nullable=False)
    done = db.Column(db.Boolean, default=False, nullable=False)

    # When a todo is due. At most one of these is ever set:
    #   due_at  — a UTC instant, for a todo with a time of day.
    #   due_on  — a plain local calendar date, for an all-day todo.
    # They replace the old due_date + due_time pair, where a single column
    # meant a *local* date on its own but a *UTC* date once a time was present.
    # That ambiguity is what put weekday recurrences on the wrong day.
    due_at = db.Column(db.DateTime, nullable=True, index=True)
    due_on = db.Column(db.Date, nullable=True, index=True)

    # Legacy. Frozen at the point of the due_at/due_on migration and no longer
    # read or written, so redeploying an older build still finds valid data.
    # Safe to drop once this release has bedded in.
    due_date = db.Column(db.Date, nullable=True)
    due_time = db.Column(db.String(5), nullable=True)

    notes = db.Column(db.Text, nullable=True)
    spawned_from_id = db.Column(db.Integer, nullable=True)
    recurrence_interval = db.Column(db.Integer, nullable=True)
    recurrence_unit = db.Column(db.String(20), nullable=True)
    recurrence_days = db.Column(db.String(15), nullable=True)  # CSV of JS weekday nums (Sun=0..Sat=6)
    notified_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def to_dict(self):
        # The API keeps the due_date + due_time shape the clients already speak.
        # Only the storage changed; splitting the instant back out here means
        # the PWA and the mobile apps need no coordinated release.
        if self.due_at is not None:
            due_date, due_time = self.due_at.date().isoformat(), self.due_at.strftime("%H:%M")
        elif self.due_on is not None:
            due_date, due_time = self.due_on.isoformat(), None
        else:
            due_date, due_time = None, None

        return {
            "id": self.id,
            "title": self.title,
            "done": self.done,
            "due_date": due_date,
            "due_time": due_time,
            "notes": self.notes,
            "spawned_from_id": self.spawned_from_id,
            "recurrence_interval": self.recurrence_interval,
            "recurrence_unit": self.recurrence_unit,
            "recurrence_days": self.recurrence_days,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class PushSubscription(db.Model):
    __tablename__ = "push_subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    endpoint = db.Column(db.Text, nullable=False, unique=True)
    p256dh = db.Column(db.Text, nullable=False)
    auth = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class UserSettings(db.Model):
    __tablename__ = "user_settings"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    overdue_check_enabled = db.Column(db.Boolean, default=True, nullable=False)
    overdue_check_time = db.Column(db.String(5), default="07:00", nullable=False)  # "HH:MM" in user's local TZ
    timezone = db.Column(db.String(64), default="UTC", nullable=False)  # IANA name
    last_overdue_check_date = db.Column(db.Date, nullable=True)  # last day a nag fired, in user's local TZ
    theme = db.Column(db.String(16), default="indigo", nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def to_dict(self):
        return {
            "overdue_check_enabled": self.overdue_check_enabled,
            "overdue_check_time": self.overdue_check_time,
            "timezone": self.timezone,
            "theme": self.theme,
        }
