"""Every environment-derived setting, read once, in one place.

Importing this module is what loads .env, so it must be imported before
anything that reads config — which in practice means it is the first local
import in app.py and in each cron entry point.

APP_VERSION deliberately does NOT live here: deploy.sh greps it straight out
of app.py to verify a deploy actually took, so it stays where that sed
expects it.
"""

import os
import sys

from dotenv import load_dotenv

_HERE = os.path.dirname(os.path.abspath(__file__))

# Two calls, because the web app and the cron scripts reach this file from
# different working directories. load_dotenv does not override values already
# in the environment, so the first call wins and the second only fills gaps;
# under systemd both are moot because EnvironmentFile has already populated
# the process environment. See docs/logs for why this is verified through
# /diagnostics rather than by reasoning about the path.
load_dotenv()
load_dotenv(os.path.join(_HERE, ".env"))


# --- Required. Absent means misconfigured, and failing at startup beats
# --- discovering it on the first request.
SECRET_KEY = os.environ["SECRET_KEY"]
API_KEY = os.environ["API_KEY"]

# --- Database
DEFAULT_DATABASE_URL = "sqlite:///" + os.path.join(_HERE, "markd.db")
DATABASE_URL = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)

# --- URLs
APP_URL = os.environ.get("APP_URL", "https://markd.appfoundry.cc").rstrip("/")

# --- Web push
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_CONTACT = os.environ.get("VAPID_CONTACT", "mailto:admin@example.com")

# --- Transactional mail. Sends from a dedicated subdomain so app-mail
# --- reputation is insulated from the root domain; see deploy/README.md.
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "Markd <markd@mail.appfoundry.cc>")
RESEND_WEBHOOK_SECRET = os.environ.get("RESEND_WEBHOOK_SECRET", "")

# --- One-time bootstrap of the first account, when converting a fresh install
# --- from the old single-password app. Read once and then irrelevant.
INITIAL_ADMIN_EMAIL = os.environ.get("INITIAL_ADMIN_EMAIL", "").strip().lower()
INITIAL_ADMIN_PASSWORD = os.environ.get("UI_PASSWORD", "")

# --- Signup CAPTCHA. Inert until both are set — see antispam.py layer 4.
# --- Read here rather than in antispam because app.py imports antispam first,
# --- so a module-level os.environ read there happens before .env is loaded.
# --- Under systemd that is masked by EnvironmentFile; anywhere else the keys
# --- would come back empty and the gate would silently stay off.
TURNSTILE_SITE_KEY = os.environ.get("TURNSTILE_SITE_KEY", "")
TURNSTILE_SECRET_KEY = os.environ.get("TURNSTILE_SECRET_KEY", "")

# --- Optional newline-delimited blocklist, so the list can grow without a deploy.
DISPOSABLE_DOMAINS_FILE = os.environ.get("DISPOSABLE_DOMAINS_FILE", "")

# --- Maintenance. A signup left unverified this long was never a person; the
# --- daily purge deletes it. See purge_stale_signups.py.
PURGE_UNVERIFIED_DAYS = int(os.environ.get("PURGE_UNVERIFIED_DAYS", "7"))

# --- Misc
NOTIFICATIONS_LOG = os.environ.get("NOTIFICATIONS_LOG", "/var/log/markd/notifications.log")
THEMES = {"indigo", "mint", "sunset", "berry", "slate"}

PUSH_CONFIGURED = bool(VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY)
EMAIL_CONFIGURED = bool(RESEND_API_KEY)


# Set by cronlib before it imports the app. The cron scripts build the whole
# Flask app once a minute, so without this every warning below is reprinted
# 1440 times a day into the same log /diagnostics tails 30 lines of — which is
# how the real "run:" lines stopped being visible. /diagnostics reports this
# same state in colour, so the log is not the only place it would be missed.
QUIET_STARTUP = False


def startup_warnings() -> list:
    """Human-readable warnings for anything switched off by missing config.

    Returned rather than printed so the caller decides where they go — the web
    app puts them on stderr at boot, tests just ignore them.
    """
    warnings = []
    if not PUSH_CONFIGURED:
        warnings.append("VAPID keys not configured — push notifications disabled.")
    if not EMAIL_CONFIGURED:
        warnings.append("RESEND_API_KEY not set — email verification and password reset disabled.")
    if not RESEND_WEBHOOK_SECRET:
        warnings.append("RESEND_WEBHOOK_SECRET not set — bounce/complaint suppression disabled.")
    return warnings


def warn(message: str) -> None:
    print(f"WARNING: {message}", file=sys.stderr, flush=True)
