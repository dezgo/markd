"""Who is making this request, what they may do, and account lifecycle."""

from functools import wraps

from flask import abort, g, redirect, request, session, url_for

from config import API_KEY
from database import db
from models import EmailToken, PushSubscription, Todo, User, UserSettings

# Every table that hangs off a user. Deleting an account means clearing all of
# them; there were two implementations of this and they cleared different
# subsets, so the webhook path left settings, todos and push subscriptions
# orphaned while the purge cron cleared them.
USER_OWNED = (EmailToken, PushSubscription, UserSettings, Todo)


def current_user_id():
    if "_user_id" in g:
        return g._user_id
    return session.get("user_id")


def current_user():
    uid = current_user_id()
    if uid:
        return db.session.get(User, uid)
    return None


def require_session(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login", next=request.path))
        return f(*args, **kwargs)
    return decorated


def require_api_key(f):
    """Accept either a logged-in session or the API key header.

    API key requests act as the initial admin (user_id=1) for backwards compat.
    Per-user API keys can come later.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("user_id"):
            return f(*args, **kwargs)
        key = request.headers.get("X-API-Key") or request.args.get("api_key")
        if key != API_KEY:
            abort(401)
        g._user_id = 1
        return f(*args, **kwargs)
    return decorated


# The initial admin. There is no roles table and no need for one on a personal
# instance; user 1 is who the bootstrap creates and who API-key requests act as.
ADMIN_USER_ID = 1


def is_admin() -> bool:
    return current_user_id() == ADMIN_USER_ID


def require_admin(f):
    """Admin-only page. Answers 404 rather than 403 to anyone else, so the
    route's existence is not advertised to ordinary accounts."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login", next=request.path))
        if not is_admin():
            abort(404)
        return f(*args, **kwargs)
    return decorated


def get_or_create_settings(user_id: int) -> UserSettings:
    s = db.session.get(UserSettings, user_id)
    if s is None:
        s = UserSettings(user_id=user_id)
        db.session.add(s)
        db.session.commit()
    return s


def delete_user(user: User) -> None:
    """Remove an account and everything belonging to it.

    Does not commit — the caller decides the transaction boundary, since both
    callers delete in a loop.
    """
    for model in USER_OWNED:
        model.query.filter_by(user_id=user.id).delete()
    db.session.delete(user)
