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
from typing import TypeVar

from .runs import _status, find_run, iter_manifests
from .sources.base import (
    EquityPoint,
    Fills,
    HealthSample,
    LatencySample,
    LogSourceAdapter,
    PnL,
    PricePoint,
    Trade,
    WinRate,
    is_encrypted,
)
from .sources.rustle import RustleAdapter
from .sources.ticktrader import TickTraderLatencyAdapter, TickTraderTradeLogAdapter

POLL_INTERVAL_SECONDS = 1.0
EQUITY_HISTORY_LIMIT = 500
TRADE_TAPE_LIMIT = 50
PRICE_HISTORY_LIMIT = 500

# ponytail: TickTrader-para's two known telemetry channels, hardcoded rather than
# discovered - add a third only if a real channel shows up.
TICKTRADER_LATENCY_CHANNELS = ("data", "api")

RunKey = tuple[str, str]  # (project, run_id)


def _log_path(run_dir: Path, project: str) -> Path:
    return run_dir / "trade_log.jsonl" if project == "rustle" else run_dir / "trade_log.csv"


def _make_adapter(log_path: Path, project: str, manifest: dict) -> LogSourceAdapter:
    if project == "rustle":
        config_path = manifest.get("config_path")
        return RustleAdapter(log_path, config_path=Path(config_path) if config_path else None)
    return TickTraderTradeLogAdapter(log_path, symbol=manifest.get("symbol", ""))


def _ticktrader_latency_path(run_dir: Path, channel: str) -> Path:
    return run_dir / f"{channel}_latency.jsonl"


def _ticktrader_latency_snapshot(run_dir: Path) -> dict[str, list[LatencySample]]:
    """One-shot read of every present TickTrader-para latency channel file."""
    result: dict[str, list[LatencySample]] = {}
    for channel in TICKTRADER_LATENCY_CHANNELS:
        path = _ticktrader_latency_path(run_dir, channel)
        if not path.is_file():
            continue
        samples = TickTraderLatencyAdapter(path, channel=channel).tail().channel_latency.get(channel, [])
        if samples:
            result[channel] = samples
    return result


_Slotted = TypeVar("_Slotted", PnL, WinRate)


def _merge_latest_by_slot(target: dict[str, _Slotted], entries: list[_Slotted]) -> None:
    """Fold in new PnL/WinRate samples, keeping only the latest (already-cumulative) one per slot."""
    for entry in entries:
        target[entry.slot] = entry


def _merge_latest_health(target: dict[str, HealthSample], entries: list[HealthSample]) -> None:
    """Fold in new HealthSample entries, keeping only the latest one per component."""
    for entry in entries:
        target[entry.component] = entry


def _merge_fill_counts(target: dict[str, Fills], entries: list[Fills]) -> None:
    """Fold in new Fills samples, summing counts per slot rather than keeping only the latest."""
    for entry in entries:
        prior = target.get(entry.slot)
        total = (prior.count if prior else 0) + entry.count
        target[entry.slot] = Fills(ts=entry.ts, slot=entry.slot, count=total)


def _merge_capped(target: dict[str, list], new: dict[str, list], limit: int) -> None:
    for key, values in new.items():
        if not values:
            continue
        target[key] = (target.get(key, []) + values)[-limit:]


def _merge_extend(target: dict[str, list], new: dict[str, list]) -> None:
    # ponytail: latency samples arrive at network-call cadence, not tick cadence,
    # and TickTraderLatencyAdapter itself already keeps a session's full history
    # uncapped - matching that here keeps "trend over the run's lifetime" true.
    for key, values in new.items():
        if not values:
            continue
        target[key] = target.get(key, []) + values


@dataclass
class Overview:
    run_id: str
    project: str
    status: str
    encrypted_locked: bool
    # Whether this run is actively background-polled right now, i.e. whether
    # `/stream` would accept a subscription for it. False for every on-demand
    # (completed-run or not-yet-polled) computation - see `get_overview`.
    live_tracked: bool = False
    equity: list[EquityPoint] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    pnl: list[PnL] = field(default_factory=list)
    win_rates: list[WinRate] = field(default_factory=list)
    fills: list[Fills] = field(default_factory=list)
    health: list[HealthSample] = field(default_factory=list)
    symbol_prices: dict[str, list[PricePoint]] = field(default_factory=dict)
    channel_latency: dict[str, list[LatencySample]] = field(default_factory=dict)


@dataclass
class Delta:
    equity: list[EquityPoint]
    trades: list[Trade]
    pnl: list[PnL] = field(default_factory=list)
    win_rates: list[WinRate] = field(default_factory=list)
    fills: list[Fills] = field(default_factory=list)
    health: list[HealthSample] = field(default_factory=list)
    symbol_prices: dict[str, list[PricePoint]] = field(default_factory=dict)
    channel_latency: dict[str, list[LatencySample]] = field(default_factory=dict)


@dataclass
class _LiveState:
    project: str
    adapter: LogSourceAdapter | None
    encrypted_locked: bool = False
    equity: list[EquityPoint] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    latest_pnl: dict[str, PnL] = field(default_factory=dict)
    latest_win_rates: dict[str, WinRate] = field(default_factory=dict)
    fill_totals: dict[str, Fills] = field(default_factory=dict)
    latest_health: dict[str, HealthSample] = field(default_factory=dict)
    symbol_prices: dict[str, list[PricePoint]] = field(default_factory=dict)
    channel_latency: dict[str, list[LatencySample]] = field(default_factory=dict)
    latency_adapters: dict[str, LogSourceAdapter] = field(default_factory=dict)
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
            encrypted = is_encrypted(log_path)
            if encrypted and project == "rustle":
                # rustle interleaves trades/health/latency in one file - an encrypted
                # log locks all of it, since there's nothing separable to tail.
                return
            adapter = None if encrypted else _make_adapter(log_path, project, manifest)
            state = _LiveState(project=project, adapter=adapter, encrypted_locked=encrypted)
            self._live[key] = state

        equity: list[EquityPoint] = []
        trades: list[Trade] = []
        pnl: list[PnL] = []
        win_rates: list[WinRate] = []
        fills: list[Fills] = []
        health: list[HealthSample] = []
        prices: dict[str, list[PricePoint]] = {}
        latency: dict[str, list[LatencySample]] = {}

        if state.adapter is not None:
            parsed = state.adapter.tail()
            equity, trades = parsed.equity, parsed.trades
            pnl, win_rates, fills, health = parsed.pnl, parsed.win_rates, parsed.fills, parsed.health
            prices = parsed.symbol_prices
            if project == "rustle":
                latency = parsed.channel_latency

        if project == "ticktrader":
            # TickTrader-para's latency channels live in their own files, so they keep
            # flowing even when the trade log itself is encrypted-locked.
            latency = self._poll_ticktrader_latency(run_dir, state)

        if not any((equity, trades, pnl, win_rates, fills, health, prices, latency)):
            return

        state.equity = (state.equity + equity)[-EQUITY_HISTORY_LIMIT:]
        state.trades = (state.trades + trades)[-TRADE_TAPE_LIMIT:]
        _merge_latest_by_slot(state.latest_pnl, pnl)
        _merge_latest_by_slot(state.latest_win_rates, win_rates)
        _merge_fill_counts(state.fill_totals, fills)
        _merge_latest_health(state.latest_health, health)
        _merge_capped(state.symbol_prices, prices, PRICE_HISTORY_LIMIT)
        _merge_extend(state.channel_latency, latency)

        delta = Delta(
            equity=equity, trades=trades, pnl=pnl, win_rates=win_rates, fills=fills, health=health,
            symbol_prices=prices, channel_latency=latency,
        )
        for queue in state.subscribers:
            queue.put_nowait(delta)

    def _poll_ticktrader_latency(
        self, run_dir: Path, state: "_LiveState"
    ) -> dict[str, list[LatencySample]]:
        new: dict[str, list[LatencySample]] = {}
        for channel in TICKTRADER_LATENCY_CHANNELS:
            path = _ticktrader_latency_path(run_dir, channel)
            if not path.is_file():
                continue
            adapter = state.latency_adapters.get(channel)
            if adapter is None:
                adapter = TickTraderLatencyAdapter(path, channel=channel)
                state.latency_adapters[channel] = adapter
            samples = adapter.tail().channel_latency.get(channel, [])
            if samples:
                new[channel] = samples
        return new

    def get_overview(self, project: str, run_id: str) -> Overview | None:
        key = (project, run_id)

        state = self._live.get(key)
        if state is not None:
            return Overview(
                run_id=run_id, project=project, status="live", encrypted_locked=state.encrypted_locked,
                live_tracked=True,
                equity=list(state.equity), trades=list(state.trades),
                pnl=list(state.latest_pnl.values()), win_rates=list(state.latest_win_rates.values()),
                fills=list(state.fill_totals.values()), health=list(state.latest_health.values()),
                symbol_prices={k: list(v) for k, v in state.symbol_prices.items()},
                channel_latency={k: list(v) for k, v in state.channel_latency.items()},
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
        # TickTrader-para's latency channels live in their own files, so they stay
        # readable regardless of the trade log's encryption state; rustle has no
        # such separation (see `_poll_run`).
        channel_latency = _ticktrader_latency_snapshot(run_dir) if project == "ticktrader" else {}

        if not log_path.is_file():
            overview = Overview(
                run_id=run_id, project=project, status=status, encrypted_locked=False,
                channel_latency=channel_latency,
            )
        elif is_encrypted(log_path):
            overview = Overview(
                run_id=run_id, project=project, status=status, encrypted_locked=True,
                channel_latency=channel_latency,
            )
        else:
            adapter = _make_adapter(log_path, project, manifest)
            parsed = adapter.tail()
            latest_pnl: dict[str, PnL] = {}
            latest_win_rates: dict[str, WinRate] = {}
            fill_totals: dict[str, Fills] = {}
            latest_health: dict[str, HealthSample] = {}
            _merge_latest_by_slot(latest_pnl, parsed.pnl)
            _merge_latest_by_slot(latest_win_rates, parsed.win_rates)
            _merge_fill_counts(fill_totals, parsed.fills)
            _merge_latest_health(latest_health, parsed.health)
            if project == "rustle":
                channel_latency = dict(parsed.channel_latency)
            overview = Overview(
                run_id=run_id, project=project, status=status, encrypted_locked=False,
                equity=parsed.equity[-EQUITY_HISTORY_LIMIT:], trades=parsed.trades[-TRADE_TAPE_LIMIT:],
                pnl=list(latest_pnl.values()), win_rates=list(latest_win_rates.values()),
                fills=list(fill_totals.values()), health=list(latest_health.values()),
                symbol_prices={k: v[-PRICE_HISTORY_LIMIT:] for k, v in parsed.symbol_prices.items()},
                channel_latency=channel_latency,
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
