"""Static assets, the landing page, and the app shell."""

from flask import Blueprint, current_app, jsonify, make_response, redirect,     render_template, send_from_directory, session, url_for

from accounts import current_user_id, get_or_create_settings, require_session

bp = Blueprint("pages", __name__)


def _no_cache_js(body: str):
    resp = make_response(body)
    resp.headers["Content-Type"] = "application/javascript"
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@bp.route("/favicon.ico")
def favicon():
    return send_from_directory(current_app.static_folder, "favicon.ico")


@bp.route("/sw.js")
def service_worker():
    return _no_cache_js(current_app.config["SW_JS"])


@bp.route("/app.js")
def app_js():
    return _no_cache_js(current_app.config["APP_JS"])


@bp.route("/version")
def version():
    resp = jsonify({"version": current_app.config["APP_VERSION"]})
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/")
def landing():
    if session.get("user_id"):
        return redirect(url_for("pages.app_home"))
    return render_template("landing.html")


@bp.route("/app")
@require_session
def app_home():
    s = get_or_create_settings(current_user_id())
    return render_template("index.html", theme=s.theme,
                           version=current_app.config["APP_VERSION"])
