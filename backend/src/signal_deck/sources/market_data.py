"""Adapter for the independent market-data collector's per-symbol tick files.

`tt-collect` (rustle) and `collect_mqtt_data_v4.py` (ticktrader) run outside
this dashboard entirely, each writing `{cwd}/data/{symbol}/tick_data/
{YYYYMMDD}.txt` - one line per trade: `HH:MM:SS.mmm {"price":<p>,...}` (same
format for both). Tailing this instead of a strategy's own trade log gives
one real price series per instrument, independent of how many strategy slots
(or none at all) happen to be trading it right now.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import LogSourceAdapter, ParsedLog, PricePoint


class MarketTickAdapter(LogSourceAdapter):
    def __init__(self, path: Path, *, symbol: str, day: str, **kwargs: Any) -> None:
        super().__init__(path, **kwargs)
        self._symbol = symbol
        self._day = day

    def _epoch(self, timestamp: str) -> float:
        dt = datetime.strptime(f"{self._day} {timestamp}", "%Y%m%d %H:%M:%S.%f")
        return dt.timestamp()

    def parse_line(self, line: bytes, into: ParsedLog) -> None:
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            return
        ts_str, _, payload = text.partition(" ")
        if not payload:
            return
        try:
            row: dict[str, Any] = json.loads(payload)
        except json.JSONDecodeError:
            return
        price = row.get("price")
        if price is None:
            return
        into.add_price(self._symbol, PricePoint(ts=self._epoch(ts_str), price=float(price)))
