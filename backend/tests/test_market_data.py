from __future__ import annotations

from datetime import datetime
from pathlib import Path

from signal_deck.sources.market_data import MarketTickAdapter


def _epoch(day: str, timestamp: str) -> float:
    return datetime.strptime(f"{day} {timestamp}", "%Y%m%d %H:%M:%S.%f").timestamp()


def test_tails_price_from_collector_tick_lines(tmp_path: Path):
    path = tmp_path / "20260828.txt"
    path.write_text(
        '09:00:00.100 {"price":1988.5,"qty":1,"side":"buy","ts":1.0}\n'
        '09:00:00.600 {"price":1989.0,"qty":2,"side":"sell"}\n'
    )
    adapter = MarketTickAdapter(path, symbol="41I1G9000", day="20260828")

    result = adapter.tail()

    prices = result.symbol_prices["41I1G9000"]
    assert [p.price for p in prices] == [1988.5, 1989.0]
    assert prices[0].ts == _epoch("20260828", "09:00:00.100")
    assert all(p.trade is None for p in prices)


def test_lines_missing_price_or_malformed_json_are_skipped(tmp_path: Path):
    path = tmp_path / "20260828.txt"
    path.write_text(
        '09:00:00.100 {"price":1.0,"qty":1,"side":"buy"}\n'
        "09:00:00.200 not-json\n"
        '09:00:00.300 {"qty":1,"side":"buy"}\n'
    )
    adapter = MarketTickAdapter(path, symbol="S", day="20260828")

    result = adapter.tail()

    assert [p.price for p in result.symbol_prices["S"]] == [1.0]
