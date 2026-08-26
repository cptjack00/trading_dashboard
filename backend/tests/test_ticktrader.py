from __future__ import annotations

from pathlib import Path

from signal_deck.sources.ticktrader import TickTraderLatencyAdapter, TickTraderTradeLogAdapter

TRADE_FIXTURE = Path(__file__).parent / "fixtures" / "ticktrader_trade_log.csv"
LATENCY_FIXTURE = Path(__file__).parent / "fixtures" / "ticktrader_latency.jsonl"


def test_normalizes_trade_and_pnl_rows(tmp_path: Path):
    path = tmp_path / "trade_log.csv"
    path.write_bytes(TRADE_FIXTURE.read_bytes())
    adapter = TickTraderTradeLogAdapter(path, symbol="XYZ-PERP", default_slot="slot_0")

    result = adapter.tail()

    assert len(result.trades) == 2
    buy, sell = result.trades
    assert buy.symbol == "XYZ-PERP"
    assert buy.side == "buy"
    assert buy.price == 100.1
    assert buy.qty == 5.0
    assert buy.slot == "slot_0"
    assert sell.side == "sell"
    assert sell.price == 100.3

    prices = result.symbol_prices["XYZ-PERP"]
    assert len(prices) == 2
    assert prices[0].trade == buy
    assert prices[1].trade == sell

    assert [p.realized for p in result.pnl] == [0.0, 0.0, 1.0]  # TICK row + 2 TRADE rows
    assert [f.count for f in result.fills] == [5, 5]


def test_fill_type_rows_are_normalized_like_trade_rows(tmp_path: Path):
    path = tmp_path / "trade_log.csv"
    header, tick, buy, _ = TRADE_FIXTURE.read_text().splitlines()
    fill_row = buy.replace("TRADE", "FILL", 1)
    path.write_text("\n".join([header, tick, fill_row]) + "\n")
    adapter = TickTraderTradeLogAdapter(path, symbol="XYZ-PERP")

    result = adapter.tail()

    assert len(result.trades) == 1
    assert [f.count for f in result.fills] == [5]


def test_malformed_row_with_wrong_column_count_is_skipped(tmp_path: Path):
    header, tick, buy, _ = TRADE_FIXTURE.read_text().splitlines()
    path = tmp_path / "trade_log.csv"
    path.write_text("\n".join([header, tick, "not,enough,columns", buy]) + "\n")
    adapter = TickTraderTradeLogAdapter(path, symbol="XYZ-PERP")

    result = adapter.tail()

    assert len(result.trades) == 1  # malformed row skipped, valid rows still parsed


def test_trade_log_chunked_feed_matches_single_feed(tmp_path: Path):
    content = TRADE_FIXTURE.read_bytes()

    whole_path = tmp_path / "whole.csv"
    whole_path.write_bytes(content)
    whole_result = TickTraderTradeLogAdapter(whole_path, symbol="XYZ-PERP").tail()

    chunked_path = tmp_path / "chunked.csv"
    chunked_path.write_bytes(b"")
    chunked_adapter = TickTraderTradeLogAdapter(chunked_path, symbol="XYZ-PERP")
    midpoint = len(content) // 2
    combined_trades = []
    for chunk in (content[:midpoint], content[midpoint:]):
        with chunked_path.open("ab") as f:
            f.write(chunk)
        combined_trades.extend(chunked_adapter.tail().trades)

    assert combined_trades == whole_result.trades


def test_trade_row_cut_mid_write_is_not_parsed_until_complete(tmp_path: Path):
    lines = TRADE_FIXTURE.read_bytes().splitlines(keepends=True)
    path = tmp_path / "trade_log.csv"
    path.write_bytes(b"")
    adapter = TickTraderTradeLogAdapter(path, symbol="XYZ-PERP")

    with path.open("ab") as f:
        f.write(lines[0])  # header
        f.write(lines[1])  # TICK row
        second_trade_row = lines[2]
        f.write(second_trade_row[: len(second_trade_row) // 2])  # cut mid-write
    mid_write = adapter.tail()
    assert mid_write.trades == []

    with path.open("ab") as f:
        f.write(second_trade_row[len(second_trade_row) // 2 :])
    completed = adapter.tail()
    assert len(completed.trades) == 1


def test_normalizes_latency_samples(tmp_path: Path):
    path = tmp_path / "latency.jsonl"
    path.write_bytes(LATENCY_FIXTURE.read_bytes())
    adapter = TickTraderLatencyAdapter(path, channel="api")

    result = adapter.tail()

    samples = result.channel_latency["api"]
    assert len(samples) == 3
    last = samples[-1]
    assert last.mean == 20.0
    assert last.p99 == 30.0
    assert last.p999 == 30.0


def test_latency_chunked_feed_matches_single_feed(tmp_path: Path):
    content = LATENCY_FIXTURE.read_bytes()

    whole_path = tmp_path / "whole.jsonl"
    whole_path.write_bytes(content)
    whole_result = TickTraderLatencyAdapter(whole_path, channel="api").tail()

    chunked_path = tmp_path / "chunked.jsonl"
    chunked_path.write_bytes(b"")
    chunked_adapter = TickTraderLatencyAdapter(chunked_path, channel="api")
    midpoint = len(content) // 2
    combined_samples = []
    for chunk in (content[:midpoint], content[midpoint:]):
        with chunked_path.open("ab") as f:
            f.write(chunk)
        combined_samples.extend(chunked_adapter.tail().channel_latency.get("api", []))

    assert combined_samples == whole_result.channel_latency["api"]


def test_latency_line_cut_mid_write_is_not_parsed_until_complete(tmp_path: Path):
    lines = LATENCY_FIXTURE.read_bytes().splitlines(keepends=True)
    path = tmp_path / "latency.jsonl"
    path.write_bytes(b"")
    adapter = TickTraderLatencyAdapter(path, channel="api")

    first_line = lines[0]
    with path.open("ab") as f:
        f.write(first_line[: len(first_line) // 2])
    mid_write = adapter.tail()
    assert mid_write.channel_latency == {}

    with path.open("ab") as f:
        f.write(first_line[len(first_line) // 2 :])
    completed = adapter.tail()
    assert len(completed.channel_latency["api"]) == 1
