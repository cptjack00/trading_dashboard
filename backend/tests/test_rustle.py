from __future__ import annotations

from pathlib import Path

from signal_deck.sources.rustle import RustleAdapter

FIXTURE = Path(__file__).parent / "fixtures" / "rustle_sample.jsonl"


def test_normalizes_all_event_types(tmp_path: Path):
    path = tmp_path / "rustle.jsonl"
    path.write_bytes(FIXTURE.read_bytes())
    adapter = RustleAdapter(path)

    result = adapter.tail()

    assert len(result.status) == 1
    assert result.status[0].run_id == "run-redacted-1"

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.symbol == "XYZ-PERP"
    assert trade.side == "buy"
    assert trade.price == 10.5

    prices = result.symbol_prices["XYZ-PERP"]
    assert len(prices) == 2
    assert prices[0].trade == trade  # trade line produces a marked price point
    assert prices[1].trade is None  # plain price line has no marker

    assert result.equity[0].equity == 500.25
    assert result.health[0].component == "ws-feed"
    assert result.win_rates[0].wins == 3
    assert result.pnl[0].realized == 12.5
    assert result.fills[0].count == 4

    assert result.channel_latency["ws"][0].p99 == 9.4
    assert result.channel_latency["api"][0].p999 == 75.0


def test_chunked_feed_matches_single_feed(tmp_path: Path):
    content = FIXTURE.read_bytes()

    whole_path = tmp_path / "whole.jsonl"
    whole_path.write_bytes(content)
    whole_result = RustleAdapter(whole_path).tail()

    chunked_path = tmp_path / "chunked.jsonl"
    chunked_path.write_bytes(b"")
    chunked_adapter = RustleAdapter(chunked_path)
    midpoint = len(content) // 2
    combined_trades = []
    combined_prices: list = []
    for chunk in (content[:midpoint], content[midpoint:]):
        with chunked_path.open("ab") as f:
            f.write(chunk)
        step = chunked_adapter.tail()
        combined_trades.extend(step.trades)
        combined_prices.extend(step.symbol_prices.get("XYZ-PERP", []))

    assert combined_trades == whole_result.trades
    assert combined_prices == whole_result.symbol_prices["XYZ-PERP"]


def test_line_cut_mid_write_is_not_parsed_until_complete(tmp_path: Path):
    lines = FIXTURE.read_bytes().splitlines(keepends=True)
    path = tmp_path / "rustle.jsonl"
    path.write_bytes(b"")
    adapter = RustleAdapter(path)

    first_line = lines[0]
    with path.open("ab") as f:
        f.write(first_line[: len(first_line) // 2])  # cut mid-write, no trailing \n
    mid_write = adapter.tail()
    assert mid_write.status == []

    with path.open("ab") as f:
        f.write(first_line[len(first_line) // 2 :])  # write completes
    completed = adapter.tail()
    assert len(completed.status) == 1
