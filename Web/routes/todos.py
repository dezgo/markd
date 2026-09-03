"""The todo API.

The wire format still speaks due_date + due_time, because the PWA and the
mobile clients do. Storage does not: everything below converts that pair into
the unambiguous due_at / due_on columns as early as possible, and back only in
Todo.to_dict. See scheduling.py for why the pair was a trap.
"""

from datetime import date, datetime, timezone

from flask import Blueprint, abort, jsonify, request

import scheduling
from accounts import current_user_id, require_api_key
from database import db
from models import Todo, UserSettings
from recurrence import first_due_date, needs_seed_date, next_due_date, \
    parse_recurrence, valid_time

bp = Blueprint("todos", __name__)


def _user_tz(user_id: int):
    """The owner's timezone, for anything that reasons about their calendar."""
    s = db.session.get(UserSettings, user_id)
    return scheduling.resolve_tz(s.timezone if s else None)


class _BadRequest(Exception):
    def __init__(self, message):
        self.message = message


def _read_due(data, key_date="due_date", key_time="due_time"):
    """Pull and validate the due pair out of a payload.

    Returns (date | None, time | None). Raises _BadRequest on malformed input.
    """
    due_date = None
    if data.get(key_date):
        try:
            due_date = date.fromisoformat(data[key_date])
        except (TypeError, ValueError):
            raise _BadRequest("due_date must be YYYY-MM-DD")

    raw_time = data.get(key_time) or None
    if raw_time and not valid_time(raw_time):
        raise _BadRequest("due_time must be HH:MM")
    return due_date, scheduling.parse_hhmm(raw_time)


def _due_from_api(due_date, due_time, tz):
    """(due_at, due_on) for the pair a client sent.

    The client's three shapes mean three different things:
      date + time  the client already converted to UTC, so it is an instant
      date only    an all-day todo on that local date
      time only    a floating time, resolved to its next occurrence
    """
    if due_date and due_time:
        return datetime.combine(due_date, due_time), None
    if due_date:
        return None, due_date
    if due_time:
        return scheduling.due_fields(scheduling.next_occurrence_of(due_time, tz),
                                     due_time, tz)
    return None, None


def _seed_weekday_start(todo_or_time, days_csv, tz):
    """First occurrence for a weekday recurrence that was given no start date."""
    target = first_due_date(scheduling.today_in(tz), days_csv)
    return scheduling.due_fields(target, todo_or_time, tz)


@bp.route("/todos", methods=["GET"])
@require_api_key
def get_todos():
    todos = Todo.query.filter_by(user_id=current_user_id()).order_by(Todo.created_at.desc()).all()
    return jsonify([t.to_dict() for t in todos])


@bp.route("/todos", methods=["POST"])
@require_api_key
def create_todo():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400

    try:
        due_date, due_time = _read_due(data)
    except _BadRequest as e:
        return jsonify({"error": e.message}), 400

    interval, unit, days_csv, err = parse_recurrence(data)
    if err:
        return jsonify({"error": err}), 400

    uid = current_user_id()
    tz = _user_tz(uid)
    due_at, due_on = _due_from_api(due_date, due_time, tz)

    # A weekday recurrence with no start date would sit undated forever: it
    # never lands in a day group and the notifier skips it.
    if due_at is None and due_on is None and needs_seed_date(unit, days_csv):
        due_at, due_on = _seed_weekday_start(due_time, days_csv, tz)

    todo = Todo(
        user_id=uid, title=title, due_at=due_at, due_on=due_on,
        notes=data.get("notes") or None,
        recurrence_interval=interval, recurrence_unit=unit, recurrence_days=days_csv,
    )
    db.session.add(todo)
    db.session.commit()
    return jsonify(todo.to_dict()), 201


def _spawn_next(todo, tz):
    """The follow-up instance for a recurring todo that was just completed.

    Advances in the owner's calendar rather than UTC: "every Mon, Wed, Fri" is
    a statement about their week, and a timed todo's stored instant can fall on
    the previous UTC day.
    """
    today_local = scheduling.today_in(tz)
    local_time = scheduling.local_time_of(todo.due_at, tz)
    base = scheduling.local_date_of(todo.due_at, todo.due_on, tz) or today_local

    nxt = next_due_date(base, todo.recurrence_interval, todo.recurrence_unit,
                        todo.recurrence_days)
    # Fast-forward past any missed occurrences so the new one is in the future.
    while nxt <= today_local:
        nxt = next_due_date(nxt, todo.recurrence_interval, todo.recurrence_unit,
                            todo.recurrence_days)

    due_at, due_on = scheduling.due_fields(nxt, local_time, tz)
    return Todo(
        user_id=todo.user_id, title=todo.title, notes=todo.notes,
        due_at=due_at, due_on=due_on,
        recurrence_interval=todo.recurrence_interval,
        recurrence_unit=todo.recurrence_unit,
        recurrence_days=todo.recurrence_days,
        spawned_from_id=todo.id,
    )


@bp.route("/todos/<int:todo_id>", methods=["PATCH"])
@require_api_key
def update_todo(todo_id):
    todo = Todo.query.filter_by(id=todo_id, user_id=current_user_id()).first()
    if todo is None:
        abort(404)

    data = request.get_json(silent=True) or {}
    tz = _user_tz(todo.user_id)

    if "title" in data:
        title = (data["title"] or "").strip()
        if not title:
            return jsonify({"error": "title cannot be empty"}), 400
        todo.title = title

    if "done" in data:
        new_done = bool(data["done"])
        if new_done and not todo.done and todo.recurrence_interval and todo.recurrence_unit:
            db.session.add(_spawn_next(todo, tz))
        elif not new_done and todo.done and todo.recurrence_interval:
            child = Todo.query.filter_by(spawned_from_id=todo.id, done=False,
                                         user_id=current_user_id()).first()
            if child:
                db.session.delete(child)
        todo.done = new_done

    if "recurrence_interval" in data or "recurrence_unit" in data or "recurrence_days" in data:
        interval, unit, days_csv, err = parse_recurrence(data)
        if err:
            return jsonify({"error": err}), 400
        todo.recurrence_interval = interval
        todo.recurrence_unit = unit
        todo.recurrence_days = days_csv

    if "notes" in data:
        todo.notes = data["notes"] or None

    # The due pair is resolved as a unit. Clients send both keys together, and
    # a half-applied update — a new date against yesterday's time — is exactly
    # the sort of state the old field-at-a-time handling could produce.
    if "due_date" in data or "due_time" in data:
        try:
            due_date, due_time = _read_due(data)
        except _BadRequest as e:
            return jsonify({"error": e.message}), 400
        todo.due_at, todo.due_on = _due_from_api(due_date, due_time, tz)

    # Same invariant as create_todo.
    if todo.due_at is None and todo.due_on is None \
            and needs_seed_date(todo.recurrence_unit, todo.recurrence_days):
        todo.due_at, todo.due_on = _seed_weekday_start(
            scheduling.parse_hhmm(data.get("due_time")), todo.recurrence_days, tz)

    todo.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(todo.to_dict())


@bp.route("/todos/<int:todo_id>", methods=["DELETE"])
@require_api_key
def delete_todo(todo_id):
    todo = Todo.query.filter_by(id=todo_id, user_id=current_user_id()).first()
    if todo is None:
        abort(404)
    db.session.delete(todo)
    db.session.commit()
    return "", 204
