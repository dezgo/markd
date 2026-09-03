"""Outbound transactional email: sending, templating, and signed links.

Every send goes through send_email(), which is the single choke point where
the suppression list and the global hourly cap are enforced. No code path,
present or future, gets to mail a known-bad address or flood the domain by
going around it.
"""

import sys
from datetime import datetime, timedelta, timezone

import resend

import antispam
from config import APP_URL, EMAIL_FROM, RESEND_API_KEY
from database import db
from models import EmailToken, User

if RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY

# Link lifetimes. The email copy is generated from these — it previously
# repeated them as prose and drifted, promising a 30 minute reset link that
# actually lasted an hour.
VERIFY_TOKEN_HOURS = 24
RESET_TOKEN_HOURS = 1


def _hours(n: int) -> str:
    return "1 hour" if n == 1 else f"{n} hours"


def send_email(to: str, subject: str, html: str):
    """Single choke point for outbound mail.

    Every send passes the suppression list and the global hourly cap, so no code
    path — present or future — can mail a known-bad address or flood the domain.
    """
    if not RESEND_API_KEY:
        print(f"(would send email to {to}: {subject})", file=sys.stderr, flush=True)
        return False

    if antispam.is_suppressed(to):
        print(f"Suppressed address, not sending to {to}: {subject}", file=sys.stderr, flush=True)
        return False

    if not antispam.global_mail_rate_ok():
        print(
            f"GLOBAL MAIL CAP HIT — dropping send to {to} ({subject}). "
            "Something is generating mail in bulk; check /diagnostics.",
            file=sys.stderr, flush=True,
        )
        return False

    try:
        resend.Emails.send({"from": EMAIL_FROM, "to": [to], "subject": subject, "html": html})
        antispam.record_event("mail:global")
        return True
    except Exception as e:
        print(f"Email send failed for {to}: {e}", file=sys.stderr, flush=True)
        return False


def make_token(user_id: int, purpose: str, hours: int) -> str:
    token = secrets.token_urlsafe(32)
    db.session.add(EmailToken(
        user_id=user_id,
        token=token,
        purpose=purpose,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=hours),
    ))
    db.session.commit()
    return token


def consume_token(token: str, purpose: str) -> User:
    """Return the user if the token is valid and unused, else None. Marks it used."""
    rec = EmailToken.query.filter_by(token=token, purpose=purpose).first()
    if not rec or rec.used_at is not None:
        return None
    if rec.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        return None
    rec.used_at = datetime.now(timezone.utc)
    user = db.session.get(User, rec.user_id)
    db.session.commit()
    return user


def _email_layout(preview: str, heading: str, intro: str, button_label: str,
                  button_url: str, expiry_note: str, extra_note: str = "") -> str:
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f4f5fb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#111827;">
<span style="display:none;font-size:0;line-height:0;max-height:0;max-width:0;opacity:0;overflow:hidden;">{preview}</span>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f4f5fb;padding:40px 16px;">
  <tr><td align="center">
    <table role="presentation" width="520" cellspacing="0" cellpadding="0" border="0" style="background:#ffffff;border-radius:14px;box-shadow:0 4px 20px rgba(99,102,241,0.08);overflow:hidden;max-width:520px;width:100%;">
      <tr><td style="padding:32px 32px 0;text-align:center;">
        <span style="display:inline-block;font-size:26px;font-weight:700;color:#6366f1;letter-spacing:-0.5px;">Markd</span>
      </td></tr>
      <tr><td style="padding:24px 36px 36px;">
        <h1 style="margin:0 0 12px;font-size:20px;font-weight:600;color:#111827;text-align:center;">{heading}</h1>
        <p style="margin:0 0 28px;color:#6b7280;font-size:15px;line-height:1.55;text-align:center;">{intro}</p>
        <p style="text-align:center;margin:0 0 28px;">
          <a href="{button_url}" style="display:inline-block;background:#6366f1;color:#ffffff;text-decoration:none;padding:13px 32px;border-radius:10px;font-size:15px;font-weight:600;">{button_label}</a>
        </p>
        <p style="margin:0 0 6px;color:#9ca3af;font-size:12px;text-align:center;">Or paste this link:</p>
        <p style="margin:0 0 18px;font-size:12px;word-break:break-all;text-align:center;"><a href="{button_url}" style="color:#6366f1;text-decoration:none;">{button_url}</a></p>
        <p style="margin:0;color:#9ca3af;font-size:12px;text-align:center;line-height:1.5;">{expiry_note}{(' ' + extra_note) if extra_note else ''}</p>
      </td></tr>
    </table>
    <p style="margin:16px 0 0;color:#9ca3af;font-size:11px;text-align:center;">Markd · markd.appfoundry.cc</p>
  </td></tr>
</table>
</body>
</html>"""


def send_verification_email(user: User):
    token = make_token(user.id, "verify", hours=VERIFY_TOKEN_HOURS)
    url = f"{APP_URL}/verify/{token}"
    html = _email_layout(
        preview="Verify your email to start using Markd.",
        heading="Verify your email",
        intro="Click the button below to confirm your email and finish setting up your Markd account.",
        button_label="Verify my email",
        button_url=url,
        expiry_note=f"This link expires in {_hours(VERIFY_TOKEN_HOURS)}.",
    )
    return send_email(user.email, "Verify your Markd email", html)


def send_reset_email(user: User):
    token = make_token(user.id, "reset", hours=RESET_TOKEN_HOURS)
    url = f"{APP_URL}/reset-password/{token}"
    html = _email_layout(
        preview="Reset your Markd password.",
        heading="Reset your password",
        intro="Someone (hopefully you) requested a password reset for your Markd account. Click below to set a new one.",
        button_label="Set new password",
        button_url=url,
        expiry_note=f"This link expires in {_hours(RESET_TOKEN_HOURS)}.",
        extra_note="If you didn't request this, just ignore the email.",
    )
    return send_email(user.email, "Reset your Markd password", html)
