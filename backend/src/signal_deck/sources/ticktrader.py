"""Adapters for TickTrader-para's trade and latency logs.

Byte-offset tailing and marker/decoder handling live in `LogSourceAdapter`;
these classes only map decoded, complete lines onto the shared model.
Re-implemented from TickTrader-para's `dashboard/reader.py` incremental CSV
reading and JSONL latency reading, not imported as a dependency.
"""

from __future__ import annotations

import bisect
import csv
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import (
    EquityPoint,
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
        self._slot_total: dict[str, float] = {}  # latest realized+unrealized per slot

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
            realized = float(pnl_str)
            unrealized = float(row.get("unrealized_pnl") or 0.0)
            into.pnl.append(PnL(ts=ts, slot=slot, realized=realized, unrealized=unrealized))
            # Overview's equity curve is the account's total mark-to-market value
            # over time - the sum of every slot's latest realized+unrealized, not
            # just this one slot's own delta.
            self._slot_total[slot] = realized + unrealized
            into.equity.append(EquityPoint(ts=ts, equity=sum(self._slot_total.values())))

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

    ponytail: keeps every raw sample in memory (fine for a single trading-day
    session - tens of thousands of samples, not millions) but inserts each new
    sample into an already-sorted list (`bisect.insort`) and keeps a running
    sum, rather than resorting/resumming the full history on every line - a
    real multi-strategy session's ~32k-sample api_latency.jsonl made the old
    full-resort-per-line approach take minutes, not the seconds the comment
    here used to assume.
    """

    def __init__(self, path: Path, *, channel: str, **kwargs: Any) -> None:
        super().__init__(path, **kwargs)
        self._channel = channel
        self._durations: list[float] = []
        self._sum: float = 0.0

    def parse_line(self, line: bytes, into: ParsedLog) -> None:
        text = line.strip()
        if not text:
            return
        event: dict[str, Any] = json.loads(text)
        duration = event.get("duration_ms")
        if duration is None:
            return
        value = float(duration)
        bisect.insort(self._durations, value)
        self._sum += value
        into.add_latency(
            self._channel,
            LatencySample(
                ts=_parse_iso(event.get("ts", "")),
                mean=self._sum / len(self._durations),
                p99=_percentile(self._durations, 0.99),
                p999=_percentile(self._durations, 0.999),
            ),
        )
