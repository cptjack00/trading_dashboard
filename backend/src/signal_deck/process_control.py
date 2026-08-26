"""Process-start (#9) and stop (#10) machinery: launches a run's binary,
tracks it in a small persisted registry, and reconciles its liveness.

ponytail: the registry is one JSON file, read-and-rewritten whole on every
change - fine at this scale (a handful of concurrently-tracked runs on one
operator's box); a real database is unwarranted.

`reconcile()` classifies a finished run as "stopped" (an operator-requested
stop, or a clean exit) vs. "crashed" (an unrequested nonzero exit) using an
in-memory `Popen` handle when this process launched it. After a dashboard
restart that handle is gone, so a vanished PID just gets marked "stopped" -
crashed-vs-stopped can no longer be told apart for a run this process
lifetime didn't launch.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

TERMINAL_STATES = ("stopped", "crashed", "backtest")


class RunNotFoundError(LookupError):
    pass


@dataclass
class RegistryEntry:
    project: str
    run_id: str
    pid: int
    run_dir: str
    started_at: float
    stop_requested_at: float | None = None


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours to signal
    return True


def _load(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(path: Path, data: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _key(project: str, run_id: str) -> str:
    return f"{project}:{run_id}"


def _write_manifest(run_dir: Path, manifest: dict) -> None:
    (run_dir / "run.json").write_text(json.dumps(manifest))


def _read_manifest(run_dir: Path) -> dict | None:
    manifest_path = run_dir / "run.json"
    if not manifest_path.is_file():
        return None
    try:
        return json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


class ProcessRegistry:
    def __init__(self, registry_path: Path, stop_log_path: Path) -> None:
        self._registry_path = registry_path
        self._stop_log_path = stop_log_path
        self._procs: dict[str, subprocess.Popen] = {}
        # FastAPI's sync `def` routes run each request in a threadpool, so two
        # near-simultaneous stop requests for the same run could otherwise both
        # read `stop_requested_at is None` before either writes it back - this
        # serializes the whole read-check-write so at most one ever sends SIGTERM.
        self._lock = threading.Lock()

    def start_run(
        self,
        *,
        project: str,
        run_type: str,
        config_path: str,
        binary: str,
        runs_root: Path,
    ) -> dict:
        if not Path(config_path).is_file():
            raise FileNotFoundError(config_path)

        run_id = f"{run_type}-{uuid.uuid4().hex[:10]}"
        run_dir = runs_root / run_id
        run_dir.mkdir(parents=True)
        started_at = time.time()
        _write_manifest(
            run_dir,
            {"run_id": run_id, "run_type": run_type, "state": "live", "started_at": started_at, "ended_at": None},
        )

        # The child inherits its own duplicated copy of the fd at Popen() time,
        # so the parent's handle can (and should) close right away rather than
        # staying open, unused, for the run's entire lifetime.
        with (run_dir / "process.log").open("wb") as log_file:
            proc = subprocess.Popen(
                [binary, "--config", str(config_path)], stdout=log_file, stderr=subprocess.STDOUT
            )

        key = _key(project, run_id)
        with self._lock:
            entries = _load(self._registry_path)
            entries[key] = asdict(
                RegistryEntry(
                    project=project, run_id=run_id, pid=proc.pid, run_dir=str(run_dir), started_at=started_at
                )
            )
            _save(self._registry_path, entries)
            self._procs[key] = proc

        return {
            "run_id": run_id,
            "project": project,
            "run_type": run_type,
            "status": "backtest" if run_type == "backtest" else "live",
            "started_at": started_at,
            "ended_at": None,
            "pnl": 0.0,
        }

    def stop_run(self, *, project: str, run_id: str) -> None:
        key = _key(project, run_id)
        with self._lock:
            entries = _load(self._registry_path)
            entry = entries.get(key)
            if entry is None:
                raise RunNotFoundError(key)

            if entry.get("stop_requested_at") is None:
                entry["stop_requested_at"] = time.time()
                _save(self._registry_path, entries)
                if is_pid_alive(entry["pid"]):
                    os.kill(entry["pid"], signal.SIGTERM)

        self._append_stop_record(project=project, run_id=run_id)

    def _append_stop_record(self, *, project: str, run_id: str) -> None:
        at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._stop_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._stop_log_path.open("a") as f:
            f.write(f"operator stopped run {project}/{run_id} at {at}\n")

    def reconcile(self) -> None:
        entries = _load(self._registry_path)
        for key, entry in entries.items():
            run_dir = Path(entry["run_dir"])
            manifest = _read_manifest(run_dir)
            if manifest is None or manifest.get("state") in TERMINAL_STATES:
                continue

            proc = self._procs.get(key)
            if proc is not None:
                returncode = proc.poll()
                if returncode is None:
                    continue
                stopped_by_operator = entry.get("stop_requested_at") is not None
                new_state = "stopped" if stopped_by_operator or returncode == 0 else "crashed"
            else:
                if is_pid_alive(entry["pid"]):
                    continue
                new_state = "stopped"

            manifest["state"] = new_state
            manifest["ended_at"] = time.time()
            _write_manifest(run_dir, manifest)
