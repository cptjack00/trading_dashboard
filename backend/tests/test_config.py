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
    assert settings.rustle_cwd is None
    assert settings.rustle_backtest_cmd is None
    assert settings.rustle_live_cmd is None
    assert settings.ticktrader_cwd is None
    assert settings.ticktrader_backtest_cmd is None
    assert settings.ticktrader_live_cmd is None
    assert str(settings.process_registry_file) == "process_registry.json"
    assert str(settings.stop_log_file) == "stop_events.log"


def test_process_control_settings_from_env(monkeypatch):
    monkeypatch.setenv("SIGNAL_DECK_SECRET", "s")
    monkeypatch.setenv("SIGNAL_DECK_HOST", "100.64.0.1")
    monkeypatch.setenv("SIGNAL_DECK_RUSTLE_CWD", "/opt/rustle")
    monkeypatch.setenv("SIGNAL_DECK_RUSTLE_BACKTEST_CMD", "cargo run -p tt-replay --bin tt-replay --release -- --config")
    monkeypatch.setenv("SIGNAL_DECK_RUSTLE_LIVE_CMD", "cargo run -p tt-live-runner --bin tt-live-runner --release")
    monkeypatch.setenv("SIGNAL_DECK_TICKTRADER_CWD", "/opt/ticktrader")
    monkeypatch.setenv("SIGNAL_DECK_TICKTRADER_BACKTEST_CMD", "python -m ticktrader backtest --config")
    monkeypatch.setenv("SIGNAL_DECK_TICKTRADER_LIVE_CMD", "python -m ticktrader live --config")
    settings = Settings()
    assert str(settings.rustle_cwd) == "/opt/rustle"
    assert settings.rustle_backtest_cmd == "cargo run -p tt-replay --bin tt-replay --release -- --config"
    assert settings.rustle_live_cmd == "cargo run -p tt-live-runner --bin tt-live-runner --release"
    assert str(settings.ticktrader_cwd) == "/opt/ticktrader"
    assert settings.ticktrader_backtest_cmd == "python -m ticktrader backtest --config"
    assert settings.ticktrader_live_cmd == "python -m ticktrader live --config"
