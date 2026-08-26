"""Signed, httpOnly session tokens from a single shared secret.

Stdlib-only (hmac/secrets/hashlib) per the spec — no external auth framework.
"""

from __future__ import annotations

import base64
import hmac
import json
import time
from hashlib import sha256

SESSION_COOKIE = "signal_deck_session"


def create_session_token(secret: str, ttl_seconds: int, *, now: float | None = None) -> str:
    issued_at = now if now is not None else time.time()
    payload = json.dumps({"exp": issued_at + ttl_seconds}).encode()
    body = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.new(secret.encode(), body, sha256).hexdigest()
    return f"{body.decode()}.{signature}"


def verify_session_token(token: str, secret: str, *, now: float | None = None) -> bool:
    try:
        body, signature = token.split(".", 1)
    except ValueError:
        return False

    expected = hmac.new(secret.encode(), body.encode(), sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False

    padded = body + "=" * (-len(body) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        return False

    current = now if now is not None else time.time()
    return isinstance(payload.get("exp"), (int, float)) and payload["exp"] > current
