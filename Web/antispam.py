"""Signup abuse defences.

Bot signups were mailing verification links to nonexistent and trap addresses,
which hard-bounced and dragged down the sending domain's reputation until real
mail started landing in spam. Everything here exists to make sure an address is
plausibly real, and the submitter plausibly human, *before* anything is sent.

The layers, cheapest first — a request must clear all of them:

  1. honeypot field + submit-timing trap   (free, catches naive form-fillers)
  2. per-IP and global rate limits         (caps the blast radius of anything else)
  3. address validation                    (syntax, disposable domains, MX lookup)
  4. Cloudflare Turnstile                  (only if keys are configured)

Layer 4 is inert until TURNSTILE_SITE_KEY/TURNSTILE_SECRET_KEY are set, so the
first three ship and work on their own.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from flask import request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

import config
from database import db
from models import RateEvent, SuppressedEmail

try:
    import dns.resolver
    _DNS_AVAILABLE = True
except ImportError:  # pragma: no cover - dnspython missing means MX checks are skipped
    _DNS_AVAILABLE = False
    print("WARNING: dnspython not installed — MX validation disabled.", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Mirrored from config so tests can patch them, but config is what reads the
# environment — see the note there about import order.
TURNSTILE_SITE_KEY = config.TURNSTILE_SITE_KEY
TURNSTILE_SECRET_KEY = config.TURNSTILE_SECRET_KEY
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

# Minimum seconds between the form being served and submitted. Humans take
# several seconds to type an email and a password; bots post instantly.
MIN_FORM_SECONDS = 2.5
# Signed form tokens expire after this, so a scraped form can't be replayed all day.
MAX_FORM_AGE_SECONDS = 3600

# (limit, window) per bucket.
#
# Two separate signup counters, because they defend different things:
#   - "attempt" counts every POST, and exists to stop hammering. It is loose,
#     so someone who fat-fingers their address a few times isn't locked out.
#   - "sent" counts only signups that actually put mail on the wire. It is the
#     one that protects the sending domain, so it is tight.
LIMIT_SIGNUP_ATTEMPT_IP_HOUR = (15, timedelta(hours=1))
LIMIT_SIGNUP_IP_HOUR = (3, timedelta(hours=1))
LIMIT_SIGNUP_IP_DAY = (10, timedelta(days=1))
# Login was the one auth route with no limit at all, which made it the cheapest
# way to test a stolen password list against real accounts. Two counters, for
# the two shapes of attack: many guesses from one source, and many guesses
# against one account from a botnet. Both count only failures, so a working
# session's normal re-logins never trip them.
LIMIT_LOGIN_IP_15MIN = (10, timedelta(minutes=15))
LIMIT_LOGIN_ACCOUNT_15MIN = (5, timedelta(minutes=15))
LIMIT_FORGOT_IP_HOUR = (5, timedelta(hours=1))
LIMIT_RESEND_IP_HOUR = (3, timedelta(hours=1))
# Circuit breaker: a distributed botnet defeats per-IP limits, but it cannot get
# past a cap on total outbound verification mail. If this trips, something is
# wrong and the domain is better served by sending nothing.
LIMIT_GLOBAL_MAIL_HOUR = (40, timedelta(hours=1))

RATE_EVENT_RETENTION = timedelta(days=2)


def is_turnstile_enabled() -> bool:
    return bool(TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY)


# ---------------------------------------------------------------------------
# Client identity
# ---------------------------------------------------------------------------

def client_ip() -> str:
    """Real client IP. Requires ProxyFix, since nginx proxies over a unix socket
    and request.remote_addr is otherwise meaningless."""
    return request.remote_addr or "unknown"


# ---------------------------------------------------------------------------
# Layer 1 — honeypot + timing
# ---------------------------------------------------------------------------

HONEYPOT_FIELD = "website"
FORM_TOKEN_FIELD = "form_token"
_FORM_SALT = "markd-form-token"


def _serializer():
    from flask import current_app
    return URLSafeTimedSerializer(current_app.secret_key, salt=_FORM_SALT)


def issue_form_token() -> str:
    """Signed 'this form was served at T' stamp, embedded in the form."""
    return _serializer().dumps({"t": time.time()})


def form_looks_human(form) -> bool:
    """False if the honeypot was filled, or the form was submitted implausibly
    fast, or the timing token is missing/forged/stale."""
    if (form.get(HONEYPOT_FIELD) or "").strip():
        return False

    token = form.get(FORM_TOKEN_FIELD) or ""
    if not token:
        return False
    try:
        payload = _serializer().loads(token, max_age=MAX_FORM_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return False

    elapsed = time.time() - float(payload.get("t", 0))
    return elapsed >= MIN_FORM_SECONDS


# ---------------------------------------------------------------------------
# Layer 2 — rate limiting
# ---------------------------------------------------------------------------

def count_since(key: str, since: datetime) -> int:
    """Rows recorded against `key` since `since`. Also the read model behind the
    /diagnostics counters."""
    return RateEvent.query.filter(RateEvent.key == key, RateEvent.created_at >= since).count()


def rate_exceeded(key: str, limit_window) -> bool:
    """True if `key` has already used its allowance for the window."""
    limit, window = limit_window
    since = datetime.now(timezone.utc) - window
    return count_since(key, since) >= limit


def record_event(key: str) -> None:
    db.session.add(RateEvent(key=key))
    db.session.commit()


def prune_rate_events() -> int:
    """Drop counters older than any window we care about. Cheap enough to call
    on the rare path where a limit is checked."""
    cutoff = datetime.now(timezone.utc) - RATE_EVENT_RETENTION
    deleted = RateEvent.query.filter(RateEvent.created_at < cutoff).delete()
    db.session.commit()
    return deleted


def signup_attempt_rate_ok(ip: str) -> bool:
    return not rate_exceeded(f"signup:attempt:{ip}", LIMIT_SIGNUP_ATTEMPT_IP_HOUR)


def signup_send_rate_ok(ip: str) -> bool:
    return not (
        rate_exceeded(f"signup:sent:{ip}", LIMIT_SIGNUP_IP_HOUR)
        or rate_exceeded(f"signup:sent:{ip}", LIMIT_SIGNUP_IP_DAY)
    )


def login_rate_ok(ip: str, email: str) -> bool:
    """False once this IP, or this account, has burned its failed-login budget."""
    return not (
        rate_exceeded(f"login:ip:{ip}", LIMIT_LOGIN_IP_15MIN)
        or rate_exceeded(f"login:acct:{email}", LIMIT_LOGIN_ACCOUNT_15MIN)
    )


def record_login_failure(ip: str, email: str) -> None:
    record_event(f"login:ip:{ip}")
    record_event(f"login:acct:{email}")


def global_mail_rate_ok() -> bool:
    return not rate_exceeded("mail:global", LIMIT_GLOBAL_MAIL_HOUR)


# ---------------------------------------------------------------------------
# Layer 3 — address validation
# ---------------------------------------------------------------------------

# Deliberately stricter than the old "anything@anything.anything": requires a
# sane local part and a TLD of at least two letters.
STRICT_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}"
    r"@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$"
)

# The high-volume throwaway providers. Not exhaustive by design — the MX check
# and rate limits carry the rest; this just makes the common case cheap.
DISPOSABLE_DOMAINS = {
    "0-mail.com", "10minutemail.com", "20minutemail.com", "33mail.com",
    "anonbox.net", "byom.de", "cock.li", "dispostable.com", "dropmail.me",
    "emailondeck.com", "emailtemporario.com.br", "fakeinbox.com", "fakemail.net",
    "getairmail.com", "getnada.com", "grr.la", "guerrillamail.biz",
    "guerrillamail.com", "guerrillamail.de", "guerrillamail.info",
    "guerrillamail.net", "guerrillamail.org", "guerrillamailblock.com",
    "harakirimail.com", "inboxbear.com", "inboxkitten.com", "jetable.org",
    "mail-temporaire.fr", "mail7.io", "mailcatch.com", "maildrop.cc",
    "mailinator.com", "mailnesia.com", "mailsac.com", "mailtemp.info",
    "mintemail.com", "moakt.com", "mohmal.com", "mytemp.email", "nada.email",
    "no-spam.ws", "opayq.com", "pokemail.net", "sharklasers.com", "shitmail.me",
    "spam4.me", "spambog.com", "spamgourmet.com", "spam.la", "tempail.com",
    "tempinbox.com", "tempmail.net", "tempmail.plus", "tempmailo.com",
    "tempr.email", "temp-mail.io", "temp-mail.org", "throwawaymail.com",
    "trashmail.com", "trashmail.de", "trashmail.me", "trbvm.com", "trashmail.net",
    "yopmail.com", "yopmail.fr", "yopmail.net", "zetmail.com",
}


def _load_extra_disposable() -> set:
    """Optional newline-delimited blocklist file, so the list can grow without
    a redeploy."""
    path = config.DISPOSABLE_DOMAINS_FILE
    if not path or not os.path.exists(path):
        return set()
    try:
        with open(path) as f:
            return {
                line.strip().lower() for line in f
                if line.strip() and not line.startswith("#")
            }
    except OSError as e:
        print(f"Could not read DISPOSABLE_DOMAINS_FILE {path}: {e}", file=sys.stderr, flush=True)
        return set()


_ALL_DISPOSABLE = DISPOSABLE_DOMAINS | _load_extra_disposable()

# domain -> (deliverable, checked_at). MX records rarely change and bots retry
# the same domains, so caching keeps signup fast and DNS traffic low.
_MX_CACHE = {}
_MX_CACHE_TTL = 6 * 3600
_DNS_TIMEOUT = 3.0


def domain_has_mail_exchanger(domain: str) -> bool:
    """True if `domain` can receive mail.

    Fails *open* on timeouts and resolver errors: a DNS blip must not lock real
    users out of signup. Only a definitive "this domain does not exist" or
    "no mail records" answer rejects.
    """
    if not _DNS_AVAILABLE:
        return True

    cached = _MX_CACHE.get(domain)
    if cached and (time.time() - cached[1]) < _MX_CACHE_TTL:
        return cached[0]

    resolver = dns.resolver.Resolver()
    resolver.timeout = _DNS_TIMEOUT
    resolver.lifetime = _DNS_TIMEOUT

    result = True
    try:
        answers = resolver.resolve(domain, "MX")
        # A null MX ("." with priority 0) is an explicit "accepts no mail".
        result = any(str(r.exchange).rstrip(".") for r in answers)
    except dns.resolver.NoAnswer:
        # No MX is legal — RFC 5321 falls back to the A/AAAA record.
        try:
            resolver.resolve(domain, "A")
            result = True
        except Exception:
            result = False
    except (dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
        result = False
    except Exception as e:
        print(f"MX lookup failed for {domain} (allowing): {e}", file=sys.stderr, flush=True)
        return True  # not cached — transient

    _MX_CACHE[domain] = (result, time.time())
    return result


def is_suppressed(email: str) -> bool:
    return db.session.get(SuppressedEmail, email.lower()) is not None


def suppress(email: str, reason: str, detail: str = None) -> None:
    email = email.lower()
    if db.session.get(SuppressedEmail, email) is None:
        db.session.add(SuppressedEmail(email=email, reason=reason, detail=detail))
        db.session.commit()


def validate_email_address(email: str):
    """Return None if the address is worth mailing, else a reason string.

    Reasons are for the log, not the user — see the note in app.signup about
    why the UI stays vague.
    """
    if not email or len(email) > 254 or not STRICT_EMAIL_RE.match(email):
        return "syntax"

    domain = email.rsplit("@", 1)[1].lower()
    if domain in _ALL_DISPOSABLE:
        return "disposable"
    if is_suppressed(email):
        return "suppressed"
    if not domain_has_mail_exchanger(domain):
        return "no-mx"
    return None


# ---------------------------------------------------------------------------
# Layer 4 — Cloudflare Turnstile
# ---------------------------------------------------------------------------

TURNSTILE_FIELD = "cf-turnstile-response"


def verify_turnstile(form, ip: str) -> bool:
    """Validate the Turnstile token server-side. No keys configured -> no gate."""
    if not is_turnstile_enabled():
        return True

    token = form.get(TURNSTILE_FIELD) or ""
    if not token:
        return False

    payload = urllib.parse.urlencode({
        "secret": TURNSTILE_SECRET_KEY,
        "response": token,
        "remoteip": ip,
    }).encode()
    req = urllib.request.Request(
        TURNSTILE_VERIFY_URL, data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        # Cloudflare unreachable. Fail *closed* — the other layers are still in
        # front of this, and silently disabling the CAPTCHA is how a gate that
        # looks fine ends up passing everything.
        print(f"Turnstile verify failed: {e}", file=sys.stderr, flush=True)
        return False

    if not body.get("success"):
        print(f"Turnstile rejected: {body.get('error-codes')}", file=sys.stderr, flush=True)
        return False
    return True
