from datetime import datetime, timezone
from database import db

RECURRENCE_UNITS = {"days", "weeks", "months", "years"}


class Todo(db.Model):
    __tablename__ = "todos"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(500), nullable=False)
    done = db.Column(db.Boolean, default=False, nullable=False)
    due_date = db.Column(db.Date, nullable=True)
    due_time = db.Column(db.String(5), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    spawned_from_id = db.Column(db.Integer, nullable=True)
    recurrence_interval = db.Column(db.Integer, nullable=True)
    recurrence_unit = db.Column(db.String(10), nullable=True)
    notified_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "done": self.done,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "due_time": self.due_time,
            "notes": self.notes,
            "spawned_from_id": self.spawned_from_id,
            "recurrence_interval": self.recurrence_interval,
            "recurrence_unit": self.recurrence_unit,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class PushSubscription(db.Model):
    __tablename__ = "push_subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    endpoint = db.Column(db.Text, nullable=False, unique=True)
    p256dh = db.Column(db.Text, nullable=False)
    auth = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
