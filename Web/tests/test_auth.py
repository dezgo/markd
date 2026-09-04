"""Login, and the rate limit in front of it."""

import pytest

GOOD = {"email": "admin@example.com", "password": "test-password-123"}


@pytest.fixture(autouse=True)
def clear_rate_events(flask_app):
    """Each test starts with a fresh budget; the counters are DB rows."""
    from database import db
    from models import RateEvent
    with flask_app.app_context():
        RateEvent.query.delete()
        db.session.commit()


def _post(client, **over):
    return client.post("/login", data={**GOOD, **over})


def test_good_credentials_log_in(client):
    r = _post(client)
    assert r.status_code == 302
    with client.session_transaction() as s:
        assert s.get("user_id") == 1


def test_bad_password_is_rejected(client):
    r = _post(client, password="wrong")
    assert r.status_code == 200
    assert "Incorrect email or password" in r.get_data(as_text=True)
    with client.session_transaction() as s:
        assert "user_id" not in s


def test_login_is_rate_limited_by_account(client):
    """Five failures against one account, then it stops answering."""
    for _ in range(5):
        assert "Incorrect email or password" in _post(client, password="x").get_data(as_text=True)

    body = _post(client, password="x").get_data(as_text=True)
    assert "Too many failed attempts" in body


def test_the_limit_blocks_the_correct_password_too(client):
    """Otherwise the lockout is trivially bypassed by the guess that matters."""
    for _ in range(5):
        _post(client, password="x")
    assert "Too many failed attempts" in _post(client).get_data(as_text=True)
    with client.session_transaction() as s:
        assert "user_id" not in s


def test_successful_logins_do_not_consume_the_budget(client):
    for _ in range(8):
        assert _post(client).status_code == 302
    assert _post(client).status_code == 302


def test_a_different_account_has_its_own_budget(client, flask_app):
    """Per-account counters must not let one target lock out another."""
    for _ in range(5):
        _post(client, email="someone@example.com", password="x")
    # The original account is untouched by that: only the shared IP counter has
    # ticked, and it allows more than five.
    assert "Too many failed attempts" not in _post(client, password="x").get_data(as_text=True)


def test_ip_budget_catches_spraying_across_accounts(client):
    """Ten failures from one source, spread over ten different accounts, still
    trips — that is the shape a credential-stuffing run has."""
    for i in range(10):
        _post(client, email=f"user{i}@example.com", password="x")
    assert "Too many failed attempts" in _post(client, password="x").get_data(as_text=True)


def test_logout_clears_the_session(client):
    _post(client)
    client.get("/logout")
    with client.session_transaction() as s:
        assert "user_id" not in s


# ---------------------------------------------------------------------------
# Email verification — the link a mail scanner must not be able to trigger
# ---------------------------------------------------------------------------

def _pending_user(flask_app, email="pending@example.com"):
    """An unverified account plus a live verification token."""
    from database import db
    from models import User
    from mail import make_token, VERIFY_TOKEN_HOURS
    from werkzeug.security import generate_password_hash
    with flask_app.app_context():
        u = User.query.filter_by(email=email).first()
        if u is None:
            u = User(email=email, password_hash=generate_password_hash("pw-12345678"),
                     email_verified=False)
            db.session.add(u)
            db.session.commit()
        u.email_verified = False
        db.session.commit()
        return u.id, make_token(u.id, "verify", hours=VERIFY_TOKEN_HOURS)


def _is_verified(flask_app, uid):
    from database import db
    from models import User
    with flask_app.app_context():
        return db.session.get(User, uid).email_verified


def test_fetching_the_link_does_not_verify_anything(client, flask_app):
    """The bug that produced 366 phantom "verified" accounts: corporate mail
    gateways GET every URL in a message to scan it."""
    uid, token = _pending_user(flask_app)
    r = client.get(f"/verify/{token}")
    assert r.status_code == 200
    assert not _is_verified(flask_app, uid), "a scanner's GET verified the account"


def test_fetching_the_link_does_not_spend_the_token(client, flask_app):
    """A scanner must not burn the link before the recipient opens the mail."""
    uid, token = _pending_user(flask_app)
    client.get(f"/verify/{token}")
    client.get(f"/verify/{token}")
    assert client.post(f"/verify/{token}").status_code == 302
    assert _is_verified(flask_app, uid)


def test_posting_the_confirmation_verifies(client, flask_app):
    uid, token = _pending_user(flask_app)
    r = client.post(f"/verify/{token}")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert _is_verified(flask_app, uid)


def test_the_confirm_page_offers_a_post_form(client, flask_app):
    _, token = _pending_user(flask_app)
    body = client.get(f"/verify/{token}").get_data(as_text=True)
    assert 'method="post"' in body.lower()
    assert token in body


def test_a_token_cannot_be_reused(client, flask_app):
    uid, token = _pending_user(flask_app)
    assert client.post(f"/verify/{token}").status_code == 302
    body = client.post(f"/verify/{token}").get_data(as_text=True)
    assert "invalid or has expired" in body


def test_an_unknown_token_is_rejected_on_both_methods(client):
    for call in (client.get, client.post):
        body = call("/verify/not-a-real-token").get_data(as_text=True)
        assert "invalid or has expired" in body


def test_an_expired_token_is_rejected(client, flask_app):
    from database import db
    from models import EmailToken
    from datetime import datetime, timedelta, timezone
    uid, token = _pending_user(flask_app)
    with flask_app.app_context():
        rec = EmailToken.query.filter_by(token=token).first()
        rec.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.session.commit()
    assert "invalid or has expired" in client.get(f"/verify/{token}").get_data(as_text=True)
    assert not _is_verified(flask_app, uid)


def test_an_already_verified_link_just_sends_you_to_login(client, flask_app):
    """Clicking the mail twice should not look like a failure."""
    _, token = _pending_user(flask_app)
    client.post(f"/verify/{token}")
    _, token2 = _pending_user(flask_app)
    client.post(f"/verify/{token2}")
    r = client.get(f"/verify/{token2}")
    assert r.status_code in (200, 302)


def test_a_reset_link_is_not_spent_by_a_scanner(client, flask_app):
    """Same exposure on the other emailed link."""
    from mail import make_token, RESET_TOKEN_HOURS
    uid, _ = _pending_user(flask_app, "resetter@example.com")
    with flask_app.app_context():
        token = make_token(uid, "reset", hours=RESET_TOKEN_HOURS)
    client.get(f"/reset-password/{token}")
    r = client.post(f"/reset-password/{token}", data={"password": "brand-new-pw-1"})
    assert r.status_code == 302, "the GET consumed the reset token"
