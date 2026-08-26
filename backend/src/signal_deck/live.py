"""Live ingestion for the run-detail Overview tab.

An asyncio poll loop (~1s) tails each currently *live* run's log and fans new
equity/trade deltas out to subscribed SSE clients. Completed runs (stopped,
backtest, crashed) are never polled in the background - their Overview is
computed on demand, once, and cached, since their log never grows again.

A run whose log is detected as encrypted (magic-header sniff, no decode
attempted) is neither polled nor parsed on demand: no key-resolution
mechanism exists yet (see `sources/base.py`), so encrypted always means
"locked" here.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from .runs import _status, find_run, iter_manifests
from .sources.base import EquityPoint, LogSourceAdapter, Trade, is_encrypted
from .sources.rustle import RustleAdapter
from .sources.ticktrader import TickTraderTradeLogAdapter

POLL_INTERVAL_SECONDS = 1.0
EQUITY_HISTORY_LIMIT = 500
TRADE_TAPE_LIMIT = 50

RunKey = tuple[str, str]  # (project, run_id)


def _log_path(run_dir: Path, project: str) -> Path:
    return run_dir / ("events.jsonl" if project == "rustle" else "trade_log.csv")


def _make_adapter(log_path: Path, project: str, manifest: dict) -> LogSourceAdapter:
    if project == "rustle":
        return RustleAdapter(log_path)
    return TickTraderTradeLogAdapter(log_path, symbol=manifest.get("symbol", ""))


@dataclass
class Overview:
    run_id: str
    project: str
    status: str
    encrypted_locked: bool
    equity: list[EquityPoint] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)


@dataclass
class Delta:
    equity: list[EquityPoint]
    trades: list[Trade]


@dataclass
class _LiveState:
    adapter: LogSourceAdapter
    equity: list[EquityPoint] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    subscribers: list["asyncio.Queue[Delta | None]"] = field(default_factory=list)


class LiveIngestionManager:
    def __init__(self, rustle_root: Path | None, ticktrader_root: Path | None) -> None:
        self._rustle_root = rustle_root
        self._ticktrader_root = ticktrader_root
        self._live: dict[RunKey, _LiveState] = {}
        self._completed_cache: dict[RunKey, Overview] = {}
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run_forever(self) -> None:
        while True:
            self.poll_once()
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    def poll_once(self) -> None:
        """One tick: tail every currently-live run, drop ones that ended."""
        found = find_all_live(self._rustle_root, self._ticktrader_root)
        live_keys = set(found)

        for key in list(self._live):
            if key not in live_keys:
                state = self._live.pop(key)
                for queue in state.subscribers:
                    queue.put_nowait(None)  # sentinel: run ended, close the stream

        for key, (run_dir, manifest, project) in found.items():
            self._poll_run(key, run_dir, manifest, project)

    def _poll_run(self, key: RunKey, run_dir: Path, manifest: dict, project: str) -> None:
        log_path = _log_path(run_dir, project)
        if not log_path.is_file():
            return

        state = self._live.get(key)
        if state is None:
            if is_encrypted(log_path):
                return  # locked: nothing to tail
            state = _LiveState(adapter=_make_adapter(log_path, project, manifest))
            self._live[key] = state

        parsed = state.adapter.tail()
        if not parsed.equity and not parsed.trades:
            return

        state.equity = (state.equity + parsed.equity)[-EQUITY_HISTORY_LIMIT:]
        state.trades = (state.trades + parsed.trades)[-TRADE_TAPE_LIMIT:]
        for queue in state.subscribers:
            queue.put_nowait(Delta(equity=parsed.equity, trades=parsed.trades))

    def get_overview(self, project: str, run_id: str) -> Overview | None:
        key = (project, run_id)

        state = self._live.get(key)
        if state is not None:
            return Overview(
                run_id=run_id, project=project, status="live", encrypted_locked=False,
                equity=list(state.equity), trades=list(state.trades),
            )

        cached = self._completed_cache.get(key)
        if cached is not None:
            return cached

        found = find_run(self._rustle_root, self._ticktrader_root, project, run_id)
        if found is None:
            return None
        run_dir, manifest = found
        status = _status(run_type=manifest["run_type"], state=manifest["state"])
        log_path = _log_path(run_dir, project)

        if not log_path.is_file():
            overview = Overview(run_id=run_id, project=project, status=status, encrypted_locked=False)
        elif is_encrypted(log_path):
            overview = Overview(run_id=run_id, project=project, status=status, encrypted_locked=True)
        else:
            adapter = _make_adapter(log_path, project, manifest)
            parsed = adapter.tail()
            overview = Overview(
                run_id=run_id, project=project, status=status, encrypted_locked=False,
                equity=parsed.equity[-EQUITY_HISTORY_LIMIT:], trades=parsed.trades[-TRADE_TAPE_LIMIT:],
            )

        if status != "live":
            self._completed_cache[key] = overview
        return overview

    def subscribe(self, project: str, run_id: str) -> "asyncio.Queue[Delta | None] | None":
        state = self._live.get((project, run_id))
        if state is None:
            return None
        queue: "asyncio.Queue[Delta | None]" = asyncio.Queue()
        state.subscribers.append(queue)
        return queue

    def unsubscribe(self, project: str, run_id: str, queue: "asyncio.Queue[Delta | None]") -> None:
        state = self._live.get((project, run_id))
        if state is not None and queue in state.subscribers:
            state.subscribers.remove(queue)


def find_all_live(
    rustle_root: Path | None, ticktrader_root: Path | None
) -> dict[RunKey, tuple[Path, dict, str]]:
    """Every currently-live run's (run_dir, manifest, project), keyed by (project, run_id)."""
    live: dict[RunKey, tuple[Path, dict, str]] = {}
    for project, root in (("rustle", rustle_root), ("ticktrader", ticktrader_root)):
        if root is None:
            continue
        for run_dir, manifest in iter_manifests(root):
            if _status(run_type=manifest["run_type"], state=manifest["state"]) == "live":
                live[(project, manifest["run_id"])] = (run_dir, manifest, project)
    return live
