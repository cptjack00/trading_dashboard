from __future__ import annotations

import json
from pathlib import Path

from signal_deck.live import LiveIngestionManager
from signal_deck.sources.base import LogSourceAdapter


def _write_manifest(run_dir: Path, **fields) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps(fields))


def _rustle_events(*lines: dict) -> str:
    return "\n".join(json.dumps(line) for line in lines) + "\n"


def test_poll_once_tails_live_run_and_pushes_to_subscribers(tmp_path: Path):
    root = tmp_path / "rustle-runs"
    run_dir = root / "run-1"
    _write_manifest(run_dir, run_id="run-1", run_type="live", state="live", started_at=1.0, ended_at=None)
    (run_dir / "events.jsonl").write_text(
        _rustle_events(
            {"type": "equity", "ts": 1, "equity": 100.0},
            {"type": "trade", "ts": 1, "symbol": "BTC", "side": "buy", "price": 10.0, "qty": 1.0},
        )
    )

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
    assert [t.symbol for t in overview.trades] == ["BTC"]

    with (run_dir / "events.jsonl").open("a") as f:
        f.write(json.dumps({"type": "equity", "ts": 2, "equity": 105.0}) + "\n")
    manager.poll_once()

    delta = queue.get_nowait()
    assert [p.equity for p in delta.equity] == [105.0]
    assert delta.trades == []


def test_completed_run_is_read_once_and_cached(tmp_path: Path):
    root = tmp_path / "rustle-runs"
    run_dir = root / "run-1"
    _write_manifest(run_dir, run_id="run-1", run_type="live", state="stopped", started_at=1.0, ended_at=5.0)
    (run_dir / "events.jsonl").write_text(_rustle_events({"type": "equity", "ts": 1, "equity": 42.0}))

    manager = LiveIngestionManager(root, None)

    first = manager.get_overview("rustle", "run-1")
    assert first.status == "stopped"
    assert [p.equity for p in first.equity] == [42.0]

    with (run_dir / "events.jsonl").open("a") as f:
        f.write(json.dumps({"type": "equity", "ts": 2, "equity": 999.0}) + "\n")

    second = manager.get_overview("rustle", "run-1")
    assert second is first  # cached - the appended line was never read


def test_encrypted_live_run_is_locked_not_parsed(tmp_path: Path):
    root = tmp_path / "rustle-runs"
    run_dir = root / "run-1"
    _write_manifest(run_dir, run_id="run-1", run_type="live", state="live", started_at=1.0, ended_at=None)
    (run_dir / "events.jsonl").write_bytes(
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
    (run_dir / "events.jsonl").write_bytes(LogSourceAdapter.MAGIC_HEADER + b"\ngarbage\n")

    manager = LiveIngestionManager(root, None)
    overview = manager.get_overview("rustle", "run-1")

    assert overview.status == "crashed"
    assert overview.encrypted_locked is True


def test_run_ending_drops_it_from_live_and_closes_subscribers(tmp_path: Path):
    root = tmp_path / "rustle-runs"
    run_dir = root / "run-1"
    manifest_path = run_dir / "run.json"
    _write_manifest(run_dir, run_id="run-1", run_type="live", state="live", started_at=1.0, ended_at=None)
    (run_dir / "events.jsonl").write_text(_rustle_events({"type": "equity", "ts": 1, "equity": 1.0}))

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
