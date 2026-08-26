from __future__ import annotations

import os
from pathlib import Path

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

        # ponytail: optional, no default path guessed — an unset root just means
        # that project contributes no runs to the list yet.
        self.rustle_runs_dir = _optional_path("SIGNAL_DECK_RUSTLE_RUNS_DIR")
        self.ticktrader_runs_dir = _optional_path("SIGNAL_DECK_TICKTRADER_RUNS_DIR")

        # Local settings store for #8's config-root scan list. Relative by
        # default so it lands in the systemd unit's writable WorkingDirectory.
        self.config_roots_file = Path(
            os.environ.get("SIGNAL_DECK_CONFIG_ROOTS_FILE", "config_roots.json")
        )

        # #9's process-start registry and #10's durable stop-request log.
        self.process_registry_file = Path(
            os.environ.get("SIGNAL_DECK_PROCESS_REGISTRY_FILE", "process_registry.json")
        )
        self.stop_log_file = Path(os.environ.get("SIGNAL_DECK_STOP_LOG_FILE", "stop_events.log"))

        # ponytail: optional like the runs dirs above - a project with no binary
        # configured just can't launch runs from the dashboard yet.
        self.rustle_binary = _optional_path("SIGNAL_DECK_RUSTLE_BINARY")
        self.ticktrader_binary = _optional_path("SIGNAL_DECK_TICKTRADER_BINARY")


def _optional_path(env_var: str) -> Path | None:
    value = os.environ.get(env_var)
    return Path(value) if value else None


def load_settings() -> Settings:
    return Settings()
