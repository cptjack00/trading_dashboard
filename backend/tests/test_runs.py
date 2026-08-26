from __future__ import annotations

import json
from pathlib import Path

from signal_deck.runs import discover_all_runs, discover_rustle_runs, discover_ticktrader_runs
from signal_deck.sources.base import LogSourceAdapter


def _write_manifest(run_dir: Path, **fields) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps(fields))


def test_discover_rustle_runs_reads_manifest_and_totals_pnl(tmp_path: Path):
    root = tmp_path / "rustle-runs"
    run_dir = root / "run-1"
    _write_manifest(
        run_dir,
        run_id="run-1",
        run_type="live",
        state="live",
        started_at=1000.0,
        ended_at=None,
    )
    (run_dir / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"type": "pnl", "ts": 1, "slot": "s1", "realized": 10.0, "unrealized": 1.0}),
                json.dumps({"type": "pnl", "ts": 2, "slot": "s1", "realized": 12.0, "unrealized": 0.5}),
                json.dumps({"type": "pnl", "ts": 3, "slot": "s2", "realized": 5.0}),
            ]
        )
        + "\n"
    )

    [run] = discover_rustle_runs(root)

    assert run.run_id == "run-1"
    assert run.project == "rustle"
    assert run.status == "live"
    assert run.pnl == 12.5 + 5.0  # latest snapshot per slot, not summed deltas


def test_discover_rustle_runs_skips_dirs_without_manifest(tmp_path: Path):
    root = tmp_path / "rustle-runs"
    (root / "not-a-run").mkdir(parents=True)

    assert discover_rustle_runs(root) == []


def test_discover_rustle_runs_missing_root_returns_empty(tmp_path: Path):
    assert discover_rustle_runs(tmp_path / "does-not-exist") == []


def test_discover_rustle_runs_skips_malformed_manifest(tmp_path: Path):
    root = tmp_path / "rustle-runs"
    (root / "bad-json").mkdir(parents=True)
    (root / "bad-json" / "run.json").write_text("{not valid json")

    (root / "missing-fields").mkdir(parents=True)
    (root / "missing-fields" / "run.json").write_text(json.dumps({"run_id": "x"}))

    good_dir = root / "good"
    _write_manifest(
        good_dir, run_id="good", run_type="live", state="live", started_at=1.0, ended_at=None
    )

    assert [r.run_id for r in discover_rustle_runs(root)] == ["good"]


def test_discover_ticktrader_runs_reads_manifest_and_trade_log(tmp_path: Path):
    root = tmp_path / "tt-runs"
    run_dir = root / "run-2"
    _write_manifest(
        run_dir,
        run_id="run-2",
        run_type="backtest",
        state="stopped",
        started_at=2000.0,
        ended_at=2600.0,
        symbol="XYZ-PERP",
    )
    (run_dir / "trade_log.csv").write_text(
        "\n".join(
            [
                "timestamp,type,trade_price,trade_side,matched_volume,pnl,unrealized_pnl",
                "09:00:00.000,TICK,,,,0.0,0.0",
                "09:00:01.000,TRADE,100.1,BUY,5,2.0,0.0",
            ]
        )
        + "\n"
    )

    [run] = discover_ticktrader_runs(root)

    assert run.run_id == "run-2"
    assert run.project == "ticktrader"
    assert run.status == "backtest"
    assert run.pnl == 2.0


def test_backtest_status_wins_over_state():
    from signal_deck.runs import _status

    assert _status(run_type="backtest", state="live") == "backtest"
    assert _status(run_type="live", state="crashed") == "crashed"
    assert _status(run_type="live", state="live") == "live"


def test_discover_rustle_runs_with_encrypted_log_lists_run_with_zero_pnl(tmp_path: Path):
    root = tmp_path / "rustle-runs"
    run_dir = root / "run-1"
    _write_manifest(run_dir, run_id="run-1", run_type="live", state="live", started_at=1.0, ended_at=None)
    (run_dir / "events.jsonl").write_bytes(LogSourceAdapter.MAGIC_HEADER + b"\nnot-json-or-anything\n")

    [run] = discover_rustle_runs(root)  # must not raise despite unparseable ciphertext

    assert run.status == "live"
    assert run.pnl == 0.0


def test_discover_all_runs_combines_both_projects_and_ignores_unset_roots(tmp_path: Path):
    rustle_root = tmp_path / "rustle-runs"
    _write_manifest(
        rustle_root / "run-1",
        run_id="run-1",
        run_type="live",
        state="live",
        started_at=1.0,
        ended_at=None,
    )
    (rustle_root / "run-1" / "events.jsonl").write_text("")

    runs = discover_all_runs(rustle_root, None)

    assert [r.run_id for r in runs] == ["run-1"]
