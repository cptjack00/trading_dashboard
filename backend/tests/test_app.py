import json
from pathlib import Path

from fastapi.testclient import TestClient

from signal_deck.app import create_app


def make_client(settings, tmp_path: Path) -> TestClient:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>shell</html>")
    app = create_app(settings, frontend_dist=dist)
    return TestClient(app)


def login(client: TestClient) -> None:
    resp = client.post("/api/login", json={"password": "test-secret"})
    assert resp.status_code == 200


def test_session_requires_login_first(settings, tmp_path):
    client = make_client(settings, tmp_path)
    resp = client.get("/api/session")
    assert resp.status_code == 401


def test_login_with_wrong_password_rejected(settings, tmp_path):
    client = make_client(settings, tmp_path)
    resp = client.post("/api/login", json={"password": "nope"})
    assert resp.status_code == 401
    assert "signal_deck_session" not in resp.cookies


def test_login_then_session_persists(settings, tmp_path):
    client = make_client(settings, tmp_path)
    login_resp = client.post("/api/login", json={"password": "test-secret"})
    assert login_resp.status_code == 200
    assert "signal_deck_session" in login_resp.cookies

    session_resp = client.get("/api/session")
    assert session_resp.status_code == 200
    assert session_resp.json() == {"authenticated": True}


def test_logout_clears_session(settings, tmp_path):
    client = make_client(settings, tmp_path)
    client.post("/api/login", json={"password": "test-secret"})
    client.post("/api/logout")
    resp = client.get("/api/session")
    assert resp.status_code == 401


def test_frontend_shell_served(settings, tmp_path):
    client = make_client(settings, tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "shell" in resp.text


def test_runs_requires_session(settings, tmp_path):
    client = make_client(settings, tmp_path)
    resp = client.get("/api/runs")
    assert resp.status_code == 401


def test_runs_empty_when_no_roots_configured(settings, tmp_path):
    client = make_client(settings, tmp_path)
    login(client)
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_runs_aggregates_discovered_runs(settings, tmp_path):
    run_dir = tmp_path / "rustle-runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "run_type": "live",
                "state": "live",
                "started_at": 1000.0,
                "ended_at": None,
            }
        )
    )
    (run_dir / "events.jsonl").write_text(
        json.dumps({"type": "pnl", "ts": 1, "slot": "s1", "realized": 3.5}) + "\n"
    )
    settings.rustle_runs_dir = tmp_path / "rustle-runs"

    client = make_client(settings, tmp_path)
    login(client)
    resp = client.get("/api/runs")

    assert resp.status_code == 200
    [run] = resp.json()
    assert run["run_id"] == "run-1"
    assert run["project"] == "rustle"
    assert run["status"] == "live"
    assert run["pnl"] == 3.5
