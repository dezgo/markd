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
