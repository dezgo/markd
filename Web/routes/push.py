"""Push subscription management."""

from flask import Blueprint, jsonify, request

from accounts import current_user_id, require_session
from config import VAPID_PUBLIC_KEY
from database import db
from models import PushSubscription

bp = Blueprint("push", __name__)


@bp.route("/push/vapid-public-key")
@require_session
def push_vapid_key():
    return jsonify({"publicKey": VAPID_PUBLIC_KEY})


@bp.route("/push/subscribe", methods=["POST"])
@require_session
def push_subscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")
    keys = data.get("keys") or {}
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    if not endpoint or not p256dh or not auth:
        return jsonify({"error": "endpoint, keys.p256dh, and keys.auth are required"}), 400

    sub = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if sub:
        sub.user_id = current_user_id()
        sub.p256dh = p256dh
        sub.auth = auth
    else:
        db.session.add(PushSubscription(
            user_id=current_user_id(),
            endpoint=endpoint, p256dh=p256dh, auth=auth,
        ))
    db.session.commit()
    return "", 204


@bp.route("/push/subscribe", methods=["DELETE"])
@require_session
def push_unsubscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")
    if endpoint:
        PushSubscription.query.filter_by(endpoint=endpoint, user_id=current_user_id()).delete()
        db.session.commit()
    return "", 204
