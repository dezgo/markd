"""User settings: the daily overdue check, timezone, and theme."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, current_app, jsonify, render_template, request

from accounts import current_user_id, get_or_create_settings, require_session
from config import THEMES
from database import db
from recurrence import valid_time

bp = Blueprint("settings", __name__)


@bp.route("/settings")
@require_session
def settings_page():
    s = get_or_create_settings(current_user_id())
    return render_template("settings.html", settings=s,
                           version=current_app.config["APP_VERSION"])


@bp.route("/api/settings", methods=["GET"])
@require_session
def get_settings():
    s = get_or_create_settings(current_user_id())
    return jsonify(s.to_dict())


@bp.route("/api/settings", methods=["PATCH"])
@require_session
def update_settings():
    data = request.get_json(silent=True) or {}
    s = get_or_create_settings(current_user_id())

    if "overdue_check_enabled" in data:
        s.overdue_check_enabled = bool(data["overdue_check_enabled"])

    if "overdue_check_time" in data:
        t = data["overdue_check_time"]
        if not isinstance(t, str) or not valid_time(t):
            return jsonify({"error": "overdue_check_time must be HH:MM"}), 400
        s.overdue_check_time = t

    if "timezone" in data:
        tz = data["timezone"]
        if not isinstance(tz, str) or not tz:
            return jsonify({"error": "timezone must be a non-empty IANA name"}), 400
        try:
            ZoneInfo(tz)
        except ZoneInfoNotFoundError:
            return jsonify({"error": f"unknown timezone: {tz}"}), 400
        s.timezone = tz

    if "theme" in data:
        theme = data["theme"]
        if theme not in THEMES:
            return jsonify({"error": f"theme must be one of: {', '.join(sorted(THEMES))}"}), 400
        s.theme = theme

    db.session.commit()
    return jsonify(s.to_dict())
