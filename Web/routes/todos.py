"""The todo API."""

from datetime import date, datetime, timezone

from flask import Blueprint, abort, jsonify, request

import scheduling
from accounts import current_user_id, require_api_key
from database import db
from models import Todo, UserSettings
from recurrence import first_due_date, needs_seed_date, next_due_date,     parse_recurrence, valid_time

bp = Blueprint("todos", __name__)


def _user_tz(user_id: int):
    """The owner's timezone, for anything that reasons about their calendar.

    Weekday recurrences are the reason this matters: the stored due_date is a
    UTC date, and east of UTC a morning todo sits on the previous UTC day, so
    weekday maths done on the raw column lands a day out. See scheduling.py.
    """
    s = db.session.get(UserSettings, user_id)
    return scheduling.resolve_tz(s.timezone if s else None)


def _seed_weekday_start(due_date, due_time, days_csv, tz):
    """Give a weekday recurrence its first occurrence, in the owner's calendar.

    Only called when no date was supplied, which is also the only case where
    due_time is a local wall clock rather than a UTC one — the client converts
    to UTC only when it sends both fields.
    """
    target = first_due_date(scheduling.today_in(tz), days_csv)
    return scheduling.to_storage(target, scheduling.parse_hhmm(due_time), tz)


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

    due_date = None
    if data.get("due_date"):
        try:
            due_date = date.fromisoformat(data["due_date"])
        except ValueError:
            return jsonify({"error": "due_date must be YYYY-MM-DD"}), 400

    due_time = data.get("due_time") or None
    if due_time and not valid_time(due_time):
        return jsonify({"error": "due_time must be HH:MM"}), 400

    notes = data.get("notes") or None

    interval, unit, days_csv, err = parse_recurrence(data)
    if err:
        return jsonify({"error": err}), 400

    # A weekday recurrence with no start date would sit undated forever: it never
    # lands in a day group and the notifier skips it. Seed the first occurrence.
    if due_date is None and needs_seed_date(unit, days_csv):
        due_date, due_time = _seed_weekday_start(
            due_date, due_time, days_csv, _user_tz(current_user_id()))

    todo = Todo(
        user_id=current_user_id(),
        title=title, due_date=due_date, due_time=due_time,
        notes=notes, recurrence_interval=interval, recurrence_unit=unit,
        recurrence_days=days_csv,
    )
    db.session.add(todo)
    db.session.commit()
    return jsonify(todo.to_dict()), 201


@bp.route("/todos/<int:todo_id>", methods=["PATCH"])
@require_api_key
def update_todo(todo_id):
    todo = Todo.query.filter_by(id=todo_id, user_id=current_user_id()).first()
    if todo is None:
        abort(404)

    data = request.get_json(silent=True) or {}

    if "title" in data:
        title = data["title"].strip()
        if not title:
            return jsonify({"error": "title cannot be empty"}), 400
        todo.title = title

    if "done" in data:
        new_done = bool(data["done"])
        if new_done and not todo.done and todo.recurrence_interval and todo.recurrence_unit:
            # Advance in the owner's calendar, not UTC: "every Mon, Wed, Fri"
            # is a statement about their week, and a timed todo's stored date
            # can be the day before the one they see.
            tz = _user_tz(todo.user_id)
            today_local = scheduling.today_in(tz)
            local_time = scheduling.local_time_of_day(todo.due_date, todo.due_time, tz)
            base = scheduling.local_date(todo.due_date, todo.due_time, tz) or today_local

            nxt = next_due_date(base, todo.recurrence_interval, todo.recurrence_unit, todo.recurrence_days)
            # Fast-forward past any overdue occurrences so the spawned instance is in the future.
            while nxt <= today_local:
                nxt = next_due_date(nxt, todo.recurrence_interval, todo.recurrence_unit, todo.recurrence_days)
            next_date, next_time = scheduling.to_storage(nxt, local_time, tz)

            db.session.add(Todo(
                user_id=todo.user_id,
                title=todo.title,
                notes=todo.notes,
                due_time=next_time,
                recurrence_interval=todo.recurrence_interval,
                recurrence_unit=todo.recurrence_unit,
                recurrence_days=todo.recurrence_days,
                due_date=next_date,
                spawned_from_id=todo.id,
            ))
        elif not new_done and todo.done and todo.recurrence_interval:
            child = Todo.query.filter_by(spawned_from_id=todo.id, done=False, user_id=current_user_id()).first()
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

    if "due_time" in data:
        due_time = data["due_time"] or None
        if due_time and not valid_time(due_time):
            return jsonify({"error": "due_time must be HH:MM"}), 400
        todo.due_time = due_time

    if "notes" in data:
        todo.notes = data["notes"] or None

    if "due_date" in data:
        if data["due_date"] is None:
            todo.due_date = None
        else:
            try:
                todo.due_date = date.fromisoformat(data["due_date"])
            except ValueError:
                return jsonify({"error": "due_date must be YYYY-MM-DD"}), 400

    # Same invariant as create_todo: a weekday recurrence always has a start date.
    if todo.due_date is None and needs_seed_date(todo.recurrence_unit, todo.recurrence_days):
        todo.due_date, todo.due_time = _seed_weekday_start(
            todo.due_date, todo.due_time, todo.recurrence_days, _user_tz(todo.user_id))

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
