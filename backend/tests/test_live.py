from __future__ import annotations

import json
from pathlib import Path

from signal_deck.live import LiveIngestionManager
from signal_deck.sources.base import LogSourceAdapter


def _write_manifest(run_dir: Path, **fields) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps(fields))


def _filled_row(slot: str, timestamp: str, pnl: float, *, side: str = "BUY", price: float = 10.0, qty: float = 1.0) -> dict:
    return {
        "slot_id": slot,
        "timestamp": timestamp,
        "type": "CONTROL",
        "best_bid": price - 0.1,
        "best_ask": price + 0.1,
        "spread": 0.2,
        "trade_price": price,
        "trade_side": side,
        "matched_volume": qty,
        "position": 1,
        "action": "FILLED",
        "pnl": pnl,
    }


def _rustle_trade_log(*rows: dict) -> str:
    return "\n".join(json.dumps(row) for row in rows) + "\n"


def test_poll_once_tails_live_run_and_pushes_to_subscribers(tmp_path: Path):
    root = tmp_path / "rustle-runs"
    run_dir = root / "run-1"
    _write_manifest(run_dir, run_id="run-1", run_type="live", state="live", started_at=1.0, ended_at=None)
    (run_dir / "trade_log.jsonl").write_text(_rustle_trade_log(_filled_row("s1", "09:00:00.000", 100.0)))

    manager = LiveIngestionManager(root, None)
    queue = manager.subscribe("rustle", "run-1")
    assert queue is None  # not live yet - nothing polled

    manager.poll_once()

    queue = manager.subscribe("rustle", "run-1")
    assert queue is not None

    overview = manager.get_overview("rustle", "run-1")
    assert overview.status == "live"
    assert overview.encrypted_locked is False
    assert [p.equity for p in overview.equity] == [100.0]
    assert [t.slot for t in overview.trades] == ["s1"]

    with (run_dir / "trade_log.jsonl").open("a") as f:
        f.write(json.dumps(_filled_row("s1", "09:00:01.000", 105.0)) + "\n")
    manager.poll_once()

    delta = queue.get_nowait()
    assert [p.equity for p in delta.equity] == [105.0]
    assert len(delta.trades) == 1


def test_completed_run_is_read_once_and_cached(tmp_path: Path):
    root = tmp_path / "rustle-runs"
    run_dir = root / "run-1"
    _write_manifest(run_dir, run_id="run-1", run_type="live", state="stopped", started_at=1.0, ended_at=5.0)
    (run_dir / "trade_log.jsonl").write_text(_rustle_trade_log(_filled_row("s1", "09:00:00.000", 42.0)))

    manager = LiveIngestionManager(root, None)

    first = manager.get_overview("rustle", "run-1")
    assert first.status == "stopped"
    assert [p.equity for p in first.equity] == [42.0]

    with (run_dir / "trade_log.jsonl").open("a") as f:
        f.write(json.dumps(_filled_row("s1", "09:00:01.000", 999.0)) + "\n")

    second = manager.get_overview("rustle", "run-1")
    assert second is first  # cached - the appended line was never read


def test_encrypted_live_run_is_locked_not_parsed(tmp_path: Path):
    root = tmp_path / "rustle-runs"
    run_dir = root / "run-1"
    _write_manifest(run_dir, run_id="run-1", run_type="live", state="live", started_at=1.0, ended_at=None)
    (run_dir / "trade_log.jsonl").write_bytes(
        LogSourceAdapter.MAGIC_HEADER + b"\n" + b"not-valid-json-or-anything-parseable\n"
    )

    manager = LiveIngestionManager(root, None)
    manager.poll_once()  # must not raise despite unparseable encrypted content

    overview = manager.get_overview("rustle", "run-1")
    assert overview.status == "live"
    assert overview.encrypted_locked is True
    assert overview.equity == []
    assert overview.trades == []
    assert manager.subscribe("rustle", "run-1") is None  # never polled in the background


def test_encrypted_completed_run_is_locked_on_demand(tmp_path: Path):
    root = tmp_path / "rustle-runs"
    run_dir = root / "run-1"
    _write_manifest(run_dir, run_id="run-1", run_type="live", state="crashed", started_at=1.0, ended_at=5.0)
    (run_dir / "trade_log.jsonl").write_bytes(LogSourceAdapter.MAGIC_HEADER + b"\ngarbage\n")

    manager = LiveIngestionManager(root, None)
    overview = manager.get_overview("rustle", "run-1")

    assert overview.status == "crashed"
    assert overview.encrypted_locked is True


def test_run_ending_drops_it_from_live_and_closes_subscribers(tmp_path: Path):
    root = tmp_path / "rustle-runs"
    run_dir = root / "run-1"
    manifest_path = run_dir / "run.json"
    _write_manifest(run_dir, run_id="run-1", run_type="live", state="live", started_at=1.0, ended_at=None)
    (run_dir / "trade_log.jsonl").write_text(_rustle_trade_log(_filled_row("s1", "09:00:00.000", 1.0)))

    manager = LiveIngestionManager(root, None)
    manager.poll_once()
    queue = manager.subscribe("rustle", "run-1")
    assert queue is not None

    manifest_path.write_text(
        json.dumps(
            {"run_id": "run-1", "run_type": "live", "state": "stopped", "started_at": 1.0, "ended_at": 9.0}
        )
    )
    manager.poll_once()

    assert queue.get_nowait() is None  # sentinel: stream should close
    assert manager.subscribe("rustle", "run-1") is None

    overview = manager.get_overview("rustle", "run-1")
    assert overview.status == "stopped"


def test_get_overview_unknown_run_returns_none(tmp_path: Path):
    manager = LiveIngestionManager(tmp_path / "rustle-runs", None)
    assert manager.get_overview("rustle", "nope") is None


def test_performance_and_market_data_flows_for_live_rustle_run(tmp_path: Path):
    root = tmp_path / "rustle-runs"
    run_dir = root / "run-1"
    _write_manifest(run_dir, run_id="run-1", run_type="live", state="live", started_at=1.0, ended_at=None)
    (run_dir / "trade_log.jsonl").write_text(
        _rustle_trade_log(_filled_row("s1", "09:00:00.000", 1.0, price=10.0, side="BUY"))
    )

    manager = LiveIngestionManager(root, None)
    manager.poll_once()

    overview = manager.get_overview("rustle", "run-1")
    [pnl] = overview.pnl
    assert (pnl.slot, pnl.realized, pnl.unrealized) == ("s1", 1.0, 0.0)
    [win_rate] = overview.win_rates
    assert (win_rate.wins, win_rate.losses) == (1, 0)  # 0 -> 1.0 is a win
    [fill] = overview.fills
    assert fill.count == 1
    prices = overview.symbol_prices["s1"]  # no config_path in this manifest - symbol falls back to slot_id
    assert len(prices) == 1 and prices[0].trade.side == "buy"

    # A second fill for the same slot accumulates the fill count rather than
    # replacing it, and pnl/win-rate move to the latest snapshot.
    with (run_dir / "trade_log.jsonl").open("a") as f:
        f.write(json.dumps(_filled_row("s1", "09:00:01.000", -2.0, price=9.0, side="SELL")) + "\n")
    manager.poll_once()

    overview = manager.get_overview("rustle", "run-1")
    [fill] = overview.fills
    assert fill.count == 2  # 1 + 1, summed
    [pnl] = overview.pnl
    assert pnl.realized == -2.0  # latest wins, not summed
    [win_rate] = overview.win_rates
    assert (win_rate.wins, win_rate.losses) == (1, 1)  # second fill's delta (1.0 -> -2.0) is a loss


def test_live_tracked_is_false_for_completed_runs(tmp_path: Path):
    root = tmp_path / "rustle-runs"
    run_dir = root / "run-1"
    _write_manifest(run_dir, run_id="run-1", run_type="live", state="stopped", started_at=1.0, ended_at=5.0)
    (run_dir / "trade_log.jsonl").write_text(_rustle_trade_log(_filled_row("s1", "09:00:00.000", 1.0)))

    manager = LiveIngestionManager(root, None)
    overview = manager.get_overview("rustle", "run-1")
    assert overview.live_tracked is False


def test_live_tracked_is_false_for_a_live_run_before_the_first_poll_tick(tmp_path: Path):
    root = tmp_path / "rustle-runs"
    run_dir = root / "run-1"
    _write_manifest(run_dir, run_id="run-1", run_type="live", state="live", started_at=1.0, ended_at=None)
    (run_dir / "trade_log.jsonl").write_text(_rustle_trade_log(_filled_row("s1", "09:00:00.000", 1.0)))

    manager = LiveIngestionManager(root, None)
    overview = manager.get_overview("rustle", "run-1")  # no poll_once() yet - fetched via the on-demand path
    assert overview.status == "live"
    assert overview.live_tracked is False  # not actually subscribable until a poll tick registers it


def test_encrypted_rustle_run_has_no_latency_data(tmp_path: Path):
    root = tmp_path / "rustle-runs"
    run_dir = root / "run-1"
    _write_manifest(run_dir, run_id="run-1", run_type="live", state="live", started_at=1.0, ended_at=None)
    (run_dir / "trade_log.jsonl").write_bytes(LogSourceAdapter.MAGIC_HEADER + b"\ngarbage\n")

    manager = LiveIngestionManager(root, None)
    manager.poll_once()

    overview = manager.get_overview("rustle", "run-1")
    assert overview.encrypted_locked is True
    assert overview.channel_latency == {}  # rustle has no separable latency stream


def test_ticktrader_encrypted_trade_log_still_streams_latency(tmp_path: Path):
    root = tmp_path / "tt-runs"
    run_dir = root / "run-2"
    _write_manifest(run_dir, run_id="run-2", run_type="live", state="live", started_at=1.0, ended_at=None)
    (run_dir / "trade_log.csv").write_bytes(LogSourceAdapter.MAGIC_HEADER + b"\ngarbage\n")
    (run_dir / "api_latency.jsonl").write_text(
        json.dumps({"ts": "2026-01-01T00:00:00+00:00", "duration_ms": 12.0}) + "\n"
    )

    manager = LiveIngestionManager(None, root)
    manager.poll_once()

    overview = manager.get_overview("ticktrader", "run-2")
    assert overview.encrypted_locked is True
    assert overview.trades == []
    assert overview.pnl == []
    assert overview.channel_latency["api"][0].mean == 12.0

    queue = manager.subscribe("ticktrader", "run-2")
    assert queue is not None  # still live-tracked despite the encrypted trade log

    with (run_dir / "api_latency.jsonl").open("a") as f:
        f.write(json.dumps({"ts": "2026-01-01T00:00:01+00:00", "duration_ms": 20.0}) + "\n")
    manager.poll_once()

    delta = queue.get_nowait()
    assert delta.channel_latency["api"][0].mean == 16.0  # running mean of 12, 20
    assert delta.trades == []


def test_ticktrader_completed_run_reads_latency_regardless_of_trade_log_encryption(tmp_path: Path):
    root = tmp_path / "tt-runs"
    run_dir = root / "run-3"
    _write_manifest(run_dir, run_id="run-3", run_type="live", state="stopped", started_at=1.0, ended_at=5.0)
    (run_dir / "trade_log.csv").write_bytes(LogSourceAdapter.MAGIC_HEADER + b"\ngarbage\n")
    (run_dir / "data_latency.jsonl").write_text(
        json.dumps({"ts": "2026-01-01T00:00:00+00:00", "duration_ms": 3.0}) + "\n"
    )

    manager = LiveIngestionManager(None, root)
    overview = manager.get_overview("ticktrader", "run-3")

    assert overview.encrypted_locked is True
    assert overview.channel_latency["data"][0].mean == 3.0


def test_ticktrader_live_run_uses_symbol_from_manifest(tmp_path: Path):
    root = tmp_path / "tt-runs"
    run_dir = root / "run-2"
    _write_manifest(
        run_dir, run_id="run-2", run_type="live", state="live", started_at=1.0, ended_at=None, symbol="XYZ-PERP"
    )
    (run_dir / "trade_log.csv").write_text(
        "\n".join(
            [
                "timestamp,type,trade_price,trade_side,matched_volume,pnl,unrealized_pnl",
                "09:00:01.000,TRADE,100.1,BUY,5,2.0,0.0",
            ]
        )
        + "\n"
    )

    manager = LiveIngestionManager(None, root)
    manager.poll_once()

    overview = manager.get_overview("ticktrader", "run-2")
    assert overview.status == "live"
    [trade] = overview.trades
    assert trade.symbol == "XYZ-PERP"
    assert trade.side == "buy"
