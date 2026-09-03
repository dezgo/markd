"""Web push delivery, shared by the app and both cron senders.

The bundling rule lives here because it is a product rule, not a detail of
either caller: Markd never sends a user more than one push at a time. Anything
that would be several notifications is collapsed into one before it reaches
the transport, so this module deliberately exposes no "send one push per
todo" entry point.

Dead-subscription pruning also lives here. A 404 or 410 from the push service
means the browser threw the subscription away, and re-sending to it forever is
how a subscription table fills with corpses.
"""

import json
import sys

from pywebpush import WebPushException, webpush

from config import PUSH_CONFIGURED, VAPID_CONTACT, VAPID_PRIVATE_KEY
from database import db
from models import PushSubscription

# How many task titles to name before falling back to "+N more".
MAX_TITLES_SHOWN = 5

# Store-and-forward for a day. FCM/APNs hold the message while the device is
# asleep or offline, so a push raised at 07:00 still lands when the phone wakes
# at 08:00. Without this the default is deliver-now-or-drop.
DEFAULT_TTL = 86400


def _default_log(msg):
    print(msg, file=sys.stderr, flush=True)


def bundle_body(titles) -> str:
    """"Feed cat, Take bins, +3 more" — the one-line summary of a bundle."""
    shown = list(titles)[:MAX_TITLES_SHOWN]
    body = ", ".join(shown)
    if len(titles) > MAX_TITLES_SHOWN:
        body += f", +{len(titles) - MAX_TITLES_SHOWN} more"
    return body


def build_payload(title: str, titles, tag: str) -> str:
    """A single notification covering everything in `titles`."""
    return json.dumps({"title": title, "body": bundle_body(titles), "tag": tag})


def send_to_user(user_id: int, payload: str, tag_for_log: str = "",
                 ttl: int = DEFAULT_TTL, urgency: str = "normal", log=_default_log):
    """Deliver one payload to every live subscription a user has.

    Returns (delivered, total_subscriptions). A user with no subscriptions is
    (0, 0) — which callers must distinguish from (0, n), because the first is
    "nowhere to send it" and the second is "we failed, try again next tick".

    Subscriptions the push service reports as gone are deleted; the caller is
    expected to commit.
    """
    if not PUSH_CONFIGURED:
        log("  push not configured — nothing sent")
        return 0, 0

    subs = PushSubscription.query.filter_by(user_id=user_id).all()
    if not subs:
        return 0, 0

    delivered = 0
    dead = []
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_CONTACT},
                ttl=ttl,
                headers={"Urgency": urgency},
            )
            delivered += 1
        except WebPushException as exc:
            code = exc.response.status_code if exc.response is not None else "?"
            if code in (404, 410):
                dead.append(sub)
                log(f"  sub {sub.id}: gone ({code}) — removing")
            else:
                log(f"  sub {sub.id}: FAIL ({code}) {exc}")
        except Exception as exc:
            log(f"  sub {sub.id}: ERROR {exc!r}")

    for sub in dead:
        db.session.delete(sub)

    return delivered, len(subs)
