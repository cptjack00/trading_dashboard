from __future__ import annotations

import json
import os
import signal
import stat
import time
from pathlib import Path

import pytest

from signal_deck.process_control import ProcessRegistry, RunNotFoundError, is_pid_alive


def _write_fake_binary(path: Path, *, trap_sigterm: bool = True) -> None:
    """A fake sleep/echo script standing in for the real trading binaries -
    accepts `--config <path>` (ignored) and just sleeps, optionally trapping
    SIGTERM to exit cleanly like tt-live-runner's ShutdownCoordinator does."""
    body = "sleep 30\n" if not trap_sigterm else "trap 'exit 0' TERM\nsleep 30 &\nwait\n"
    path.write_text(f"#!/bin/sh\n{body}")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met before timeout")


@pytest.fixture
def registry(tmp_path: Path) -> ProcessRegistry:
    return ProcessRegistry(tmp_path / "process_registry.json", tmp_path / "stop_events.log")


@pytest.fixture
def fake_binary(tmp_path: Path) -> Path:
    binary = tmp_path / "fake-runner.sh"
    _write_fake_binary(binary)
    return binary


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "strategy.toml"
    path.write_text("name = 'test'\n")
    return path


def test_start_run_spawns_process_and_writes_manifest(registry, fake_binary, config_file, tmp_path):
    runs_root = tmp_path / "runs"
    run = registry.start_run(
        project="rustle", run_type="live", config_path=str(config_file), cmd_prefix=str(fake_binary), cwd=tmp_path, runs_root=runs_root
    )

    assert run["project"] == "rustle"
    assert run["status"] == "live"
    run_dir = runs_root / run["run_id"]
    manifest = json.loads((run_dir / "run.json").read_text())
    assert manifest["run_id"] == run["run_id"]
    assert manifest["state"] == "live"

    try:
        assert is_pid_alive(_registered_pid(registry, "rustle", run["run_id"]))
    finally:
        os.kill(_registered_pid(registry, "rustle", run["run_id"]), signal.SIGKILL)


def test_start_run_persists_registry_across_instances(registry, fake_binary, config_file, tmp_path):
    runs_root = tmp_path / "runs"
    run = registry.start_run(
        project="rustle", run_type="live", config_path=str(config_file), cmd_prefix=str(fake_binary), cwd=tmp_path, runs_root=runs_root
    )
    pid = _registered_pid(registry, "rustle", run["run_id"])

    reloaded = ProcessRegistry(tmp_path / "process_registry.json", tmp_path / "stop_events.log")
    reloaded.stop_run(project="rustle", run_id=run["run_id"])

    # This test runs in one process, so unlike a real dashboard restart the
    # child is still ours to reap - `registry` (which holds the Popen handle
    # from start_run) must reconcile or the exited child stays a zombie and
    # os.kill(pid, 0) keeps reporting it "alive".
    try:
        _wait_until(lambda: _reap(registry) or not is_pid_alive(pid))
    finally:
        if is_pid_alive(pid):
            os.kill(pid, signal.SIGKILL)


def test_start_run_with_config_content_writes_and_launches_used_config_copy(
    registry, fake_binary, config_file, tmp_path
):
    runs_root = tmp_path / "runs"
    run = registry.start_run(
        project="rustle",
        run_type="live",
        config_path=str(config_file),
        cmd_prefix=str(fake_binary),
        cwd=tmp_path,
        runs_root=runs_root,
        config_content="name = 'edited'\n",
    )

    run_dir = runs_root / run["run_id"]
    used_config = run_dir / "used_config.toml"
    assert used_config.read_text() == "name = 'edited'\n"
    assert config_file.read_text() == "name = 'test'\n"

    pid = _registered_pid(registry, "rustle", run["run_id"])
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_text()
        assert str(used_config) in cmdline
    finally:
        os.kill(pid, signal.SIGKILL)


def test_start_run_missing_config_raises(registry, fake_binary, tmp_path):
    with pytest.raises(FileNotFoundError):
        registry.start_run(
            project="rustle",
            run_type="live",
            config_path=str(tmp_path / "nope.toml"),
            cmd_prefix=str(fake_binary),
            cwd=tmp_path,
            runs_root=tmp_path / "runs",
        )


def test_stop_run_sends_sigterm_to_tracked_pid(registry, fake_binary, config_file, tmp_path):
    run = registry.start_run(
        project="rustle", run_type="live", config_path=str(config_file), cmd_prefix=str(fake_binary), cwd=tmp_path, runs_root=tmp_path / "runs"
    )
    pid = _registered_pid(registry, "rustle", run["run_id"])
    assert is_pid_alive(pid)

    registry.stop_run(project="rustle", run_id=run["run_id"])

    _wait_until(lambda: _reap(registry) or not is_pid_alive(pid))


def test_stop_run_unknown_run_raises(registry):
    with pytest.raises(RunNotFoundError):
        registry.stop_run(project="rustle", run_id="does-not-exist")


def test_stop_run_twice_rapidly_sends_at_most_one_sigterm(monkeypatch, registry, fake_binary, config_file, tmp_path):
    run = registry.start_run(
        project="rustle", run_type="live", config_path=str(config_file), cmd_prefix=str(fake_binary), cwd=tmp_path, runs_root=tmp_path / "runs"
    )
    pid = _registered_pid(registry, "rustle", run["run_id"])

    kill_calls = []
    real_kill = os.kill

    def spy_kill(target_pid, sig):
        kill_calls.append((target_pid, sig))
        return real_kill(target_pid, sig)

    monkeypatch.setattr("signal_deck.process_control.os.kill", spy_kill)

    registry.stop_run(project="rustle", run_id=run["run_id"])
    registry.stop_run(project="rustle", run_id=run["run_id"])

    sigterm_calls = [c for c in kill_calls if c[1] == signal.SIGTERM]
    assert sigterm_calls == [(pid, signal.SIGTERM)]
    _wait_until(lambda: _reap(registry) or not is_pid_alive(pid))


def test_stop_run_concurrent_calls_send_at_most_one_sigterm(monkeypatch, registry, fake_binary, config_file, tmp_path):
    """Two truly concurrent stop requests (not just sequential) must still
    dedupe - FastAPI's sync routes run each request in a threadpool, so a
    check-then-write race here would let both send SIGTERM."""
    import threading

    run = registry.start_run(
        project="rustle", run_type="live", config_path=str(config_file), cmd_prefix=str(fake_binary), cwd=tmp_path, runs_root=tmp_path / "runs"
    )
    pid = _registered_pid(registry, "rustle", run["run_id"])

    kill_calls = []
    real_kill = os.kill

    def spy_kill(target_pid, sig):
        kill_calls.append((target_pid, sig))
        return real_kill(target_pid, sig)

    monkeypatch.setattr("signal_deck.process_control.os.kill", spy_kill)

    barrier = threading.Barrier(2)

    def call_stop():
        barrier.wait(timeout=2)
        registry.stop_run(project="rustle", run_id=run["run_id"])

    threads = [threading.Thread(target=call_stop) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2)

    sigterm_calls = [c for c in kill_calls if c[1] == signal.SIGTERM]
    assert sigterm_calls == [(pid, signal.SIGTERM)]
    _wait_until(lambda: _reap(registry) or not is_pid_alive(pid))


def test_stop_run_writes_durable_record_for_every_request(registry, fake_binary, config_file, tmp_path):
    run = registry.start_run(
        project="rustle", run_type="live", config_path=str(config_file), cmd_prefix=str(fake_binary), cwd=tmp_path, runs_root=tmp_path / "runs"
    )
    pid = _registered_pid(registry, "rustle", run["run_id"])

    registry.stop_run(project="rustle", run_id=run["run_id"])
    registry.stop_run(project="rustle", run_id=run["run_id"])
    _wait_until(lambda: _reap(registry) or not is_pid_alive(pid))

    lines = (tmp_path / "stop_events.log").read_text().splitlines()
    assert len(lines) == 2
    assert all(f"run rustle/{run['run_id']}" in line for line in lines)


def test_reconcile_marks_crashed_process_as_crashed(registry, tmp_path, config_file):
    crashing_binary = tmp_path / "crash.sh"
    crashing_binary.write_text("#!/bin/sh\nexit 1\n")
    crashing_binary.chmod(crashing_binary.stat().st_mode | stat.S_IEXEC)

    run = registry.start_run(
        project="rustle",
        run_type="live",
        config_path=str(config_file),
        cmd_prefix=str(crashing_binary),
        cwd=tmp_path,
        runs_root=tmp_path / "runs",
    )
    run_dir = tmp_path / "runs" / run["run_id"]

    def manifest_state_is(expected: str) -> bool:
        registry.reconcile()
        return json.loads((run_dir / "run.json").read_text())["state"] == expected

    _wait_until(lambda: manifest_state_is("crashed"))


def test_reconcile_marks_operator_stopped_process_as_stopped(registry, fake_binary, config_file, tmp_path):
    run = registry.start_run(
        project="rustle", run_type="live", config_path=str(config_file), cmd_prefix=str(fake_binary), cwd=tmp_path, runs_root=tmp_path / "runs"
    )
    run_dir = tmp_path / "runs" / run["run_id"]
    registry.stop_run(project="rustle", run_id=run["run_id"])

    def manifest_state_is(expected: str) -> bool:
        registry.reconcile()
        return json.loads((run_dir / "run.json").read_text())["state"] == expected

    _wait_until(lambda: manifest_state_is("stopped"))


def test_reconcile_leaves_still_live_run_untouched(registry, fake_binary, config_file, tmp_path):
    run = registry.start_run(
        project="rustle", run_type="live", config_path=str(config_file), cmd_prefix=str(fake_binary), cwd=tmp_path, runs_root=tmp_path / "runs"
    )
    pid = _registered_pid(registry, "rustle", run["run_id"])
    run_dir = tmp_path / "runs" / run["run_id"]

    try:
        registry.reconcile()
        assert json.loads((run_dir / "run.json").read_text())["state"] == "live"
    finally:
        os.kill(pid, signal.SIGKILL)


def _registered_pid(registry: ProcessRegistry, project: str, run_id: str) -> int:
    data = json.loads(registry._registry_path.read_text())
    return data[f"{project}:{run_id}"]["pid"]


def _reap(registry: ProcessRegistry) -> None:
    """Poll+reap any of `registry`'s in-memory child processes that already
    exited, so a same-process test doesn't see a zombie as still "alive"."""
    for proc in registry._procs.values():
        proc.poll()
