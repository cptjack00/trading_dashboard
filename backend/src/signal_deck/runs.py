"""Run discovery for the unified run list.

Scans a root directory per project for run subdirectories. Each run
subdirectory carries a small `run.json` manifest (run_id, run_type, state,
started_at, ended_at, ...) alongside that project's log files.

ponytail: the manifest schema is trusted, not validated against a formal
contract. #9's process-start registry (`process_control.py`) writes and
updates it directly. Malformed/incomplete manifests are skipped rather than
raising, so one bad run (e.g. caught mid-write) can't take down the rest of
the list.

Alongside that, `iter_runs` also picks up runs the dashboard never launched
itself: a directory with a trade log but no `run.json` (an operator ran
rustle/ticktrader by hand). Those get a synthesized stand-in manifest so
every other consumer (discovery, `find_run`, live-tracking) keeps working
against one shape.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from .process_control import is_pid_alive
from .sources.base import PnL, is_encrypted
from .sources.rustle import RustleAdapter
from .sources.ticktrader import TickTraderTradeLogAdapter

MANIFEST_NAME = "run.json"
PID_FILE_NAME = "runner.pid"
_TRADE_LOG_NAMES = {"rustle": "trade_log.jsonl", "ticktrader": "trade_log.csv"}


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    project: str
    run_type: str
    status: str
    started_at: float
    ended_at: float | None
    pnl: float


def _status(*, run_type: str, state: str) -> str:
    return "backtest" if run_type == "backtest" else state


def _latest_pnl_total(pnl: list[PnL]) -> float:
    latest: dict[str, PnL] = {}
    for point in pnl:
        latest[point.slot] = point
    return sum(point.realized + point.unrealized for point in latest.values())


_REQUIRED_MANIFEST_FIELDS = ("run_id", "run_type", "state", "started_at")


def _load_manifest(run_dir: Path) -> dict | None:
    manifest_path = run_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict) or not all(f in manifest for f in _REQUIRED_MANIFEST_FIELDS):
        return None
    return manifest


def _run_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir())


def iter_manifests(root: Path) -> list[tuple[Path, dict]]:
    """(run_dir, manifest) for every run directory under `root` with a valid
    manifest; malformed/missing manifests are skipped, not raised."""
    pairs = ((run_dir, _load_manifest(run_dir)) for run_dir in _run_dirs(root))
    return [(run_dir, manifest) for run_dir, manifest in pairs if manifest is not None]


def _read_runner_pid(run_dir: Path) -> int | None:
    pid_path = run_dir / PID_FILE_NAME
    if not pid_path.is_file():
        return None
    try:
        return int(pid_path.read_text().strip())
    except (OSError, ValueError):
        return None


def _manifestless_status(run_dir: Path) -> str:
    """`unknown` is a fourth status alongside live/stopped/crashed/backtest: a
    manifest-less run this process never launched has no exit code and no
    operator-stop record to tell "stopped" from "crashed" apart, so a dead (or
    never-recorded) PID reports `unknown` rather than guessing either."""
    pid = _read_runner_pid(run_dir)
    if pid is not None and is_pid_alive(pid):
        return "live"
    return "unknown"


def _find_manifestless_run_dirs(root: Path, log_name: str) -> list[Path]:
    """Depth-agnostic scan for directories holding `log_name` with no `run.json`
    sibling - rustle nests two levels deep (`<mode>_<config>/<date>/`), ticktrader
    one (`<prefix>-<timestamp>/`), so this walks until it finds a run rather than
    assuming a fixed depth. Descent stops the moment a run dir is identified -
    no nested runs-within-runs."""
    if not root.is_dir():
        return []
    found: list[Path] = []

    def _walk(current: Path) -> None:
        if (current / MANIFEST_NAME).is_file():
            return
        if (current / log_name).is_file():
            found.append(current)
            return
        for child in sorted(p for p in current.iterdir() if p.is_dir()):
            _walk(child)

    for child in sorted(p for p in root.iterdir() if p.is_dir()):
        _walk(child)
    return found


def _synthesize_manifest(run_dir: Path, root: Path) -> dict:
    run_id = str(run_dir.relative_to(root)).replace("/", "__")
    try:
        started_at = run_dir.stat().st_ctime
    except OSError:
        started_at = 0.0
    return {
        "run_id": run_id,
        # ponytail: run_type is unknowable without a manifest - dashboard-launched
        # backtests always carry one (they go through `--out`), so a manifest-less
        # run is always treated as "live" here; revisit if that stops holding.
        "run_type": "live",
        "state": _manifestless_status(run_dir),
        "started_at": started_at,
        "ended_at": None,
    }


def _start_of_today() -> float:
    now = time.localtime()
    return time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1))


def _is_worth_tracking(run_dir: Path, log_path: Path) -> bool:
    """A manifest-less run only surfaces if it's from today or still alive.

    A project's runs root can hold months of completed runs (production trade
    logs run tens to hundreds of MB each); without this filter every one of
    them would be discovered and have its PnL re-parsed from scratch on every
    `/api/runs` poll (~every 5s from the frontend) - fine for a handful of
    dashboard-launched runs, not for real production log volumes. This check
    is deliberately cheap (a stat + a pid check) so it never touches the
    expensive log-parsing path for a run it's about to skip anyway.
    """
    try:
        if log_path.stat().st_mtime >= _start_of_today():
            return True
    except OSError:
        return False
    return _manifestless_status(run_dir) == "live"


def iter_runs(root: Path, project: str) -> list[tuple[Path, dict]]:
    """(run_dir, manifest) for every run under `root`: manifest-backed
    (`iter_manifests`) plus manifest-less ones discovered by trade-log presence -
    the latter filtered to today-or-still-live, see `_is_worth_tracking`."""
    pairs = iter_manifests(root)
    seen_ids = {manifest["run_id"] for _, manifest in pairs}
    log_name = _TRADE_LOG_NAMES[project]
    for run_dir in _find_manifestless_run_dirs(root, log_name):
        if not _is_worth_tracking(run_dir, run_dir / log_name):
            continue
        manifest = _synthesize_manifest(run_dir, root)
        if manifest["run_id"] in seen_ids:
            continue
        pairs.append((run_dir, manifest))
    return pairs


def _pnl_or_locked(log_path: Path, adapter) -> list[PnL]:
    # An encrypted log with no key configured is never fed to an adapter -
    # `parse_line` expects plaintext and would raise on raw ciphertext bytes.
    if not log_path.is_file() or is_encrypted(log_path):
        return []
    return adapter.tail().pnl


def discover_rustle_runs(root: Path) -> list[RunSummary]:
    summaries = []
    for run_dir, manifest in iter_runs(root, "rustle"):
        log_path = run_dir / "trade_log.jsonl"
        config_path = manifest.get("config_path")
        adapter = RustleAdapter(log_path, config_path=Path(config_path) if config_path else None)
        pnl = _pnl_or_locked(log_path, adapter)
        summaries.append(
            RunSummary(
                run_id=manifest["run_id"],
                project="rustle",
                run_type=manifest["run_type"],
                status=_status(run_type=manifest["run_type"], state=manifest["state"]),
                started_at=manifest["started_at"],
                ended_at=manifest.get("ended_at"),
                pnl=_latest_pnl_total(pnl),
            )
        )
    return summaries


def discover_ticktrader_runs(root: Path) -> list[RunSummary]:
    summaries = []
    for run_dir, manifest in iter_runs(root, "ticktrader"):
        log_path = run_dir / "trade_log.csv"
        adapter = TickTraderTradeLogAdapter(log_path, symbol=manifest.get("symbol", ""))
        pnl = _pnl_or_locked(log_path, adapter)
        summaries.append(
            RunSummary(
                run_id=manifest["run_id"],
                project="ticktrader",
                run_type=manifest["run_type"],
                status=_status(run_type=manifest["run_type"], state=manifest["state"]),
                started_at=manifest["started_at"],
                ended_at=manifest.get("ended_at"),
                pnl=_latest_pnl_total(pnl),
            )
        )
    return summaries


def discover_all_runs(rustle_root: Path | None, ticktrader_root: Path | None) -> list[RunSummary]:
    runs: list[RunSummary] = []
    if rustle_root is not None:
        runs.extend(discover_rustle_runs(rustle_root))
    if ticktrader_root is not None:
        runs.extend(discover_ticktrader_runs(ticktrader_root))
    return runs


def find_run(
    rustle_root: Path | None, ticktrader_root: Path | None, project: str, run_id: str
) -> tuple[Path, dict] | None:
    """Locate a run's directory and manifest by project + run_id, for run-detail
    endpoints that need the underlying log path rather than a `RunSummary`.
    """
    root = {"rustle": rustle_root, "ticktrader": ticktrader_root}.get(project)
    if root is None:
        return None
    for run_dir, manifest in iter_runs(root, project):
        if manifest["run_id"] == run_id:
            return run_dir, manifest
    return None
