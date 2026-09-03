"""Resend delivery webhook — bounce and complaint suppression."""

import base64
import hashlib
import hmac
import json
import sys
from datetime import datetime, timezone

from flask import Blueprint, abort, jsonify, request

import antispam
from accounts import delete_user
from config import RESEND_WEBHOOK_SECRET
from database import db
from models import User

bp = Blueprint("webhooks", __name__)


def _verify_svix_signature(raw_body: bytes) -> bool:
    """Verify Resend's Svix-signed webhook.

    Signed content is "{id}.{timestamp}.{body}", HMAC-SHA256 with the secret's
    base64 payload, compared against any v1 signature in the header.
    """
    if not RESEND_WEBHOOK_SECRET:
        return False

    msg_id = request.headers.get("svix-id", "")
    timestamp = request.headers.get("svix-timestamp", "")
    sig_header = request.headers.get("svix-signature", "")
    if not (msg_id and timestamp and sig_header):
        return False

    # Reject replays of an old, legitimately-signed delivery.
    try:
        age = abs(datetime.now(timezone.utc).timestamp() - int(timestamp))
    except ValueError:
        return False
    if age > 300:
        return False

    secret = RESEND_WEBHOOK_SECRET
    if secret.startswith("whsec_"):
        secret = secret[len("whsec_"):]
    try:
        key = base64.b64decode(secret)
    except Exception:
        return False

    signed = f"{msg_id}.{timestamp}.".encode() + raw_body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()

    # Header holds space-separated "v1,<sig>" entries — Svix sends more than one
    # during a secret rotation.
    for part in sig_header.split():
        version, _, sig = part.partition(",")
        if version == "v1" and hmac.compare_digest(sig, expected):
            return True
    return False


@bp.route("/webhooks/resend", methods=["POST"])
def resend_webhook():
    raw = request.get_data()
    if not _verify_svix_signature(raw):
        abort(401)

    try:
        event = json.loads(raw.decode())
    except (ValueError, UnicodeDecodeError):
        abort(400)

    event_type = event.get("type", "")
    data = event.get("data") or {}
    recipients = data.get("to") or []
    if isinstance(recipients, str):
        recipients = [recipients]

    if event_type == "email.bounced":
        # Transient bounces (full mailbox, greylisting) recover on their own —
        # suppressing those would lock out real users.
        bounce_type = ((data.get("bounce") or {}).get("type") or "").lower()
        if bounce_type and bounce_type != "permanent":
            print(f"Resend {bounce_type} bounce for {recipients} — not suppressing",
                  file=sys.stderr, flush=True)
            return jsonify({"ok": True, "action": "ignored-transient"})
        reason = "bounced"
    elif event_type == "email.complained":
        reason = "complained"
    else:
        return jsonify({"ok": True, "action": "ignored"})

    for addr in recipients:
        addr = (addr or "").strip().lower()
        if not addr:
            continue
        antispam.suppress(addr, reason, detail=json.dumps(data.get("bounce") or {})[:500])
        # A hard bounce on an unverified account means the address was never
        # real. Drop the row so the junk doesn't accumulate.
        user = User.query.filter_by(email=addr).first()
        if user and not user.email_verified:
            delete_user(user)
            db.session.commit()
            print(f"Removed unverified user after {reason}: {addr}", file=sys.stderr, flush=True)
        else:
            print(f"Suppressed {addr} ({reason})", file=sys.stderr, flush=True)

    return jsonify({"ok": True, "action": reason})
