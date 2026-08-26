from __future__ import annotations

import os

DEFAULT_SESSION_TTL_SECONDS = 7 * 24 * 3600
DEFAULT_PORT = 8000


class ConfigError(RuntimeError):
    pass


class Settings:
    def __init__(self) -> None:
        secret = os.environ.get("SIGNAL_DECK_SECRET")
        if not secret:
            raise ConfigError("SIGNAL_DECK_SECRET must be set to a shared login secret")
        self.secret = secret

        # ponytail: no auto-detection magic here — the operator sets this explicitly
        # (`tailscale ip -4`), and we fail closed rather than default to 0.0.0.0.
        host = os.environ.get("SIGNAL_DECK_HOST")
        if not host or host == "0.0.0.0":
            raise ConfigError(
                "SIGNAL_DECK_HOST must be set to the Tailscale interface address "
                "(run `tailscale ip -4`); refusing to bind 0.0.0.0"
            )
        self.host = host

        self.port = int(os.environ.get("SIGNAL_DECK_PORT", DEFAULT_PORT))
        self.session_ttl_seconds = int(
            os.environ.get("SIGNAL_DECK_SESSION_TTL", DEFAULT_SESSION_TTL_SECONDS)
        )


def load_settings() -> Settings:
    return Settings()
