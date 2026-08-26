"""Run discovery for the unified run list.

Scans a root directory per project for run subdirectories. Each run
subdirectory carries a small `run.json` manifest (run_id, run_type, state,
started_at, ended_at, ...) alongside that project's log files.

ponytail: the manifest schema is trusted, not validated against a formal
contract. It's the natural place for #9's process-start registry to write
to directly once it exists; a manifest.json for it is fabricated in tests
until then. Malformed/incomplete manifests are skipped rather than raising,
so one bad run (e.g. caught mid-write by #9 later) can't take down the rest
of the list.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .sources.base import PnL
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


def discover_rustle_runs(root: Path) -> list[RunSummary]:
    summaries = []
    for run_dir in _run_dirs(root):
        manifest = _load_manifest(run_dir)
        if manifest is None:
            continue
        log_path = run_dir / "events.jsonl"
        pnl = RustleAdapter(log_path).tail().pnl if log_path.is_file() else []
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
    for run_dir in _run_dirs(root):
        manifest = _load_manifest(run_dir)
        if manifest is None:
            continue
        log_path = run_dir / "trade_log.csv"
        pnl = (
            TickTraderTradeLogAdapter(log_path, symbol=manifest.get("symbol", "")).tail().pnl
            if log_path.is_file()
            else []
        )
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
