import pytest

from signal_deck.config import ConfigError, Settings


def test_missing_secret_rejected(monkeypatch):
    monkeypatch.delenv("SIGNAL_DECK_SECRET", raising=False)
    monkeypatch.setenv("SIGNAL_DECK_HOST", "100.64.0.1")
    with pytest.raises(ConfigError):
        Settings()


def test_missing_host_rejected(monkeypatch):
    monkeypatch.setenv("SIGNAL_DECK_SECRET", "s")
    monkeypatch.delenv("SIGNAL_DECK_HOST", raising=False)
    with pytest.raises(ConfigError):
        Settings()


def test_wildcard_host_rejected(monkeypatch):
    monkeypatch.setenv("SIGNAL_DECK_SECRET", "s")
    monkeypatch.setenv("SIGNAL_DECK_HOST", "0.0.0.0")
    with pytest.raises(ConfigError):
        Settings()


def test_valid_settings_loaded(monkeypatch):
    monkeypatch.setenv("SIGNAL_DECK_SECRET", "s")
    monkeypatch.setenv("SIGNAL_DECK_HOST", "100.64.0.1")
    settings = Settings()
    assert settings.secret == "s"
    assert settings.host == "100.64.0.1"


def test_process_control_settings_default_unconfigured(monkeypatch):
    monkeypatch.setenv("SIGNAL_DECK_SECRET", "s")
    monkeypatch.setenv("SIGNAL_DECK_HOST", "100.64.0.1")
    settings = Settings()
    assert settings.rustle_binary is None
    assert settings.ticktrader_binary is None
    assert str(settings.process_registry_file) == "process_registry.json"
    assert str(settings.stop_log_file) == "stop_events.log"


def test_process_control_settings_from_env(monkeypatch):
    monkeypatch.setenv("SIGNAL_DECK_SECRET", "s")
    monkeypatch.setenv("SIGNAL_DECK_HOST", "100.64.0.1")
    monkeypatch.setenv("SIGNAL_DECK_RUSTLE_BINARY", "/opt/rustle/tt-live-runner")
    monkeypatch.setenv("SIGNAL_DECK_TICKTRADER_BINARY", "/opt/ticktrader/run.py")
    settings = Settings()
    assert str(settings.rustle_binary) == "/opt/rustle/tt-live-runner"
    assert str(settings.ticktrader_binary) == "/opt/ticktrader/run.py"
