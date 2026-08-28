import json
import os
import signal
import stat
import time
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from signal_deck.app import create_app
from signal_deck.process_control import is_pid_alive


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
    (run_dir / "trade_log.jsonl").write_text(
        json.dumps(
            {
                "slot_id": "s1",
                "timestamp": "09:00:00.000",
                "type": "CONTROL",
                "best_bid": 9.9,
                "best_ask": 10.1,
                "spread": 0.2,
                "trade_price": 10.0,
                "trade_side": "BUY",
                "matched_volume": 1,
                "position": 1,
                "action": "FILLED",
                "pnl": 3.5,
            }
        )
        + "\n"
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
    log_name = "trade_log.jsonl" if project == "rustle" else "trade_log.csv"
    if project == "rustle":
        (run_dir / log_name).write_text(
            json.dumps(
                {
                    "slot_id": "s1",
                    "timestamp": "09:00:00.000",
                    "type": "CONTROL",
                    "best_bid": 9.9,
                    "best_ask": 10.1,
                    "spread": 0.2,
                    "trade_price": 10.0,
                    "trade_side": "BUY",
                    "matched_volume": 1,
                    "position": 1,
                    "action": "FILLED",
                    "pnl": 7.5,
                }
            )
            + "\n"
        )
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
    [equity_point] = body["equity"]
    assert equity_point["equity"] == 7.5


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


def test_run_overview_includes_performance_and_market_fields(settings, tmp_path):
    # This run has no health_log.jsonl (see test_live.py for that adapter), so
    # it only covers what trade_log.jsonl drives (pnl, win rate, fills) plus
    # the Market tab's price series, which comes from the independent
    # collector's own data/ tree, not from the trade log above.
    run_dir = tmp_path / "rustle-runs" / "run-1"
    run_dir.mkdir(parents=True)
    config_path = tmp_path / "run-1.toml"
    config_path.write_text(
        '[[multi_symbol.slots]]\nslot_label = "s1"\n[multi_symbol.slots.config]\nsymbol = "XYZ-PERP"\n'
    )
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": "run-1", "run_type": "live", "state": "live", "started_at": 1.0, "ended_at": None,
                "config_path": str(config_path),
            }
        )
    )
    (run_dir / "trade_log.jsonl").write_text(
        json.dumps(
            {
                "slot_id": "s1",
                "timestamp": "09:00:00.000",
                "type": "CONTROL",
                "best_bid": 0.9,
                "best_ask": 1.1,
                "spread": 0.2,
                "trade_price": 1.0,
                "trade_side": "BUY",
                "matched_volume": 1,
                "position": 0,
                "action": "FILLED",
                "pnl": 2.0,
            }
        )
        + "\n"
    )
    settings.rustle_runs_dir = tmp_path / "rustle-runs"
    settings.rustle_cwd = tmp_path / "rustle-cwd"
    day = datetime.now().strftime("%Y%m%d")
    tick_dir = settings.rustle_cwd / "data" / "XYZ-PERP" / "tick_data"
    tick_dir.mkdir(parents=True)
    (tick_dir / f"{day}.txt").write_text('09:00:00.500 {"price":1.25,"qty":1,"side":"buy"}\n')
    client = make_client(settings, tmp_path)
    login(client)
    client.app.state.live_manager.poll_once()

    resp = client.get("/api/runs/rustle/run-1/overview")
    body = resp.json()

    assert body["live_tracked"] is True
    [pnl] = body["pnl"]
    assert (pnl["slot"], pnl["realized"], pnl["unrealized"]) == ("s1", 2.0, 0.0)
    [win_rate] = body["win_rates"]
    assert (win_rate["wins"], win_rate["losses"]) == (1, 0)
    assert body["fills"] == [{"ts": pnl["ts"], "slot": "s1", "count": 1}]
    # One chart for the real symbol every "s1"-labeled slot trades, sourced
    # from the collector's own tick file - not a per-slot fake "symbol".
    assert body["symbol_prices"]["XYZ-PERP"][0]["price"] == 1.25


def _filled_row_json(slot: str, timestamp: str, pnl: float) -> str:
    return json.dumps(
        {
            "slot_id": slot,
            "timestamp": timestamp,
            "type": "CONTROL",
            "best_bid": 9.9,
            "best_ask": 10.1,
            "spread": 0.2,
            "trade_price": 10.0,
            "trade_side": "BUY",
            "matched_volume": 1,
            "position": 1,
            "action": "FILLED",
            "pnl": pnl,
        }
    )


def _write_run_with_many_trades(tmp_path: Path, *, count: int) -> None:
    run_dir = tmp_path / "rustle-runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps({"run_id": "run-1", "run_type": "live", "state": "live", "started_at": 1.0, "ended_at": None})
    )
    rows = [_filled_row_json("s1", f"09:{i:02d}:00.000", float(i)) for i in range(count)]
    (run_dir / "trade_log.jsonl").write_text("\n".join(rows) + "\n")


def test_trades_requires_session(settings, tmp_path):
    client = make_client(settings, tmp_path)
    resp = client.get("/api/runs/rustle/run-1/trades")
    assert resp.status_code == 401


def test_trades_unknown_run_is_404(settings, tmp_path):
    settings.rustle_runs_dir = tmp_path / "rustle-runs"
    client = make_client(settings, tmp_path)
    login(client)
    resp = client.get("/api/runs/rustle/nope/trades")
    assert resp.status_code == 404


def test_overview_trims_to_fifty_trades_but_full_history_is_pageable(settings, tmp_path):
    _write_run_with_many_trades(tmp_path, count=60)
    settings.rustle_runs_dir = tmp_path / "rustle-runs"
    client = make_client(settings, tmp_path)
    login(client)
    client.app.state.live_manager.poll_once()

    overview = client.get("/api/runs/rustle/run-1/overview").json()
    assert len(overview["trades"]) == 50  # unchanged first-paint shape

    full_history = client.get("/api/runs/rustle/run-1/trades", params={"limit": 60}).json()
    latest_page = client.get("/api/runs/rustle/run-1/trades", params={"limit": 10}).json()
    assert len(full_history) == 60  # all 60 fills survive retention, not just the last 50
    assert latest_page == full_history[-10:]


def test_trades_endpoint_pages_before_a_timestamp(settings, tmp_path):
    _write_run_with_many_trades(tmp_path, count=60)
    settings.rustle_runs_dir = tmp_path / "rustle-runs"
    client = make_client(settings, tmp_path)
    login(client)
    client.app.state.live_manager.poll_once()

    latest = client.get("/api/runs/rustle/run-1/trades", params={"limit": 60}).json()
    cutoff = latest[30]["ts"]

    page = client.get("/api/runs/rustle/run-1/trades", params={"before": cutoff, "limit": 10}).json()
    assert [t["ts"] for t in page] == [t["ts"] for t in latest[20:30]]


def _fake_binary(tmp_path: Path) -> Path:
    """A fake sleep/echo script standing in for the real trading binaries."""
    path = tmp_path / "fake-runner.sh"
    path.write_text("#!/bin/sh\ntrap 'exit 0' TERM\nsleep 30 &\nwait\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met before timeout")


def test_start_run_requires_session(settings, tmp_path):
    client = make_client(settings, tmp_path)
    resp = client.post("/api/runs", json={"project": "rustle", "run_type": "backtest", "config": "x"})
    assert resp.status_code == 401


def test_start_run_rejects_unconfigured_project(settings, tmp_path):
    client = make_client(settings, tmp_path)
    login(client)
    resp = client.post("/api/runs", json={"project": "rustle", "run_type": "backtest", "config": "x"})
    assert resp.status_code == 400


def test_start_run_rejects_invalid_run_type(settings, tmp_path):
    settings.rustle_runs_dir = tmp_path / "rustle-runs"
    _bin = str(_fake_binary(tmp_path))
    settings.rustle_cwd = tmp_path
    settings.rustle_backtest_cmd = _bin
    settings.rustle_live_cmd = _bin
    client = make_client(settings, tmp_path)
    login(client)
    resp = client.post("/api/runs", json={"project": "rustle", "run_type": "bogus", "config": "x"})
    assert resp.status_code == 400


def test_start_run_missing_config_rejected(settings, tmp_path):
    settings.rustle_runs_dir = tmp_path / "rustle-runs"
    _bin = str(_fake_binary(tmp_path))
    settings.rustle_cwd = tmp_path
    settings.rustle_backtest_cmd = _bin
    settings.rustle_live_cmd = _bin
    settings.config_roots_file = tmp_path / "config_roots.json"
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    client = make_client(settings, tmp_path)
    login(client)
    client.post("/api/config-roots/rustle", json={"root": str(configs_dir)})

    resp = client.post(
        "/api/runs", json={"project": "rustle", "run_type": "backtest", "config": str(configs_dir / "nope.toml")}
    )
    assert resp.status_code == 400


def test_start_run_rejects_config_outside_registered_roots(settings, tmp_path):
    settings.rustle_runs_dir = tmp_path / "rustle-runs"
    _bin = str(_fake_binary(tmp_path))
    settings.rustle_cwd = tmp_path
    settings.rustle_backtest_cmd = _bin
    settings.rustle_live_cmd = _bin
    settings.config_roots_file = tmp_path / "config_roots.json"
    (tmp_path / "configs").mkdir()
    outside = tmp_path / "outside" / "strategy.toml"
    outside.parent.mkdir()
    outside.write_text("name = 'x'\n")

    client = make_client(settings, tmp_path)
    login(client)
    client.post("/api/config-roots/rustle", json={"root": str(tmp_path / "configs")})

    resp = client.post("/api/runs", json={"project": "rustle", "run_type": "backtest", "config": str(outside)})
    assert resp.status_code == 400
    assert "registered" in resp.json()["detail"]


def test_start_run_spawns_process_and_appears_in_run_list(settings, tmp_path):
    settings.rustle_runs_dir = tmp_path / "rustle-runs"
    _bin = str(_fake_binary(tmp_path))
    settings.rustle_cwd = tmp_path
    settings.rustle_backtest_cmd = _bin
    settings.rustle_live_cmd = _bin
    settings.process_registry_file = tmp_path / "process_registry.json"
    settings.stop_log_file = tmp_path / "stop_events.log"
    settings.config_roots_file = tmp_path / "config_roots.json"
    config = tmp_path / "strategy.toml"
    config.write_text("name = 'x'\n")

    client = make_client(settings, tmp_path)
    login(client)
    client.post("/api/config-roots/rustle", json={"root": str(tmp_path)})

    resp = client.post("/api/runs", json={"project": "rustle", "run_type": "live", "config": str(config)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["project"] == "rustle"
    assert body["status"] == "live"

    runs_resp = client.get("/api/runs")
    [run] = runs_resp.json()
    assert run["run_id"] == body["run_id"]

    pid = client.app.state.process_registry._procs[f"rustle:{body['run_id']}"].pid
    try:
        client.post(f"/api/runs/rustle/{body['run_id']}/stop")
        _wait_until(lambda: client.app.state.process_registry.reconcile() or not is_pid_alive(pid))
    finally:
        if is_pid_alive(pid):
            os.kill(pid, signal.SIGKILL)


def test_stop_run_requires_session(settings, tmp_path):
    client = make_client(settings, tmp_path)
    resp = client.post("/api/runs/rustle/run-1/stop")
    assert resp.status_code == 401


def test_stop_run_unknown_run_is_404(settings, tmp_path):
    client = make_client(settings, tmp_path)
    login(client)
    resp = client.post("/api/runs/rustle/nope/stop")
    assert resp.status_code == 404


def test_stop_run_sends_sigterm_and_appears_stopped_after_reconcile(settings, tmp_path):
    settings.rustle_runs_dir = tmp_path / "rustle-runs"
    _bin = str(_fake_binary(tmp_path))
    settings.rustle_cwd = tmp_path
    settings.rustle_backtest_cmd = _bin
    settings.rustle_live_cmd = _bin
    settings.process_registry_file = tmp_path / "process_registry.json"
    settings.stop_log_file = tmp_path / "stop_events.log"
    settings.config_roots_file = tmp_path / "config_roots.json"
    config = tmp_path / "strategy.toml"
    config.write_text("name = 'x'\n")

    client = make_client(settings, tmp_path)
    login(client)
    client.post("/api/config-roots/rustle", json={"root": str(tmp_path)})
    started = client.post("/api/runs", json={"project": "rustle", "run_type": "live", "config": str(config)}).json()
    run_id = started["run_id"]
    pid = client.app.state.process_registry._procs[f"rustle:{run_id}"].pid

    resp = client.post(f"/api/runs/rustle/{run_id}/stop")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    def stopped() -> bool:
        runs = {r["run_id"]: r for r in client.get("/api/runs").json()}
        return runs[run_id]["status"] == "stopped"

    try:
        _wait_until(stopped)
    finally:
        if is_pid_alive(pid):
            os.kill(pid, signal.SIGKILL)
