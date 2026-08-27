"""Run discovery for the unified run list.

Scans a root directory per project for run subdirectories. Each run
subdirectory carries a small `run.json` manifest (run_id, run_type, state,
started_at, ended_at, ...) alongside that project's log files.

ponytail: the manifest schema is trusted, not validated against a formal
contract. #9's process-start registry (`process_control.py`) writes and
updates it directly. Malformed/incomplete manifests are skipped rather than
raising, so one bad run (e.g. caught mid-write) can't take down the rest of
the list.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .sources.base import PnL, is_encrypted
from .sources.rustle import RustleAdapter
from .sources.ticktrader import TickTraderTradeLogAdapter

MANIFEST_NAME = "run.json"


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


def _pnl_or_locked(log_path: Path, adapter) -> list[PnL]:
    # An encrypted log with no key configured is never fed to an adapter -
    # `parse_line` expects plaintext and would raise on raw ciphertext bytes.
    if not log_path.is_file() or is_encrypted(log_path):
        return []
    return adapter.tail().pnl


def discover_rustle_runs(root: Path) -> list[RunSummary]:
    summaries = []
    for run_dir, manifest in iter_manifests(root):
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
    for run_dir, manifest in iter_manifests(root):
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
    for run_dir, manifest in iter_manifests(root):
        if manifest["run_id"] == run_id:
            return run_dir, manifest
    return None
