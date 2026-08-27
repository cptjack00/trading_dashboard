from __future__ import annotations

from datetime import datetime
from pathlib import Path

from signal_deck.sources.rustle import RustleAdapter

FIXTURE = Path(__file__).parent / "fixtures" / "rustle_sample.jsonl"
CONFIG = Path(__file__).parent / "fixtures" / "rustle_sample_config.toml"


def _epoch(timestamp: str) -> float:
    return datetime.strptime(f"20260101 {timestamp}", "%Y%m%d %H:%M:%S.%f").timestamp()


def test_ignores_non_filled_rows_and_maps_filled_rows(tmp_path: Path):
    path = tmp_path / "trade_log.jsonl"
    path.write_bytes(FIXTURE.read_bytes())
    adapter = RustleAdapter(path, config_path=CONFIG)

    result = adapter.tail()

    # 4 rows in the fixture, only 3 are action=="FILLED" - the OPEN row
    # produces no trade/pnl/fill/equity/win_rate entry at all.
    assert len(result.trades) == 3

    first = result.trades[0]
    assert first.ts == _epoch("09:00:01.200")
    assert first.symbol == "XYZ-PERP"  # resolved via config, not the slot_id
    assert first.side == "buy"
    assert first.price == 10.1
    assert first.qty == 2
    assert first.slot == "s1"

    assert result.trades[1].side == "sell"
    assert result.trades[2].symbol == "ABC-PERP"

    prices = result.symbol_prices["XYZ-PERP"]
    assert len(prices) == 2
    assert prices[0].trade == first


def test_pnl_is_the_running_per_slot_snapshot_not_a_delta(tmp_path: Path):
    path = tmp_path / "trade_log.jsonl"
    path.write_bytes(FIXTURE.read_bytes())
    result = RustleAdapter(path, config_path=CONFIG).tail()

    assert [p.realized for p in result.pnl] == [5.0, 3.0, 1.5]
    assert all(p.unrealized == 0.0 for p in result.pnl)


def test_equity_is_the_cross_slot_running_total(tmp_path: Path):
    path = tmp_path / "trade_log.jsonl"
    path.write_bytes(FIXTURE.read_bytes())
    result = RustleAdapter(path, config_path=CONFIG).tail()

    # s1: 5.0, then updates to 3.0 (not 5.0+3.0 - pnl is a snapshot); s2 adds 1.5.
    assert [e.equity for e in result.equity] == [5.0, 3.0, 4.5]


def test_win_rate_derived_from_realized_pnl_delta_between_fills(tmp_path: Path):
    path = tmp_path / "trade_log.jsonl"
    path.write_bytes(FIXTURE.read_bytes())
    result = RustleAdapter(path, config_path=CONFIG).tail()

    by_slot_final = {}
    for w in result.win_rates:
        by_slot_final[w.slot] = w
    # s1: first fill 0 -> 5.0 (win), second fill 5.0 -> 3.0 (loss).
    assert by_slot_final["s1"].wins == 1
    assert by_slot_final["s1"].losses == 1
    # s2: first fill 0 -> 1.5 (win).
    assert by_slot_final["s2"].wins == 1
    assert by_slot_final["s2"].losses == 0


def test_fills_increment_by_one_per_filled_row(tmp_path: Path):
    path = tmp_path / "trade_log.jsonl"
    path.write_bytes(FIXTURE.read_bytes())
    result = RustleAdapter(path, config_path=CONFIG).tail()

    assert [f.count for f in result.fills] == [1, 1, 1]
    assert [f.slot for f in result.fills] == ["s1", "s1", "s2"]


def test_missing_config_falls_back_to_slot_id_as_symbol(tmp_path: Path):
    path = tmp_path / "trade_log.jsonl"
    path.write_bytes(FIXTURE.read_bytes())
    result = RustleAdapter(path).tail()  # no config_path

    assert result.trades[0].symbol == "s1"
    assert result.trades[2].symbol == "s2"


def test_chunked_feed_matches_single_feed(tmp_path: Path):
    content = FIXTURE.read_bytes()

    whole_path = tmp_path / "whole.jsonl"
    whole_path.write_bytes(content)
    whole_result = RustleAdapter(whole_path, config_path=CONFIG).tail()

    chunked_path = tmp_path / "chunked.jsonl"
    chunked_path.write_bytes(b"")
    chunked_adapter = RustleAdapter(chunked_path, config_path=CONFIG)
    midpoint = len(content) // 2
    combined_trades = []
    for chunk in (content[:midpoint], content[midpoint:]):
        with chunked_path.open("ab") as f:
            f.write(chunk)
        step = chunked_adapter.tail()
        combined_trades.extend(step.trades)

    assert combined_trades == whole_result.trades


def test_line_cut_mid_write_is_not_parsed_until_complete(tmp_path: Path):
    lines = FIXTURE.read_bytes().splitlines(keepends=True)
    path = tmp_path / "trade_log.jsonl"
    path.write_bytes(b"")
    adapter = RustleAdapter(path, config_path=CONFIG)

    # The fixture's 2nd line is the first FILLED row.
    first_two = lines[0] + lines[1]
    with path.open("ab") as f:
        f.write(first_two[: len(first_two) // 2])  # cut mid-write, no trailing \n
    mid_write = adapter.tail()
    assert mid_write.trades == []

    with path.open("ab") as f:
        f.write(first_two[len(first_two) // 2 :])  # write completes
    completed = adapter.tail()
    assert len(completed.trades) == 1
