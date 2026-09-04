"""Shared bootstrap for the cron entry points.

Importing config first is what loads .env, so every cron script starts with
`import cronlib` before touching anything else.
"""

import sys
from datetime import datetime, timezone

import config  # noqa: F401  — imported for its .env side effect

# Must be set before `from app import app`, which is what emits them.
config.QUIET_STARTUP = True


def log(msg):
    """Timestamped line into the cron log that /diagnostics tails."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def exit_unless_push_configured():
    """Leave quietly when push is switched off.

    Exit 0, not 1: an unconfigured dev box running the cron is not a failure,
    and a non-zero exit would have cron mailing about it every minute.
    """
    if not config.PUSH_CONFIGURED:
        print("VAPID keys not configured — skipping", flush=True)
        sys.exit(0)
