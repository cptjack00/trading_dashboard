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


def _write_run(tmp_path: Path, *, project: str, run_id: str, state: str) -> Path:
    root_name = "rustle-runs" if project == "rustle" else "tt-runs"
    run_dir = tmp_path / root_name / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps({"run_id": run_id, "run_type": "live", "state": state, "started_at": 1.0, "ended_at": None})
    )
    log_name = "events.jsonl" if project == "rustle" else "trade_log.csv"
    if project == "rustle":
        (run_dir / log_name).write_text(json.dumps({"type": "equity", "ts": 1, "equity": 7.5}) + "\n")
    else:
        (run_dir / log_name).write_text(
            "timestamp,type,trade_price,trade_side,matched_volume,pnl,unrealized_pnl\n"
        )
    return run_dir


def test_run_overview_requires_session(settings, tmp_path):
    client = make_client(settings, tmp_path)
    resp = client.get("/api/runs/rustle/run-1/overview")
    assert resp.status_code == 401


def test_run_overview_unknown_run_is_404(settings, tmp_path):
    settings.rustle_runs_dir = tmp_path / "rustle-runs"
    client = make_client(settings, tmp_path)
    login(client)
    resp = client.get("/api/runs/rustle/nope/overview")
    assert resp.status_code == 404


def test_run_overview_live_run_after_a_poll_tick(settings, tmp_path):
    _write_run(tmp_path, project="rustle", run_id="run-1", state="live")
    settings.rustle_runs_dir = tmp_path / "rustle-runs"
    client = make_client(settings, tmp_path)
    login(client)

    app = client.app
    app.state.live_manager.poll_once()

    resp = client.get("/api/runs/rustle/run-1/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "live"
    assert body["encrypted_locked"] is False
    assert body["equity"] == [{"ts": 1, "equity": 7.5}]


def test_run_stream_requires_live_run(settings, tmp_path):
    _write_run(tmp_path, project="rustle", run_id="run-1", state="stopped")
    settings.rustle_runs_dir = tmp_path / "rustle-runs"
    client = make_client(settings, tmp_path)
    login(client)

    resp = client.get("/api/runs/rustle/run-1/stream")
    assert resp.status_code == 404


def test_run_stream_subscribes_a_live_run(settings, tmp_path):
    _write_run(tmp_path, project="rustle", run_id="run-1", state="live")
    settings.rustle_runs_dir = tmp_path / "rustle-runs"
    client = make_client(settings, tmp_path)
    login(client)

    manager = client.app.state.live_manager
    manager.poll_once()  # run now tracked as live; subscribe can succeed
    assert manager.subscribe("rustle", "run-1") is not None


def test_format_sse_delta_and_done():
    from signal_deck.app import _format_sse
    from signal_deck.live import Delta
    from signal_deck.sources.base import EquityPoint, LatencySample, Trade

    delta = Delta(
        equity=[EquityPoint(ts=2, equity=8.5)],
        trades=[Trade(ts=2, symbol="BTC", side="buy", price=1.0, qty=1.0)],
        channel_latency={"api": [LatencySample(ts=2, mean=1.0, p99=2.0, p999=3.0)]},
    )
    frame = _format_sse(delta)
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")
    assert '"equity": 8.5' in frame
    assert '"api"' in frame

    assert _format_sse(None) == "event: done\ndata: {}\n\n"


def test_config_roots_requires_session(settings, tmp_path):
    client = make_client(settings, tmp_path)
    resp = client.get("/api/config-roots/rustle")
    assert resp.status_code == 401


def test_config_roots_unknown_project_is_404(settings, tmp_path):
    settings.config_roots_file = tmp_path / "config_roots.json"
    client = make_client(settings, tmp_path)
    login(client)
    resp = client.get("/api/config-roots/bogus")
    assert resp.status_code == 404


def test_config_roots_empty_before_any_added(settings, tmp_path):
    settings.config_roots_file = tmp_path / "config_roots.json"
    client = make_client(settings, tmp_path)
    login(client)
    resp = client.get("/api/config-roots/rustle")
    assert resp.status_code == 200
    assert resp.json() == {"roots": []}


def test_config_roots_add_persists_and_lists(settings, tmp_path):
    settings.config_roots_file = tmp_path / "config_roots.json"
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    client = make_client(settings, tmp_path)
    login(client)

    resp = client.post("/api/config-roots/rustle", json={"root": str(configs_dir)})
    assert resp.status_code == 200
    assert resp.json() == {"roots": [str(configs_dir)]}

    resp = client.get("/api/config-roots/rustle")
    assert resp.json() == {"roots": [str(configs_dir)]}


def test_config_roots_add_rejects_non_directory(settings, tmp_path):
    settings.config_roots_file = tmp_path / "config_roots.json"
    client = make_client(settings, tmp_path)
    login(client)

    resp = client.post("/api/config-roots/rustle", json={"root": str(tmp_path / "does-not-exist")})
    assert resp.status_code == 400


def test_config_scan_requires_session(settings, tmp_path):
    client = make_client(settings, tmp_path)
    resp = client.get("/api/config-scan/rustle")
    assert resp.status_code == 401


def test_config_scan_finds_nested_configs_across_roots(settings, tmp_path):
    settings.config_roots_file = tmp_path / "config_roots.json"
    root_a = tmp_path / "a"
    (root_a / "nested").mkdir(parents=True)
    (root_a / "top.toml").write_text("x = 1")
    (root_a / "nested" / "deep.toml").write_text("y = 2")

    client = make_client(settings, tmp_path)
    login(client)
    client.post("/api/config-roots/rustle", json={"root": str(root_a)})

    resp = client.get("/api/config-scan/rustle")
    assert resp.status_code == 200
    assert resp.json() == {
        "configs": sorted([str(root_a / "top.toml"), str(root_a / "nested" / "deep.toml")])
    }


def test_config_scan_empty_when_no_roots_configured(settings, tmp_path):
    settings.config_roots_file = tmp_path / "config_roots.json"
    client = make_client(settings, tmp_path)
    login(client)
    resp = client.get("/api/config-scan/ticktrader")
    assert resp.status_code == 200
    assert resp.json() == {"configs": []}


def test_run_overview_includes_performance_market_and_latency_fields(settings, tmp_path):
    run_dir = tmp_path / "rustle-runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps(
            {"run_id": "run-1", "run_type": "live", "state": "live", "started_at": 1.0, "ended_at": None}
        )
    )
    (run_dir / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"type": "pnl", "ts": 1, "slot": "s1", "realized": 2.0, "unrealized": 0.0}),
                json.dumps({"type": "winrate", "ts": 1, "slot": "s1", "wins": 1, "losses": 0}),
                json.dumps({"type": "fill", "ts": 1, "slot": "s1", "count": 1}),
                json.dumps(
                    {"type": "trade", "ts": 1, "symbol": "BTC", "side": "buy", "price": 1.0, "qty": 1.0, "slot": "s1"}
                ),
                json.dumps({"type": "latency", "ts": 1, "channel": "ws", "mean": 4.0, "p99": 6.0, "p999": 8.0}),
                json.dumps({"type": "health", "ts": 1, "component": "ws", "ok": True, "detail": None}),
            ]
        )
        + "\n"
    )
    settings.rustle_runs_dir = tmp_path / "rustle-runs"
    client = make_client(settings, tmp_path)
    login(client)
    client.app.state.live_manager.poll_once()

    resp = client.get("/api/runs/rustle/run-1/overview")
    body = resp.json()

    assert body["live_tracked"] is True
    assert body["pnl"] == [{"ts": 1, "slot": "s1", "realized": 2.0, "unrealized": 0.0}]
    assert body["win_rates"] == [{"ts": 1, "slot": "s1", "wins": 1, "losses": 0}]
    assert body["fills"] == [{"ts": 1, "slot": "s1", "count": 1}]
    assert body["health"] == [{"ts": 1, "component": "ws", "ok": True, "detail": None}]
    assert body["symbol_prices"]["BTC"][0]["price"] == 1.0
    assert body["channel_latency"]["ws"][0]["mean"] == 4.0
