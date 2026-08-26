from signal_deck.auth import create_session_token, verify_session_token

SECRET = "test-secret"


def test_roundtrip_valid_token():
    token = create_session_token(SECRET, ttl_seconds=60)
    assert verify_session_token(token, SECRET)


def test_expired_token_rejected():
    token = create_session_token(SECRET, ttl_seconds=-1)
    assert not verify_session_token(token, SECRET)


def test_wrong_secret_rejected():
    token = create_session_token(SECRET, ttl_seconds=60)
    assert not verify_session_token(token, "other-secret")


def test_tampered_payload_rejected():
    token = create_session_token(SECRET, ttl_seconds=60)
    body, sig = token.split(".", 1)
    tampered = f"{body}x.{sig}"
    assert not verify_session_token(tampered, SECRET)


def test_malformed_token_rejected():
    assert not verify_session_token("not-a-valid-token", SECRET)
