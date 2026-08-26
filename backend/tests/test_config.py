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
