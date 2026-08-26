import pytest

from signal_deck.config import Settings


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("SIGNAL_DECK_SECRET", "test-secret")
    monkeypatch.setenv("SIGNAL_DECK_HOST", "100.64.0.1")
    return Settings()
