"""Layer 4 of the signup gate.

Never ran in production — the keys were never set — so this covers it before
it is switched on. Verified once against Cloudflare's documented test keys
(1x…AA always passes, 2x…AA always fails); these run offline.
"""

import json
import urllib.error

import pytest

import antispam


@pytest.fixture(autouse=True)
def clear_rate_events(flask_app):
    """Signup counters are DB rows, and several tests here post to /signup.
    Without this the later ones trip the per-IP limit and fail for a reason
    that has nothing to do with the CAPTCHA."""
    from database import db
    from models import RateEvent
    with flask_app.app_context():
        RateEvent.query.delete()
        db.session.commit()


@pytest.fixture
def keys(monkeypatch):
    """Turnstile switched on, without touching the network."""
    monkeypatch.setattr(antispam, "TURNSTILE_SITE_KEY", "1x00000000000000000000AA")
    monkeypatch.setattr(antispam, "TURNSTILE_SECRET_KEY", "1x0000000000000000000000000000000AA")


@pytest.fixture
def cloudflare(monkeypatch):
    """Stand in for the siteverify endpoint. Returns the recorded request."""
    calls = []

    class _Resp:
        def __init__(self, body):
            self._body = json.dumps(body).encode()

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _install(body=None, raises=None):
        def _urlopen(req, timeout=None):
            calls.append(req)
            if raises:
                raise raises
            return _Resp(body)
        monkeypatch.setattr(antispam.urllib.request, "urlopen", _urlopen)
        return calls

    return _install


def test_no_keys_means_no_gate(monkeypatch):
    monkeypatch.setattr(antispam, "TURNSTILE_SITE_KEY", "")
    monkeypatch.setattr(antispam, "TURNSTILE_SECRET_KEY", "")
    assert not antispam.is_turnstile_enabled()
    assert antispam.verify_turnstile({}, "1.2.3.4") is True


def test_one_key_alone_does_not_switch_it_on(monkeypatch):
    """A half-configured gate must not look enabled to /diagnostics."""
    monkeypatch.setattr(antispam, "TURNSTILE_SITE_KEY", "site")
    monkeypatch.setattr(antispam, "TURNSTILE_SECRET_KEY", "")
    assert not antispam.is_turnstile_enabled()


def test_a_missing_token_is_rejected_without_calling_out(keys, cloudflare):
    calls = cloudflare({"success": True})
    assert antispam.verify_turnstile({}, "1.2.3.4") is False
    assert calls == [], "should not spend a round trip on an absent token"


def test_a_good_token_passes(keys, cloudflare):
    cloudflare({"success": True})
    assert antispam.verify_turnstile(
        {"cf-turnstile-response": "tok"}, "1.2.3.4") is True


def test_a_rejected_token_fails(keys, cloudflare):
    cloudflare({"success": False, "error-codes": ["invalid-input-response"]})
    assert antispam.verify_turnstile(
        {"cf-turnstile-response": "tok"}, "1.2.3.4") is False


def test_the_secret_and_ip_are_sent_not_the_site_key(keys, cloudflare):
    calls = cloudflare({"success": True})
    antispam.verify_turnstile({"cf-turnstile-response": "tok"}, "9.9.9.9")
    body = calls[0].data.decode()
    assert "1x0000000000000000000000000000000AA" in body, "secret key must be sent"
    assert "9.9.9.9" in body
    assert "1x00000000000000000000AA" not in body, "site key must not be sent"


@pytest.mark.parametrize("boom", [
    urllib.error.URLError("down"),
    TimeoutError("slow"),
    ValueError("not json"),
])
def test_it_fails_closed_when_cloudflare_is_unreachable(keys, cloudflare, boom):
    """Failing open would turn an outage into an open door, silently."""
    cloudflare(raises=boom)
    assert antispam.verify_turnstile(
        {"cf-turnstile-response": "tok"}, "1.2.3.4") is False


# ---------------------------------------------------------------------------
# The widget on the page
# ---------------------------------------------------------------------------

def test_the_signup_page_renders_the_widget_when_configured(client, keys):
    body = client.get("/signup").get_data(as_text=True)
    assert "challenges.cloudflare.com/turnstile/v0/api.js" in body
    assert 'class="cf-turnstile"' in body
    assert "1x00000000000000000000AA" in body


def test_the_signup_page_is_clean_when_not_configured(client, monkeypatch):
    monkeypatch.setattr(antispam, "TURNSTILE_SITE_KEY", "")
    body = client.get("/signup").get_data(as_text=True)
    assert "challenges.cloudflare.com" not in body
    assert "cf-turnstile" not in body


def _human_form(flask_app, **fields):
    """A submission that clears layer 1, so layer 4 is what is under test.

    The honeypot must be empty and the signed timing token must be older than
    MIN_FORM_SECONDS — forged here rather than really waiting 2.5s.
    """
    import time
    with flask_app.app_context():
        token = antispam._serializer().dumps({"t": time.time() - 10})
    # The widget injects this in a browser; without it verify_turnstile
    # short-circuits on the missing token and never reaches the code under test.
    return {antispam.FORM_TOKEN_FIELD: token,
            antispam.HONEYPOT_FIELD: "",
            antispam.TURNSTILE_FIELD: "widget-token",
            **fields}


def test_the_helper_clears_the_earlier_layers(client, flask_app, monkeypatch):
    """Guards the tests below from passing for the wrong reason: without this, a
    form rejected by the honeypot looks just like one rejected by the CAPTCHA."""
    monkeypatch.setattr(antispam, "TURNSTILE_SITE_KEY", "")
    monkeypatch.setattr(antispam, "TURNSTILE_SECRET_KEY", "")
    monkeypatch.setattr(antispam, "validate_email_address", lambda e: "syntax")
    r = client.post("/signup", data=_human_form(
        flask_app, email="reaches-layer-3@example.com", password="a-good-password-1"))
    assert "valid email address" in r.get_data(as_text=True).lower(),         "did not get past layers 1 and 2"


def test_signup_is_blocked_when_the_captcha_fails(client, flask_app, keys, cloudflare):
    cloudflare({"success": False, "error-codes": ["invalid-input-response"]})
    r = client.post("/signup", data=_human_form(
        flask_app, email="blocked@example.com", password="a-good-password-1"))
    assert r.status_code == 200
    assert "verify that you" in r.get_data(as_text=True).lower()

    from models import User
    with flask_app.app_context():
        assert User.query.filter_by(email="blocked@example.com").first() is None,             "a blocked signup must not leave an account row behind"


def test_the_captcha_runs_before_the_dns_lookup(client, flask_app, keys, cloudflare):
    """Ordering matters: otherwise signup is a free DNS prober for anyone."""
    cloudflare({"success": False, "error-codes": ["invalid-input-response"]})
    seen = []
    original = antispam.validate_email_address
    antispam.validate_email_address = lambda e: seen.append(e) or original(e)
    try:
        client.post("/signup", data=_human_form(
            flask_app, email="never-looked-up@example.com", password="a-good-password-1"))
    finally:
        antispam.validate_email_address = original
    assert seen == [], "the address was validated despite a failed CAPTCHA"


def test_a_passing_captcha_lets_the_signup_continue(client, flask_app, keys, cloudflare,
                                                    monkeypatch):
    """Past layer 4 the address checks take over, which is proof it got through."""
    cloudflare({"success": True})
    monkeypatch.setattr(antispam, "validate_email_address", lambda e: "syntax")
    r = client.post("/signup", data=_human_form(
        flask_app, email="allowed@example.com", password="a-good-password-1"))
    assert "valid email address" in r.get_data(as_text=True).lower()
