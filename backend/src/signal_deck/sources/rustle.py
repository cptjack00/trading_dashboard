"""Adapter for rustle's `trade_log.jsonl`.

Frozen 12-column schema (`crates/tt-engine/src/trade_log_schema.rs`):
`slot_id, timestamp, type, best_bid, best_ask, spread, trade_price,
trade_side, matched_volume, position, action, pnl`. `type` is always
`"CONTROL"` in production (rustle's own doc comment on the schema module
confirms this - order-lifecycle rows, not raw market ticks), so it carries
no useful signal here; `action` is the real discriminator. Only
`action == "FILLED"` rows represent an actual fill - `OPEN`/`REPLACED`/
`CANCELLED` rows are order-lifecycle noise with an unchanged `pnl` (verified
against a real run: `pnl` only moves on `FILLED` rows). `pnl` itself is each
slot's running realized-pnl snapshot as of that row, not a per-fill delta -
confirmed by cross-checking a slot's last `FILLED` row against tt-replay's
own printed per-lane summary for that slot.

There is no `symbol` column, so a fill's instrument comes from the run's own
config TOML (`[[multi_symbol.slots]] slot_label -> config.symbol`), read
once when the adapter is constructed. `timestamp` is a bare `HH:MM:SS.mmm`
with no date, so the config's `from_date` anchors it to a real epoch time -
this only works for the single-date runs the New Run flow launches (backtest
launches pass `--out`, which itself only accepts a single resolved date).
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import EquityPoint, Fills, LatencySample, LogSourceAdapter, ParsedLog, PnL, PricePoint, Trade, WinRate


def _load_slot_symbols(config_path: Path) -> dict[str, str]:
    try:
        data = tomllib.loads(config_path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    slots = data.get("multi_symbol", {}).get("slots", [])
    return {
        slot["slot_label"]: slot["config"]["symbol"]
        for slot in slots
        if "slot_label" in slot and "symbol" in slot.get("config", {})
    }


def _load_run_date(config_path: Path) -> str | None:
    try:
        data = tomllib.loads(config_path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None
    date = data.get("from_date")
    return date if isinstance(date, str) else None


class RustleAdapter(LogSourceAdapter):
    def __init__(self, path: Path, *, config_path: Path | None = None, **kwargs: Any) -> None:
        super().__init__(path, **kwargs)
        self._slot_symbols = _load_slot_symbols(config_path) if config_path else {}
        run_date = _load_run_date(config_path) if config_path else None
        # Fallback: today, in whatever timezone this process runs in - wrong
        # calendar date for a historical backtest, but keeps the run usable
        # (relative time ordering within the run is still correct) instead of
        # refusing to render when a config can't be read.
        self._run_date = run_date or datetime.now().strftime("%Y%m%d")
        self._slot_realized: dict[str, float] = {}
        self._slot_open_realized: dict[str, float] = {}
        self._slot_wins: dict[str, int] = {}
        self._slot_losses: dict[str, int] = {}

    def _epoch(self, timestamp: str) -> float:
        dt = datetime.strptime(f"{self._run_date} {timestamp}", "%Y%m%d %H:%M:%S.%f")
        return dt.timestamp()

    def parse_line(self, line: bytes, into: ParsedLog) -> None:
        text = line.strip()
        if not text:
            return
        row: dict[str, Any] = json.loads(text)
        if row.get("action") != "FILLED":
            return

        slot: str = row["slot_id"]
        ts = self._epoch(row["timestamp"])
        symbol: str = self._slot_symbols.get(slot, slot)

        trade = Trade(ts=ts, symbol=symbol, side=row["trade_side"].lower(), price=row["trade_price"], qty=row["matched_volume"], slot=slot)
        into.trades.append(trade)
        into.add_price(symbol, PricePoint(ts=ts, price=row["trade_price"], trade=trade))

        realized = row["pnl"]
        self._slot_realized[slot] = realized
        into.pnl.append(PnL(ts=ts, slot=slot, realized=realized, unrealized=0.0))
        into.equity.append(EquityPoint(ts=ts, equity=sum(self._slot_realized.values())))

        # A win/loss is scored once per round trip (flat -> flat), not per fill:
        # an opening fill just moves the slot into a position, it doesn't realize
        # a trade outcome on its own. `position == 0` is rustle's own flat marker.
        if row["position"] == 0:
            open_realized = self._slot_open_realized.get(slot, 0.0)
            delta = realized - open_realized
            if delta > 0:
                self._slot_wins[slot] = self._slot_wins.get(slot, 0) + 1
            elif delta < 0:
                self._slot_losses[slot] = self._slot_losses.get(slot, 0) + 1
            self._slot_open_realized[slot] = realized
        into.win_rates.append(
            WinRate(ts=ts, slot=slot, wins=self._slot_wins.get(slot, 0), losses=self._slot_losses.get(slot, 0))
        )

        into.fills.append(Fills(ts=ts, slot=slot, count=1))


@dataclass(frozen=True)
class _HistTotals:
    """One label-set's cumulative Prometheus histogram state, as carried in a
    `health_log.jsonl` line - counters since process start, not a windowed sample."""

    count: int
    sum: float
    buckets: list[tuple[float, int]]


def _reduce_key(family: str, labels: dict[str, Any]) -> str:
    """Collapses a histogram's label-set to the channel name the Latency tab
    groups by. `md_latency` is labelled by feed; `api_request` by endpoint
    (across every response code, since a per-code split would fragment one
    real channel into a dozen near-empty ones for little benefit)."""
    return labels.get("feed") or labels.get("endpoint") or family


def _merge_hist(a: _HistTotals, b: _HistTotals) -> _HistTotals:
    return _HistTotals(
        count=a.count + b.count,
        sum=a.sum + b.sum,
        buckets=[(le, ca + cb) for (le, ca), (_, cb) in zip(a.buckets, b.buckets)],
    )


def _bucket_percentile(buckets: list[tuple[float, int]], total: int, pct: float) -> float:
    """Prometheus's own `histogram_quantile` approach: linearly interpolate
    within the bucket where the cumulative count crosses the target rank."""
    if total <= 0 or not buckets:
        return 0.0
    rank = pct * total
    prev_le, prev_count = 0.0, 0
    for le, count in buckets:
        if count >= rank:
            span = count - prev_count
            return le if span <= 0 else prev_le + (rank - prev_count) / span * (le - prev_le)
        prev_le, prev_count = le, count
    # Rank spills past the last finite bucket, i.e. into the implicit +Inf
    # bucket `health_log.jsonl` doesn't carry - clamp to the last bound as a
    # best-effort estimate rather than claiming a value we don't have.
    return prev_le


class RustleHealthLogAdapter(LogSourceAdapter):
    """Tails rustle's `health_log.jsonl` - a live/shadow-only, ~5s-cadence
    snapshot of the same in-process Prometheus histograms `/metrics` would
    scrape (`crates/tt-log/src/metrics.rs`), independent of whether anything
    is actually scraping.

    Each snapshot is *cumulative* since process start, so on its own it says
    nothing about recent latency - keep the previous cumulative totals per
    channel and diff consecutive snapshots to recover that interval's actual
    samples (mean, and p99/p999 interpolated from the delta bucket counts).
    """

    def __init__(self, path: Path, **kwargs: Any) -> None:
        super().__init__(path, **kwargs)
        self._previous: dict[str, _HistTotals] = {}

    def parse_line(self, line: bytes, into: ParsedLog) -> None:
        text = line.strip()
        if not text:
            return
        event: dict[str, Any] = json.loads(text)
        ts = event.get("ts_ms", 0) / 1000.0

        for family, prefix in (("md_latency", "md"), ("api_request", "api")):
            merged: dict[str, _HistTotals] = {}
            for entry in event.get(family, []):
                totals = _HistTotals(
                    count=entry["count"],
                    sum=entry["sum"],
                    buckets=[(b["le"], b["count"]) for b in entry["buckets"]],
                )
                key = _reduce_key(family, entry.get("labels", {}))
                merged[key] = _merge_hist(merged[key], totals) if key in merged else totals

            for key, totals in merged.items():
                channel = f"{prefix}:{key}"
                prior = self._previous.get(channel)
                self._previous[channel] = totals
                # A run's own restart resets the underlying counters to zero,
                # producing a negative delta here too - either way there's no
                # valid interval to report yet, just a new baseline to diff from.
                if prior is None or totals.count - prior.count <= 0:
                    continue

                delta_count = totals.count - prior.count
                delta_sum = totals.sum - prior.sum
                delta_buckets = [(le, c - pc) for (le, c), (_, pc) in zip(totals.buckets, prior.buckets)]
                into.add_latency(
                    channel,
                    LatencySample(
                        ts=ts,
                        mean=(delta_sum / delta_count) * 1000,
                        p99=_bucket_percentile(delta_buckets, delta_count, 0.99) * 1000,
                        p999=_bucket_percentile(delta_buckets, delta_count, 0.999) * 1000,
                    ),
                )
