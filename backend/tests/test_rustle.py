from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from signal_deck.sources.rustle import RustleAdapter, RustleHealthLogAdapter

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


def test_win_rate_derived_from_realized_pnl_delta_over_flat_to_flat_round_trips(tmp_path: Path):
    path = tmp_path / "trade_log.jsonl"
    path.write_bytes(FIXTURE.read_bytes())
    result = RustleAdapter(path, config_path=CONFIG).tail()

    by_slot_final = {}
    for w in result.win_rates:
        by_slot_final[w.slot] = w
    # s1: opens (position 1, pnl 5.0, not yet scored), closes flat (position 0,
    # pnl 3.0) - one round trip, net 0 -> 3.0 = a single win.
    assert by_slot_final["s1"].wins == 1
    assert by_slot_final["s1"].losses == 0
    # s2: opens (position 1) and never returns to flat in this fixture - no
    # completed round trip yet, so no win/loss is scored.
    assert by_slot_final["s2"].wins == 0
    assert by_slot_final["s2"].losses == 0


def test_win_rate_marks_a_slot_open_while_mid_position(tmp_path: Path):
    # s1's first FILLED row opens a position (never scored as a win/loss on
    # its own) and its second closes flat (the completed round trip, scored);
    # s2's only row opens and never returns to flat in this fixture. A slot
    # marked `open` still carries its running PnL - just not yet counted as
    # that round trip's eventual win or loss.
    path = tmp_path / "trade_log.jsonl"
    path.write_bytes(FIXTURE.read_bytes())
    result = RustleAdapter(path, config_path=CONFIG).tail()

    by_slot = {}
    for w in result.win_rates:
        by_slot.setdefault(w.slot, []).append(w)

    assert [w.open for w in by_slot["s1"]] == [True, False]
    assert [w.open for w in by_slot["s2"]] == [True]


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


def _health_line(ts_ms: int, *, endpoint: str, count: int, total: float, buckets: list[tuple[float, int]]) -> str:
    return json.dumps(
        {
            "ts_ms": ts_ms,
            "heartbeat": True,
            "md_latency": [],
            "api_request": [
                {
                    "labels": {"endpoint": endpoint, "code": "200"},
                    "count": count,
                    "sum": total,
                    "buckets": [{"le": le, "count": c} for le, c in buckets],
                }
            ],
        }
    )


def test_health_log_first_snapshot_is_a_baseline_not_a_sample(tmp_path: Path):
    # Prometheus histograms are cumulative since process start - a lone snapshot
    # says nothing about *recent* latency, so it must only seed the diff base.
    path = tmp_path / "health_log.jsonl"
    path.write_text(
        _health_line(1000, endpoint="order_place", count=3, total=0.06, buckets=[(0.01, 0), (0.025, 3), (0.05, 3)])
        + "\n"
    )
    result = RustleHealthLogAdapter(path).tail()

    assert result.channel_latency == {}


def test_health_log_diffs_consecutive_cumulative_snapshots(tmp_path: Path):
    path = tmp_path / "health_log.jsonl"
    lines = [
        _health_line(1000, endpoint="order_place", count=0, total=0.0, buckets=[(0.01, 0), (0.025, 0), (0.05, 0)]),
        # One new 0.02s sample landed in the 5s interval since the baseline.
        _health_line(6000, endpoint="order_place", count=1, total=0.02, buckets=[(0.01, 0), (0.025, 1), (0.05, 1)]),
    ]
    path.write_text("\n".join(lines) + "\n")

    result = RustleHealthLogAdapter(path).tail()

    [sample] = result.channel_latency["api:order_place"]
    assert sample.ts == 6.0
    assert sample.mean == 20.0  # 0.02s delta-sum / 1 delta-sample, in ms
    # p99/p999 interpolated within the (0.01, 0.025] bucket the one sample fell into.
    assert round(sample.p99, 3) == 24.85
    assert round(sample.p999, 3) == 24.985


def test_health_log_counter_reset_is_treated_as_a_new_baseline(tmp_path: Path):
    # A process restart resets the underlying Prometheus counters to zero -
    # the resulting negative delta must be dropped, not reported as latency.
    path = tmp_path / "health_log.jsonl"
    lines = [
        _health_line(1000, endpoint="order_place", count=5, total=0.1, buckets=[(0.01, 0), (0.025, 5), (0.05, 5)]),
        _health_line(2000, endpoint="order_place", count=1, total=0.02, buckets=[(0.01, 0), (0.025, 1), (0.05, 1)]),
        _health_line(7000, endpoint="order_place", count=2, total=0.04, buckets=[(0.01, 0), (0.025, 2), (0.05, 2)]),
    ]
    path.write_text("\n".join(lines) + "\n")

    result = RustleHealthLogAdapter(path).tail()

    [sample] = result.channel_latency["api:order_place"]
    assert sample.ts == 7.0
    assert sample.mean == 20.0  # 0.02s delta-sum / 1 delta-sample from the post-reset baseline
