"""Adapters for TickTrader-para's trade and latency logs.

Byte-offset tailing and marker/decoder handling live in `LogSourceAdapter`;
these classes only map decoded, complete lines onto the shared model.
Re-implemented from TickTrader-para's `dashboard/reader.py` incremental CSV
reading and JSONL latency reading, not imported as a dependency.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import (
    Fills,
    LatencySample,
    LogSourceAdapter,
    ParsedLog,
    PnL,
    PricePoint,
    Trade,
)

_MIDNIGHT = datetime(1900, 1, 1)


def _parse_time_of_day(text: str) -> float:
    """TickTrader-para's trade log has no date, only HH:MM:SS.mmm. Returns
    seconds-since-midnight.

    ponytail: sessions never cross midnight in practice; add a date column
    upstream if that changes.
    """
    if not text:
        return 0.0
    return (datetime.strptime(text, "%H:%M:%S.%f") - _MIDNIGHT).total_seconds()


def _parse_iso(text: str) -> float:
    return datetime.fromisoformat(text).timestamp() if text else 0.0


def _percentile(ordered: list[float], pct: float) -> float:
    if not ordered:
        return 0.0
    idx = min(len(ordered) - 1, max(0, math.ceil(pct * len(ordered)) - 1))
    return ordered[idx]


class TickTraderTradeLogAdapter(LogSourceAdapter):
    """Reads a per-slot `trade_log.csv`. Column order/set varies across
    strategy versions, so rows are looked up by header name, not position.
    """

    def __init__(self, path: Path, *, symbol: str, default_slot: str = "default", **kwargs: Any) -> None:
        super().__init__(path, **kwargs)
        self._symbol = symbol
        self._default_slot = default_slot
        self._columns: list[str] | None = None

    def parse_line(self, line: bytes, into: ParsedLog) -> None:
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            return
        values = next(csv.reader([text]))
        if self._columns is None:
            self._columns = values
            return
        if len(values) != len(self._columns):
            return
        self._handle_row(dict(zip(self._columns, values)), into)

    def _handle_row(self, row: dict[str, str], into: ParsedLog) -> None:
        ts = _parse_time_of_day(row.get("timestamp", ""))
        slot = row.get("slot_id") or self._default_slot

        # Emitted on every row that carries a pnl value, not just trades, so this
        # is a live per-slot equity curve rather than only per-trade deltas.
        pnl_str = row.get("pnl")
        if pnl_str:
            into.pnl.append(
                PnL(ts=ts, slot=slot, realized=float(pnl_str), unrealized=float(row.get("unrealized_pnl") or 0.0))
            )

        # ponytail: trade_log.csv carries no independent quote/tick stream, only
        # TRADE/FILL rows - so every matched-price point this adapter emits also
        # carries a trade marker. A Market-tab "price movement" line built from
        # this adapter is 1:1 with its own trade markers, not movement between
        # fills; add a quote-tick handler here if trade_log.csv ever gains one.
        trade_price = row.get("trade_price")
        if row.get("type") in ("TRADE", "FILL") and trade_price:
            qty = float(row.get("matched_volume") or 0.0)
            trade = Trade(
                ts=ts,
                symbol=self._symbol,
                side=(row.get("trade_side") or "").lower(),
                price=float(trade_price),
                qty=qty,
                slot=slot,
            )
            into.trades.append(trade)
            into.add_price(self._symbol, PricePoint(ts=ts, price=trade.price, trade=trade))
            if qty:
                into.fills.append(Fills(ts=ts, slot=slot, count=int(qty)))


class TickTraderLatencyAdapter(LogSourceAdapter):
    """Reads a `*_latency.jsonl` file (one raw `duration_ms` sample per line)
    and emits a running mean/p99/p999 for `channel` on every new sample.

    ponytail: keeps every raw sample in memory and resorts the full history
    on each new line; fine for a single trading-day session, switch to a
    streaming quantile estimator with a bounded window if sessions grow
    long-running.
    """

    def __init__(self, path: Path, *, channel: str, **kwargs: Any) -> None:
        super().__init__(path, **kwargs)
        self._channel = channel
        self._durations: list[float] = []

    def parse_line(self, line: bytes, into: ParsedLog) -> None:
        text = line.strip()
        if not text:
            return
        event: dict[str, Any] = json.loads(text)
        duration = event.get("duration_ms")
        if duration is None:
            return
        self._durations.append(float(duration))
        ordered = sorted(self._durations)
        into.add_latency(
            self._channel,
            LatencySample(
                ts=_parse_iso(event.get("ts", "")),
                mean=statistics.fmean(ordered),
                p99=_percentile(ordered, 0.99),
                p999=_percentile(ordered, 0.999),
            ),
        )
